"""
Phase 2b-ii-c tests: handle_completed.

All tests use mocks — no real network, DB, or .env.
asyncio.sleep is patched to raise AssertionError so any accidental sleep call
fails the test immediately (the spec forbids sleeping in the worker).
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.constants import ERR_CORRUPT_AUDIO, ERR_NO_SPEECH, ERR_PROVIDER_RATE_LIMITED
from app.services.gnani import EmptyTranscriptError, GnaniAPIError, GnaniError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(
    *,
    gnani_job_id: str = "gnani-xyz",
    files_attempts: int = 0,
    storage_path: str = "sessions/abc/uploads/test.mp3",
) -> dict:
    """Minimal job dict as the worker would pass to handle_completed."""
    return {
        "id": uuid.uuid4(),
        "gnani_job_id": gnani_job_id,
        "files_attempts": files_attempts,
        "storage_path": storage_path,
    }


def _make_deps() -> SimpleNamespace:
    return SimpleNamespace(
        session=AsyncMock(),
        gnani_client=AsyncMock(),
    )


def _make_files_resp(
    *,
    status: str = "COMPLETED",
    transcript_url: str = "https://example.com/transcript.json",
    error_message: str | None = None,
    duration_seconds: str = "92.29",
) -> dict:
    return {
        "data": [
            {
                "file_id": "file-001",
                "status": status,
                "duration_seconds": duration_seconds,
                "error_message": error_message,
                "transcript_url": transcript_url,
            }
        ]
    }


def _429_error() -> GnaniAPIError:
    return GnaniAPIError(GnaniError("RATE_LIMITED", "Rate limit exceeded", 429, True))


def _500_error() -> GnaniAPIError:
    return GnaniAPIError(GnaniError("PROVIDER_ERROR", "Internal Server Error", 500, True))


# ---------------------------------------------------------------------------
# Fixture: block any accidental asyncio.sleep in the module under test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Patch asyncio.sleep in completed.py to fail if called."""
    async def _forbidden_sleep(seconds):
        raise AssertionError(
            f"handle_completed must not call asyncio.sleep (got sleep({seconds!r}))"
        )
    monkeypatch.setattr(asyncio, "sleep", _forbidden_sleep)


# ---------------------------------------------------------------------------
# Tests: 429 → reschedule with exponential back-off
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_429_first_attempt_reschedules():
    """429 on first /files call → increment to 1, reschedule 2^1=2 s, no sleep."""
    job = _make_job(files_attempts=0)
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gf.side_effect = _429_error()
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fj.assert_not_awaited()
    mock_fs.assert_awaited_once()
    call = mock_fs.call_args
    assert call[1]["fields"]["files_attempts"] == 1
    assert call[1]["next_run_in_s"] == 2   # min(2**1, 30)


@pytest.mark.asyncio
async def test_429_third_attempt_reschedules_with_cap():
    """429 on attempt 3 → delay = min(2^4, 30) = 16 s."""
    job = _make_job(files_attempts=3)
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gf.side_effect = _429_error()
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fj.assert_not_awaited()
    call = mock_fs.call_args
    assert call[1]["fields"]["files_attempts"] == 4
    assert call[1]["next_run_in_s"] == 16   # min(2**4, 30)


@pytest.mark.asyncio
async def test_429_large_count_capped_at_30s():
    """429 on attempt 5 → delay = min(2^6, 30) = 30 s (capped)."""
    job = _make_job(files_attempts=5)
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gf.side_effect = _429_error()
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fj.assert_not_awaited()
    call = mock_fs.call_args
    assert call[1]["next_run_in_s"] == 30   # min(2**6=64, 30) = 30


# ---------------------------------------------------------------------------
# Test: 8th failure → PROVIDER_RATE_LIMITED (fail_job, keep gnani_job_id)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eighth_failure_fails_job_provider_rate_limited():
    """files_attempts=7 + one more 429 = 8 total → PROVIDER_RATE_LIMITED fail."""
    job = _make_job(files_attempts=7)
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gf.side_effect = _429_error()
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    # Must fail, not reschedule
    mock_fs.assert_not_awaited()
    mock_fj.assert_awaited_once()
    args, kwargs = mock_fj.call_args
    # args: (session, job_id, error_code, message, retryable, ...)
    assert args[2] == ERR_PROVIDER_RATE_LIMITED
    assert kwargs["retryable"] is True
    # clear_gnani must NOT be True — we keep gnani_job_id
    assert kwargs.get("clear_gnani", False) is False


# ---------------------------------------------------------------------------
# Tests: file-level failures
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_transcript_error_message_maps_to_no_speech():
    """File status != COMPLETED with 'empty transcript' error → NO_SPEECH_DETECTED."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gf.return_value = _make_files_resp(
            status="FAILED",
            transcript_url=None,
            error_message="Empty transcript after 3 retries",
        )
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fs.assert_not_awaited()
    mock_fj.assert_awaited_once()
    args, kwargs = mock_fj.call_args
    assert args[2] == ERR_NO_SPEECH
    assert kwargs["retryable"] is False


@pytest.mark.asyncio
async def test_file_level_failure_other_error_maps_to_corrupt():
    """File status != COMPLETED without 'empty transcript' → CORRUPT_OR_UNREADABLE_AUDIO."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gf.return_value = _make_files_resp(
            status="SKIPPED",
            transcript_url=None,
            error_message="Could not decode audio stream",
        )
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fs.assert_not_awaited()
    mock_fj.assert_awaited_once()
    args, kwargs = mock_fj.call_args
    assert args[2] == ERR_CORRUPT_AUDIO
    assert kwargs["retryable"] is False


@pytest.mark.asyncio
async def test_no_data_fails_corrupt():
    """Empty data array → CORRUPT_OR_UNREADABLE_AUDIO."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gf.return_value = {"data": []}
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fj.assert_awaited_once()
    args, _ = mock_fj.call_args
    assert args[2] == ERR_CORRUPT_AUDIO


# ---------------------------------------------------------------------------
# Tests: empty / whitespace transcript
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_full_transcript_fails_no_speech():
    """fetch_transcript raises EmptyTranscriptError → NO_SPEECH_DETECTED."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fetch_transcript", new_callable=AsyncMock) as mock_ft,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gf.return_value = _make_files_resp()
        mock_ft.side_effect = EmptyTranscriptError("Empty transcript (no speech detected)")
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fs.assert_not_awaited()
    mock_fj.assert_awaited_once()
    args, kwargs = mock_fj.call_args
    assert args[2] == ERR_NO_SPEECH
    assert kwargs["retryable"] is False


@pytest.mark.asyncio
async def test_whitespace_only_transcript_fails_no_speech():
    """fetch_transcript called with whitespace-only transcript → EmptyTranscriptError.

    The gnani.fetch_transcript function strips and raises EmptyTranscriptError
    when full_transcript is empty/whitespace — we verify handle_completed
    maps that to NO_SPEECH_DETECTED.
    """
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fetch_transcript", new_callable=AsyncMock) as mock_ft,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gf.return_value = _make_files_resp()
        # fetch_transcript raises EmptyTranscriptError for whitespace too
        mock_ft.side_effect = EmptyTranscriptError("Empty transcript (no speech detected)")
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fj.assert_awaited_once()
    args, kwargs = mock_fj.call_args
    assert args[2] == ERR_NO_SPEECH
    assert kwargs["retryable"] is False


# ---------------------------------------------------------------------------
# Test: success path — transcript stored, status → summarizing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_success_stores_transcript():
    """Happy path: finish_step with transcript and status=summarizing."""
    job = _make_job()
    deps = _make_deps()
    expected_text = "so today i want to talk about the product roadmap"

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fetch_transcript", new_callable=AsyncMock) as mock_ft,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
        patch("app.steps.completed.storage_svc.delete_object", new_callable=AsyncMock),
    ):
        mock_gf.return_value = _make_files_resp()
        mock_ft.return_value = expected_text
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fj.assert_not_awaited()
    mock_fs.assert_awaited_once()
    call = mock_fs.call_args
    assert call[1]["fields"]["transcript"] == expected_text
    assert call[1]["fields"]["status"] == "summarizing"
    assert call[1]["fields"]["summary_status"] == "pending"
    assert call[1]["fields"]["files_attempts"] == 0
    assert call[1]["next_run_in_s"] == 0


# ---------------------------------------------------------------------------
# Test: delete failure does NOT fail the job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_failure_does_not_fail_job():
    """If storage delete raises, the job still succeeds (log only)."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fetch_transcript", new_callable=AsyncMock) as mock_ft,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
        patch(
            "app.steps.completed.storage_svc.delete_object",
            new_callable=AsyncMock,
        ) as mock_del,
    ):
        mock_gf.return_value = _make_files_resp()
        mock_ft.return_value = "hello world transcript"
        mock_del.side_effect = RuntimeError("Network error")
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    # finish_step was called (job succeeded)
    mock_fs.assert_awaited_once()
    # fail_job was NOT called
    mock_fj.assert_not_awaited()
    # delete was attempted
    mock_del.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: delete is called AFTER finish_step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_called_after_finish_step():
    """Storage delete must happen after finish_step, not before."""
    call_order = []
    job = _make_job()
    deps = _make_deps()

    async def _fs(*args, **kwargs):
        call_order.append("finish_step")

    async def _del(*args, **kwargs):
        call_order.append("delete_object")
        return True

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fetch_transcript", new_callable=AsyncMock) as mock_ft,
        patch("app.steps.completed.finish_step", side_effect=_fs) as mock_fs,
        patch("app.steps.completed.storage_svc.delete_object", side_effect=_del) as mock_del,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gf.return_value = _make_files_resp()
        mock_ft.return_value = "hello transcript"
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    assert call_order == ["finish_step", "delete_object"], (
        f"Expected finish_step before delete_object, got: {call_order}"
    )
    mock_fj.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test: duration_seconds as string is parsed defensively
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duration_seconds_string_parsed_defensively():
    """duration_seconds='92.29' (string) must not crash handle_completed."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fetch_transcript", new_callable=AsyncMock) as mock_ft,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
        patch("app.steps.completed.storage_svc.delete_object", new_callable=AsyncMock),
    ):
        # duration_seconds is a STRING per Gnani API (§3 verified fact)
        mock_gf.return_value = _make_files_resp(duration_seconds="92.29")
        mock_ft.return_value = "hello world"
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    # Should succeed without error
    mock_fj.assert_not_awaited()
    mock_fs.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: transient transcript fetch → reschedule 5 s (counts in files_attempts)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transient_transcript_fetch_reschedules():
    """Transient error fetching transcript_url → reschedule 5 s, count attempt."""
    job = _make_job(files_attempts=0)
    deps = _make_deps()

    with (
        patch("app.steps.completed.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.completed.fetch_transcript", new_callable=AsyncMock) as mock_ft,
        patch("app.steps.completed.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.completed.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gf.return_value = _make_files_resp()
        mock_ft.side_effect = ConnectionError("timeout")
        from app.steps.completed import handle_completed
        await handle_completed(job, deps)

    mock_fj.assert_not_awaited()
    mock_fs.assert_awaited_once()
    call = mock_fs.call_args
    assert call[1]["fields"]["files_attempts"] == 1
    assert call[1]["next_run_in_s"] == 5
