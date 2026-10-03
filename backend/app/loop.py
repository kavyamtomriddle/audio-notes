"""
Worker loop — runs as an asyncio task inside the FastAPI process.

LoopState, _idle, run_worker, shutdown_worker, build_default_worker.

Each build_default_worker closure wraps its work in transactional_session()
so the commit boundary is explicit and isolated per call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.steps.dispatch import redact

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LoopState — shared between run_worker and shutdown_worker
# ---------------------------------------------------------------------------

@dataclass
class LoopState:
    """Mutable state visible to both the loop and shutdown logic."""
    current_job_id: Any = None


# ---------------------------------------------------------------------------
# _idle — interruptible sleep
# ---------------------------------------------------------------------------

async def _idle(stop_event: asyncio.Event, seconds: float) -> None:
    """Wait up to *seconds*, returning early if *stop_event* is set."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


# ---------------------------------------------------------------------------
# run_worker — the main claim→step→idle loop
# ---------------------------------------------------------------------------

async def run_worker(
    stop_event: asyncio.Event,
    *,
    claim,
    run_step,
    sweep,
    state: LoopState,
    idle=_idle,
    clock=time.monotonic,
    sweep_every_s: float = 300.0,
    idle_s: float = 2.0,
    error_backoff_s: float = 5.0,
) -> None:
    """Run the worker loop until *stop_event* is set.

    On each iteration:
      1. Sweep if enough time has passed.
      2. Claim a job; if nothing, idle and continue.
      3. Run one step; clear current_job_id on completion.

    On Exception: log redacted message, clear current_job_id, back off.
    Never catches BaseException / CancelledError so that cancellation
    keeps current_job_id set for shutdown_worker to release.
    """
    last_sweep = clock() - sweep_every_s  # ensure first sweep runs immediately

    while not stop_event.is_set():
        try:
            # 1. Periodic sweep
            if clock() - last_sweep >= sweep_every_s:
                last_sweep = clock()
                await sweep()

            # 2. Claim
            job = await claim()
            if job is None:
                await idle(stop_event, idle_s)
                continue

            # 3. Run step
            state.current_job_id = job["id"]
            await run_step(job)
            state.current_job_id = None

        except Exception as exc:
            safe_msg = redact(str(exc))
            logger.error("Worker loop error: %s — backing off %.1f s",
                         safe_msg, error_backoff_s)
            state.current_job_id = None
            await idle(stop_event, error_backoff_s)


# ---------------------------------------------------------------------------
# shutdown_worker — graceful stop with lease release
# ---------------------------------------------------------------------------

async def shutdown_worker(
    task: asyncio.Task,
    stop_event: asyncio.Event,
    state: LoopState,
    release,
    *,
    timeout_s: float = 10.0,
) -> None:
    """Gracefully stop the worker task.

    1. Signal stop.
    2. Wait up to *timeout_s* for the task to finish.
    3. On timeout: cancel and await (suppress CancelledError).
    4. Finally: if a job was in-flight, release its lease (best-effort).
    """
    stop_event.set()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        pass
    finally:
        if state.current_job_id is not None:
            try:
                await asyncio.shield(release(state.current_job_id))
            except Exception:
                logger.error("Failed to release lease for job %s (swallowed)",
                             state.current_job_id)


# ---------------------------------------------------------------------------
# build_default_worker — production closures that own commits
# ---------------------------------------------------------------------------

def build_default_worker() -> dict:
    """Return {"claim", "run_step", "sweep", "release", "client"} closures.

    Each closure opens its own transactional_session so every DB operation
    has an explicit commit boundary.

    One shared httpx.AsyncClient is created (default timeout=60 s; services
    pass their own per-call timeouts).  It is returned under key "client"
    so the lifespan can close it after the worker stops.
    """
    import httpx

    from app.steps.deps import make_deps, transactional_session
    from app.steps import dispatch
    from app.services import storage as storage_svc
    from app import sweeper as sweeper_mod
    from app.worker import claim_job, release_lease

    # Shared HTTP client for Gnani AND LLM calls (both accept per-call timeouts).
    client = httpx.AsyncClient(timeout=60)

    async def _claim():
        async with transactional_session() as s:
            return await claim_job(s)

    async def _run_step(job):
        async with transactional_session() as s:
            deps = make_deps(
                s,
                gnani_client=client,
                llm_client=client,
            )
            await dispatch.run_step(job, deps)

    async def _sweep():
        async with transactional_session() as s:
            results = await sweeper_mod.sweep(s)
        # AFTER the commit (transactional_session exited), best-effort deletes
        for r in results:
            if r.action in ("abandoned", "timed_out"):
                try:
                    await storage_svc.delete_object(r.storage_path)
                except Exception:
                    logger.warning(
                        "Best-effort storage delete failed for job %s (swallowed)",
                        r.job_id,
                    )

    async def _release(job_id):
        async with transactional_session() as s:
            await release_lease(s, job_id)

    return {
        "claim": _claim,
        "run_step": _run_step,
        "sweep": _sweep,
        "release": _release,
        "client": client,
    }
