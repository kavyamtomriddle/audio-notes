"""
Phase 2b-ii-d tests: run_step (dispatcher) + redact().

All tests use mocks — no real network, DB, or .env.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(*, status: str = "transcribing") -> dict:
    return {"id": uuid.uuid4(), "status": status}


def _make_deps() -> SimpleNamespace:
    return SimpleNamespace(
        session=AsyncMock(),
        gnani_client=AsyncMock(),
        llm_client=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Tests: routing to correct step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatches_transcribing_to_step_transcribing():
    """status='transcribing' → step_transcribing called once."""
    job = _make_job(status="transcribing")
    deps = _make_deps()

    with patch("app.steps.dispatch.step_transcribing", new_callable=AsyncMock) as mock_st:
        from app.steps.dispatch import run_step
        await run_step(job, deps)

    mock_st.assert_awaited_once_with(job, deps)


@pytest.mark.asyncio
async def test_dispatches_summarizing_to_step_summarizing():
    """status='summarizing' → step_summarizing called once."""
    job = _make_job(status="summarizing")
    deps = _make_deps()

    with patch("app.steps.dispatch.step_summarizing", new_callable=AsyncMock) as mock_ss:
        from app.steps.dispatch import run_step
        await run_step(job, deps)

    mock_ss.assert_awaited_once_with(job, deps)


@pytest.mark.asyncio
async def test_dispatches_queued_to_step_queued():
    """status='queued' → step_queued called once (patched via lazy import path)."""
    job = _make_job(status="queued")
    deps = _make_deps()

    # Patch the lazy import so it returns our mock
    mock_step_queued = AsyncMock()
    fake_queued_module = SimpleNamespace(step_queued=mock_step_queued)

    with patch.dict("sys.modules", {"app.steps.queued": fake_queued_module}):
        from app.steps import dispatch as dispatch_mod
        # Reload to pick up the patched sys.modules in this test
        import importlib
        importlib.reload(dispatch_mod)
        await dispatch_mod.run_step(job, deps)

    mock_step_queued.assert_awaited_once_with(job, deps)


# ---------------------------------------------------------------------------
# Tests: unknown status → reschedule 60 s
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_status_reschedules_60s():
    """Unknown status → finish_step called with next_run_in_s=60."""
    job = _make_job(status="alien_status")
    deps = _make_deps()

    with patch("app.steps.dispatch.finish_step", new_callable=AsyncMock) as mock_fs:
        from app.steps.dispatch import run_step
        await run_step(job, deps)

    mock_fs.assert_awaited_once()
    assert mock_fs.call_args[1]["next_run_in_s"] == 60


# ---------------------------------------------------------------------------
# Tests: unexpected exception → swallowed, rescheduled 30 s
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unexpected_exception_in_transcribing_swallowed():
    """Crash in step_transcribing → finish_step 30 s, nothing raised to caller."""
    job = _make_job(status="transcribing")
    deps = _make_deps()

    with (
        patch("app.steps.dispatch.step_transcribing", new_callable=AsyncMock) as mock_st,
        patch("app.steps.dispatch.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_st.side_effect = RuntimeError("DB connection lost")
        from app.steps.dispatch import run_step
        # Must NOT raise
        await run_step(job, deps)

    mock_fs.assert_awaited_once()
    assert mock_fs.call_args[1]["next_run_in_s"] == 30


@pytest.mark.asyncio
async def test_unexpected_exception_in_summarizing_swallowed():
    """Crash in step_summarizing → finish_step 30 s, nothing raised to caller."""
    job = _make_job(status="summarizing")
    deps = _make_deps()

    with (
        patch("app.steps.dispatch.step_summarizing", new_callable=AsyncMock) as mock_ss,
        patch("app.steps.dispatch.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_ss.side_effect = ValueError("unexpected crash")
        from app.steps.dispatch import run_step
        await run_step(job, deps)

    mock_fs.assert_awaited_once()
    assert mock_fs.call_args[1]["next_run_in_s"] == 30


# ---------------------------------------------------------------------------
# Tests: caplog must NOT contain signed URLs or tokens
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_caplog_no_signed_url_on_exception(caplog):
    """When an exception containing a signed URL is logged, caplog must not show it."""
    signed_url = (
        "https://example.supabase.co/storage/v1/object/sign/bucket/audio.mp3"
        "?token=eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SIG"
    )
    job = _make_job(status="transcribing")
    deps = _make_deps()

    with (
        patch("app.steps.dispatch.step_transcribing", new_callable=AsyncMock) as mock_st,
        patch("app.steps.dispatch.finish_step", new_callable=AsyncMock),
        caplog.at_level(logging.ERROR, logger="app.steps.dispatch"),
    ):
        mock_st.side_effect = RuntimeError(f"Failed to connect: {signed_url}")
        from app.steps.dispatch import run_step
        await run_step(job, deps)

    # The signed URL hostname must not appear in any log message
    for record in caplog.records:
        assert "supabase.co" not in record.message, (
            f"Signed URL domain leaked to log: {record.message}"
        )
        assert "eyJhbGciOiJIUzI1NiJ9" not in record.message, (
            f"Token leaked to log: {record.message}"
        )


@pytest.mark.asyncio
async def test_caplog_no_token_on_exception(caplog):
    """token= value in exception message must not appear in logs."""
    job = _make_job(status="transcribing")
    deps = _make_deps()

    with (
        patch("app.steps.dispatch.step_transcribing", new_callable=AsyncMock) as mock_st,
        patch("app.steps.dispatch.finish_step", new_callable=AsyncMock),
        caplog.at_level(logging.ERROR, logger="app.steps.dispatch"),
    ):
        mock_st.side_effect = RuntimeError("Auth failed: token=supersecretvalue123")
        from app.steps.dispatch import run_step
        await run_step(job, deps)

    for record in caplog.records:
        assert "supersecretvalue123" not in record.message


@pytest.mark.asyncio
async def test_caplog_warning_no_signed_url_on_exception(caplog):
    """A non-storage exception reaches dispatch and logs at WARNING or higher; no signed URL or token."""
    signed_url = (
        "https://example.supabase.co/storage/v1/object/sign/bucket/audio.mp3"
        "?token=eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SIG"
    )
    job = _make_job(status="transcribing")
    deps = _make_deps()

    with (
        patch("app.steps.dispatch.step_transcribing", new_callable=AsyncMock) as mock_st,
        patch("app.steps.dispatch.finish_step", new_callable=AsyncMock),
        caplog.at_level(logging.WARNING, logger="app.steps.dispatch"),
    ):
        # ValueError is a non-storage exception
        mock_st.side_effect = ValueError(f"Crash: {signed_url}")
        from app.steps.dispatch import run_step
        await run_step(job, deps)

    assert len(caplog.records) > 0
    for record in caplog.records:
        assert record.levelno >= logging.WARNING
        assert "supabase.co" not in record.message
        assert "eyJhbGciOiJIUzI1NiJ9" not in record.message


# ---------------------------------------------------------------------------
# Tests: redact() re-export from dispatch
# ---------------------------------------------------------------------------

def test_redact_exported_from_dispatch():
    """dispatch.redact must be importable and functional."""
    from app.steps.dispatch import redact
    result = redact("error at https://api.example.com/v1?token=abc123XYZ")
    assert "https://" not in result
    assert "abc123XYZ" not in result
