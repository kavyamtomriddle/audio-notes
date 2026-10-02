"""
Gnani Batch STT async client (Context.md §3).

Single paced client: all Gnani calls go through one asyncio.Lock with ≥1 s
spacing between requests (shared API key, tight limits).  Every call has a
per-call timeout and errors pass through `normalize_gnani_error`.

Flow used by the worker:
  create_job → start_job → poll get_job → get_files → fetch_transcript
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import GNANI_API_KEY, GNANI_BASE_URL

logger = logging.getLogger(__name__)

# ---------- Pacing ----------

# Global lock + timestamp so all users share one paced pipeline.
_lock = asyncio.Lock()
_last_call_ts: float = 0.0  # monotonic time of last Gnani call
_MIN_SPACING_S = 1.0         # ≥1 s between calls

# Default per-call timeout (seconds).
_CALL_TIMEOUT = 30


# ---------- Error normalisation (§3) ----------

@dataclass
class GnaniError:
    """Normalised error from any Gnani response shape."""
    code: str
    message: str
    http_status: int
    retryable: bool


# HTTP codes that mean "try again later"
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def normalize_gnani_error(resp: httpx.Response) -> GnaniError:
    """Parse any of the 4 Gnani error shapes into a uniform GnaniError.

    Shapes (Context.md §3):
      1. Plain text body, e.g. "Internal Server Error" (500).
      2. {"detail":{"error_code":"RATE_LIMITED","message":"…","status_code":429}}
      3. {"error":"CODE","message":"…"}
      4. {"success":false,"error":{"type":"…","message":"…"}}
    Rules: 429/500/502/503/504/timeouts → retryable.
           401/403 → PROVIDER_AUTH non-retryable.
           Unknown shape → fall back to status code + truncated text.
    """
    status = resp.status_code
    retryable = status in _RETRYABLE_STATUSES

    # 401/403 → PROVIDER_AUTH
    if status in (401, 403):
        msg = "Gnani authentication failed (bad key or out of credits)"
        try:
            body = resp.json()
            if isinstance(body, dict):
                msg = (
                    body.get("message")
                    or _deep_message(body)
                    or msg
                )
        except Exception:
            pass
        return GnaniError(
            code="PROVIDER_AUTH", message=msg,
            http_status=status, retryable=False,
        )

    # Try to parse JSON
    try:
        body = resp.json()
    except Exception:
        # Shape 1: plain text
        text = resp.text[:200] if resp.text else f"HTTP {status}"
        return GnaniError(
            code=f"PROVIDER_ERROR",
            message=text,
            http_status=status,
            retryable=retryable,
        )

    if not isinstance(body, dict):
        return GnaniError(
            code="PROVIDER_ERROR",
            message=str(body)[:200],
            http_status=status,
            retryable=retryable,
        )

    # Shape 2: {"detail":{"error_code":"…","message":"…","status_code":…}}
    detail = body.get("detail")
    if isinstance(detail, dict) and "error_code" in detail:
        return GnaniError(
            code=detail.get("error_code", "PROVIDER_ERROR"),
            message=detail.get("message", "Unknown error"),
            http_status=status,
            retryable=retryable,
        )

    # Shape 3: {"error":"CODE","message":"…"}
    if "error" in body and isinstance(body["error"], str):
        return GnaniError(
            code=body["error"],
            message=body.get("message", "Unknown error"),
            http_status=status,
            retryable=retryable,
        )

    # Shape 4: {"success":false,"error":{"type":"…","message":"…"}}
    if body.get("success") is False and isinstance(body.get("error"), dict):
        err = body["error"]
        return GnaniError(
            code=err.get("type", "PROVIDER_ERROR"),
            message=err.get("message", "Unknown error"),
            http_status=status,
            retryable=retryable,
        )

    # Fallback: unknown shape
    return GnaniError(
        code="PROVIDER_ERROR",
        message=str(body)[:200],
        http_status=status,
        retryable=retryable,
    )


def _deep_message(body: dict) -> str | None:
    """Try to extract a human-readable message from nested structures."""
    if "message" in body:
        return str(body["message"])
    detail = body.get("detail")
    if isinstance(detail, dict) and "message" in detail:
        return str(detail["message"])
    if isinstance(detail, str):
        return detail
    return None


# ---------- Internal helpers ----------

def _api_headers() -> dict[str, str]:
    """Auth header for all Gnani calls."""
    if not GNANI_API_KEY:
        raise RuntimeError("GNANI_API_KEY not configured")
    return {"X-API-Key-ID": GNANI_API_KEY}


def _base() -> str:
    return GNANI_BASE_URL.rstrip("/")


async def _paced_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json: dict | None = None,
    timeout: float = _CALL_TIMEOUT,
    follow_redirects: bool = False,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Send a request with global pacing (lock + ≥1 s spacing).

    Raises httpx.TimeoutException on timeout (the worker maps this to
    a retryable error).
    """
    global _last_call_ts

    merged_headers = {**_api_headers(), **(headers or {})}

    async with _lock:
        # Enforce ≥1 s spacing since last call
        now = time.monotonic()
        wait = _MIN_SPACING_S - (now - _last_call_ts)
        if wait > 0:
            await asyncio.sleep(wait)

        resp = await client.request(
            method, url,
            json=json,
            headers=merged_headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )
        _last_call_ts = time.monotonic()
        return resp


# ---------- Public API ----------

async def create_job(
    download_url: str,
    language_code: str,
    *,
    client: httpx.AsyncClient,
) -> dict:
    """POST /stt/v3/batch/jobs — create a batch transcription job.

    Returns the parsed response dict on success (has `job_id`, `status`).
    Raises on error after normalisation.
    """
    url = f"{_base()}/stt/v3/batch/jobs"
    payload = {
        "config": {
            "model": "gnani-prisma-v2.5",
            "language_code": language_code,
            "mode": "transcribe",
        },
        "source": {
            "type": "cloud_storage",
            "auth": {"mode": "public"},
            "paths": [download_url],
        },
    }
    resp = await _paced_request(client, "POST", url, json=payload)
    if not (200 <= resp.status_code < 300):
        err = normalize_gnani_error(resp)
        raise GnaniAPIError(err)
    return resp.json()


async def start_job(
    job_id: str,
    *,
    client: httpx.AsyncClient,
) -> dict:
    """POST /stt/v3/batch/jobs/{job_id}/start — start transcription.

    Returns parsed response on success.  409 is treated as "already started"
    and does NOT raise.
    """
    url = f"{_base()}/stt/v3/batch/jobs/{job_id}/start"
    resp = await _paced_request(client, "POST", url)
    if resp.status_code == 409:
        # Could mean already running OR already failed (terminal).
        # Return a distinct status so the caller knows to call get_job to decide.
        logger.info("Gnani job %s returned 409 (conflict), must call get_job", job_id)
        return {"status": "CONFLICT", "message": "Job already started or failed. Call get_job."}
    if not (200 <= resp.status_code < 300):
        err = normalize_gnani_error(resp)
        raise GnaniAPIError(err)
    return resp.json()


async def get_job(
    job_id: str,
    *,
    client: httpx.AsyncClient,
) -> dict:
    """GET /stt/v3/batch/jobs/{job_id} — poll job status.

    Returns parsed response dict with `status`, `progress`, etc.
    """
    url = f"{_base()}/stt/v3/batch/jobs/{job_id}"
    resp = await _paced_request(client, "GET", url)
    if not (200 <= resp.status_code < 300):
        err = normalize_gnani_error(resp)
        raise GnaniAPIError(err)
    return resp.json()


async def get_files(
    job_id: str,
    *,
    client: httpx.AsyncClient,
) -> dict:
    """GET /stt/v3/batch/jobs/{job_id}/files — get file results.

    Returns parsed response dict with `data` array.
    May raise GnaniAPIError on 429 or other errors (caller handles retry).
    """
    url = f"{_base()}/stt/v3/batch/jobs/{job_id}/files"
    resp = await _paced_request(client, "GET", url)
    if not (200 <= resp.status_code < 300):
        err = normalize_gnani_error(resp)
        raise GnaniAPIError(err)
    return resp.json()


async def fetch_transcript(
    transcript_url: str,
    *,
    client: httpx.AsyncClient,
) -> str:
    """GET the transcript JSON from the temporary URL returned by /files.

    No API key needed; follow redirects.  Returns the `full_transcript` string.
    Raises on empty/whitespace transcript (NO_SPEECH_DETECTED).
    """
    # This is NOT a Gnani-keyed call, so bypass pacing — but still use timeout.
    resp = await client.get(
        transcript_url,
        follow_redirects=True,
        timeout=_CALL_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    transcript = data.get("full_transcript", "")
    if not isinstance(transcript, str):
        transcript = str(transcript) if transcript else ""
    transcript = transcript.strip()

    if not transcript:
        raise EmptyTranscriptError("Empty transcript (no speech detected)")

    return transcript


def parse_duration_seconds(raw_value) -> float | None:
    """Defensively parse `duration_seconds` which Gnani returns as a STRING.

    Returns float or None if unparseable.
    """
    if raw_value is None:
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        logger.warning("Could not parse duration_seconds: %r", raw_value)
        return None


# ---------- Exceptions ----------

class GnaniAPIError(Exception):
    """Wraps a normalised GnaniError for callers to inspect."""

    def __init__(self, error: GnaniError):
        self.error = error
        super().__init__(f"[{error.code}] {error.message}")


class EmptyTranscriptError(Exception):
    """Raised when the transcript is empty/whitespace → NO_SPEECH_DETECTED."""
    pass
