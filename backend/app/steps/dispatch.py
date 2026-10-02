"""
Dispatch: route a claimed job to the correct step function.

This is the single entry point the worker loop calls after claiming a job.
It:
  1. Dispatches by job status to the appropriate step function.
  2. Swallows and logs any unexpected exception from queued/transcribing steps,
     rescheduling 30 s so the worker loop continues.
  3. Logs a redacted message — never signed URLs, tokens, or keys.

Unknown status: log and reschedule 60 s (defensive; should not happen if the
DB CHECK constraint is enforced).
"""

from __future__ import annotations

import logging
from typing import Any

from app.steps.summarizing import _redact, step_summarizing
from app.steps.transcribing import step_transcribing
from app.worker import finish_step

logger = logging.getLogger(__name__)


# Re-export redact so callers can import from dispatch instead of summarizing.
redact = _redact


async def run_step(job: dict[str, Any], deps: Any) -> None:
    """Dispatch a claimed job to the right step function.

    deps must expose .session (AsyncSession) plus any service clients the
    individual steps need (.gnani_client, .llm_client).

    Contract:
      - Never raises to the caller.
      - Every code path calls finish_step or fail_job so the lease is released
        and next_run_at is scheduled.
    """
    status: str = job.get("status", "")
    job_id = job["id"]

    if status == "queued":
        # Import lazily so this module is importable even before queued.py exists.
        try:
            from app.steps.queued import step_queued  # noqa: PLC0415
            step_fn = step_queued
        except ImportError:
            logger.error("app.steps.queued not yet implemented — rescheduling 60 s")
            await finish_step(deps.session, job_id, fields={}, next_run_in_s=60)
            return
        try:
            await step_fn(job, deps)
        except Exception as exc:
            _log_unexpected(job_id, status, exc)
            await finish_step(deps.session, job_id, fields={}, next_run_in_s=30)

    elif status == "transcribing":
        try:
            await step_transcribing(job, deps)
        except Exception as exc:
            _log_unexpected(job_id, status, exc)
            await finish_step(deps.session, job_id, fields={}, next_run_in_s=30)

    elif status == "summarizing":
        # step_summarizing already catches all exceptions internally and
        # transitions the job to completed; we still wrap it defensively.
        try:
            await step_summarizing(job, deps)
        except Exception as exc:
            _log_unexpected(job_id, status, exc)
            await finish_step(deps.session, job_id, fields={}, next_run_in_s=30)

    else:
        # Unknown status — defensive fallback; should never happen in production.
        logger.error(
            "Job %s has unknown status %r — rescheduling 60 s", job_id, status,
        )
        await finish_step(deps.session, job_id, fields={}, next_run_in_s=60)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _log_unexpected(job_id: Any, status: str, exc: Exception) -> None:
    """Log an unexpected exception with type and redacted message (no secrets)."""
    safe_msg = _redact(str(exc))
    logger.error(
        "Job %s (status=%r): unexpected %s — rescheduling 30 s: %s",
        job_id, status, type(exc).__name__, safe_msg,
    )
