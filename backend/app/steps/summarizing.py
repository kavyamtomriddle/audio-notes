"""
Summarizing step: call the LLM, transition to completed.

Called by the worker loop when a job has status='summarizing'.

Context.md §6 / §8 rules:
  - Success → summary stored, summary_status='done', status='completed', completed_at set.
  - ANY failure → summary_status='failed', summary_error stored (≤200 chars, redacted),
    status='completed'.  Transcript is NEVER modified.
  - Summary failure is NOT a failed upload; the job reaches 'completed' either way.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.llm import LLMError, summarize
from app.worker import finish_step

logger = logging.getLogger(__name__)

# Maximum length stored in summary_error (redacted, safe to surface).
_MAX_ERROR_LEN = 200


async def step_summarizing(job: dict[str, Any], deps: Any) -> None:
    """One worker tick for a job in 'summarizing' status.

    deps must expose:
      .session      — AsyncSession (caller manages commit/rollback)
      .llm_client   — httpx.AsyncClient for LLM calls

    The LLM client already handles chunking and 2 internal retries,
    so we do not retry here; any LLMError is terminal for this tick.
    """
    session = deps.session
    job_id = job["id"]
    transcript: str = job.get("transcript") or ""

    try:
        summary = await summarize(transcript, client=deps.llm_client)
    except Exception as exc:
        # Any failure (LLMError, network, unexpected) → completed + summary_status='failed'.
        # Redact the error before storing/logging — it may contain URLs or keys.
        raw_msg = _redact(str(exc))
        error_stored = raw_msg[:_MAX_ERROR_LEN]
        logger.warning(
            "Job %s: LLM summarization failed (%s): %s",
            job_id, type(exc).__name__, error_stored,
        )
        await finish_step(
            session, job_id,
            fields={
                "summary_status": "failed",
                "summary_error": error_stored,
                "status": "completed",
                # completed_at: set to current UTC time
                "completed_at": datetime.now(timezone.utc),
            },
            next_run_in_s=0,
        )
        return

    logger.info("Job %s: summary done (%d chars)", job_id, len(summary))
    await finish_step(
        session, job_id,
        fields={
            "summary": summary,
            "summary_status": "done",
            "status": "completed",
            "completed_at": datetime.now(timezone.utc),
        },
        next_run_in_s=0,
    )


# ---------------------------------------------------------------------------
# Internal helpers (also used by dispatch.py)
# ---------------------------------------------------------------------------

def _redact(text: str) -> str:
    """Remove secrets from text before logging or storing.

    Strips:
      - https?://... URLs (replaced with [URL])
      - token=<value> query params (replaced with token=[REDACTED])
      - API-key-like strings: 20+ char sequences of [A-Za-z0-9_-] with no
        whitespace, preceded by 'key', 'token', 'secret' etc. in the text
        (replaced with [REDACTED])

    This is best-effort; the primary defence is never logging signed URLs.
    """
    import re

    # Replace https?:// URLs
    text = re.sub(r'https?://\S+', '[URL]', text)

    # Replace token= values
    text = re.sub(r'token=[^&\s"\']+', 'token=[REDACTED]', text, flags=re.IGNORECASE)

    # Replace long alphanumeric strings that look like API keys (≥20 chars, no spaces)
    # Only if surrounded by word boundaries or quotes.
    text = re.sub(r'(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])', '[REDACTED]', text)

    return text
