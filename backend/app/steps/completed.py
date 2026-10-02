"""
Completed sub-step: handle a Gnani job whose status is COMPLETED.

Called from step_transcribing when gnani_status == "COMPLETED".
Responsible for:
  1. GET /files — with exponential back-off on 429/retryable errors (cap 8).
  2. Validate file status and error_message.
  3. Fetch transcript_url immediately; treat transient errors as reschedule.
  4. Validate full_transcript is non-empty.
  5. finish_step → summarizing.
  6. Best-effort delete the audio object from storage (log only, never fail).

NEVER calls asyncio.sleep — all scheduling goes through finish_step(next_run_in_s=…).
"""

from __future__ import annotations

import logging
from typing import Any

from app.constants import ERR_CORRUPT_AUDIO, ERR_NO_SPEECH, ERR_PROVIDER_RATE_LIMITED
from app.services import storage as storage_svc
from app.services.gnani import (
    EmptyTranscriptError,
    GnaniAPIError,
    fetch_transcript,
    get_files,
    parse_duration_seconds,
)
from app.worker import fail_job, finish_step

logger = logging.getLogger(__name__)

# Maximum consecutive files-endpoint failures before giving up.
_MAX_FILES_ATTEMPTS = 8

# Retry delay for a transient transcript-URL fetch failure (seconds).
_TRANSCRIPT_FETCH_RETRY_S = 5


async def handle_completed(job: dict[str, Any], deps: Any) -> None:
    """Fetch /files, validate, fetch transcript, transition to summarizing.

    deps must expose:
      .session       — AsyncSession (caller manages commit/rollback)
      .gnani_client  — httpx.AsyncClient for Gnani / transcript URL calls

    Note: transcribing.py calls finish_step(gnani_status="COMPLETED") BEFORE
    calling us, so by the time we run, gnani_status is already persisted.
    We do NOT call finish_step here for the reschedule-for-429 case without
    first incrementing files_attempts; the DB helper handles next_run_at.
    """
    session = deps.session
    job_id = job["id"]
    gnani_job_id = job["gnani_job_id"]
    storage_path: str | None = job.get("storage_path")

    # Current consecutive /files attempt count (from the DB row).
    files_attempts: int = job.get("files_attempts") or 0

    # ------------------------------------------------------------------ #
    # Step 1: GET /files                                                   #
    # ------------------------------------------------------------------ #
    try:
        files_resp = await get_files(gnani_job_id, client=deps.gnani_client)
    except GnaniAPIError as exc:
        err = exc.error
        if err.retryable:
            # 429 or transient server error on /files endpoint.
            new_count = files_attempts + 1
            if new_count >= _MAX_FILES_ATTEMPTS:
                logger.warning(
                    "Job %s: /files failed %d times → PROVIDER_RATE_LIMITED",
                    job_id, new_count,
                )
                await fail_job(
                    session, job_id,
                    ERR_PROVIDER_RATE_LIMITED,
                    "The speech provider is rate-limiting us. Please retry in a few minutes.",
                    retryable=True,
                    # Keep gnani_job_id so a retry can poll /files again.
                )
                return

            # Compute back-off: 2^count, capped at 30 s.
            # Honour Retry-After from the response headers if the client
            # exposes it (the GnaniAPIError doesn't carry headers, but
            # deps.gnani_client is an httpx client and the response was
            # already consumed; Retry-After is documented as unverified
            # per §3 so we fall back to the exponential formula).
            delay_s = min(2 ** new_count, 30)

            logger.info(
                "Job %s: /files attempt %d retryable (%s), reschedule in %d s",
                job_id, new_count, err.code, delay_s,
            )
            await finish_step(
                session, job_id,
                fields={"files_attempts": new_count},
                next_run_in_s=delay_s,
            )
            return
        else:
            # Non-retryable /files error (e.g. auth): treat like file failure.
            logger.error(
                "Job %s: non-retryable /files error: %s", job_id, err.message,
            )
            await fail_job(
                session, job_id,
                ERR_CORRUPT_AUDIO,
                "The speech provider could not read the file.",
                retryable=False,
            )
            return

    # ------------------------------------------------------------------ #
    # Step 2: Validate file record                                         #
    # ------------------------------------------------------------------ #
    data = files_resp.get("data") or []
    if not data:
        # No file records at all.
        logger.warning("Job %s: /files returned empty data", job_id)
        await fail_job(
            session, job_id,
            ERR_CORRUPT_AUDIO,
            "The speech provider returned no file data.",
            retryable=False,
        )
        return

    file_record: dict = data[0]
    file_status: str = file_record.get("status", "")
    error_message: str | None = file_record.get("error_message")
    transcript_url: str | None = file_record.get("transcript_url")

    if file_status != "COMPLETED":
        # File failed at the provider level.
        logger.warning(
            "Job %s: file status=%r error_message=%r",
            job_id, file_status, error_message,
        )
        # Check if the error indicates silent / music-only audio.
        if _is_empty_transcript_error(error_message):
            await fail_job(
                session, job_id,
                ERR_NO_SPEECH,
                "No speech was detected in the audio.",
                retryable=False,
            )
        else:
            await fail_job(
                session, job_id,
                ERR_CORRUPT_AUDIO,
                "The speech provider could not read the file.",
                retryable=False,
            )
        return

    if not transcript_url:
        # COMPLETED status but no transcript URL — treat as corrupt.
        logger.warning("Job %s: file COMPLETED but no transcript_url", job_id)
        await fail_job(
            session, job_id,
            ERR_CORRUPT_AUDIO,
            "The speech provider did not return a transcript URL.",
            retryable=False,
        )
        return

    # Parse duration (string in Gnani API, per §3).
    _duration = parse_duration_seconds(file_record.get("duration_seconds"))

    # ------------------------------------------------------------------ #
    # Step 3: Fetch the transcript URL immediately                         #
    # ------------------------------------------------------------------ #
    try:
        full_transcript = await fetch_transcript(transcript_url, client=deps.gnani_client)
    except EmptyTranscriptError:
        # fetch_transcript raises this when full_transcript is empty/whitespace.
        logger.warning("Job %s: transcript URL returned empty transcript", job_id)
        await fail_job(
            session, job_id,
            ERR_NO_SPEECH,
            "No speech was detected in the audio.",
            retryable=False,
        )
        return
    except Exception as exc:
        # Transient network error fetching the transcript URL.
        # Count it the same as a /files failure (same cap).
        new_count = files_attempts + 1
        if new_count >= _MAX_FILES_ATTEMPTS:
            logger.warning(
                "Job %s: transcript fetch failed %d times → PROVIDER_RATE_LIMITED",
                job_id, new_count,
            )
            await fail_job(
                session, job_id,
                ERR_PROVIDER_RATE_LIMITED,
                "Repeated failures fetching the transcript. Please retry.",
                retryable=True,
            )
            return

        logger.info(
            "Job %s: transient transcript fetch error (%s), reschedule %d s",
            job_id, exc, _TRANSCRIPT_FETCH_RETRY_S,
        )
        await finish_step(
            session, job_id,
            fields={"files_attempts": new_count},
            next_run_in_s=_TRANSCRIPT_FETCH_RETRY_S,
        )
        return

    # ------------------------------------------------------------------ #
    # Step 4: full_transcript already validated non-empty by fetch_transcript,
    #          which raises EmptyTranscriptError on empty/whitespace.
    #          (Defensive double-check is handled inside fetch_transcript.)
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Step 5: Persist transcript and advance to summarizing               #
    # ------------------------------------------------------------------ #
    await finish_step(
        session, job_id,
        fields={
            "transcript": full_transcript,
            "status": "summarizing",
            "summary_status": "pending",
            "files_attempts": 0,
        },
        next_run_in_s=0,
    )
    logger.info("Job %s: transcript stored (%d chars), → summarizing", job_id, len(full_transcript))

    # ------------------------------------------------------------------ #
    # Step 6: Best-effort delete the audio object from storage            #
    # (after finish_step so a crash here doesn't break the job)           #
    # ------------------------------------------------------------------ #
    if storage_path:
        try:
            await storage_svc.delete_object(storage_path)
        except Exception as exc:
            # Log only — never fail the job over a storage cleanup error.
            logger.warning(
                "Job %s: best-effort storage delete failed (path redacted): %s",
                job_id, exc,
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_empty_transcript_error(error_message: str | None) -> bool:
    """Return True if the file-level error_message indicates silent audio.

    Gnani documents: 'Empty transcript after 3 retries'-style messages map
    to NO_SPEECH_DETECTED (Context.md §3 / §8).
    We do a case-insensitive substring check on a few known patterns.
    """
    if not error_message:
        return False
    lower = error_message.lower()
    return "empty transcript" in lower or "no speech" in lower
