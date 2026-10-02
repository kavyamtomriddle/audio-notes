"""
Phase 2b-ii-b tests: step_transcribing.

All tests use mocks — no real network, DB, or .env.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.gnani import GnaniAPIError, GnaniError


# ---------------------------------------------------------------------------
# Helpers: build a fake job dict and fake deps
# ---------------------------------------------------------------------------

def _make_job(
    *,
    gnani_job_id: str = "gnani-abc",
    processing_started_at: datetime | None = None,
) -> dict:
    """Return a minimal job dict as the worker would pass to step_transcribing."""
    if processing_started_at is None:
        processing_started_at = datetime.now(timezone.utc)
    return {
        "id": uuid.uuid4(),
        "gnani_job_id": gnani_job_id,
        "processing_started_at": processing_started_at,
    }


def _make_deps(
    *,
    get_job_resp: dict | None = None,
    get_job_exc: Exception | None = None,
    get_files_resp: dict | None = None,
    get_files_exc: Exception | None = None,
) -> SimpleNamespace:
    """Return a fake deps object with a mock session and gnani_client."""
    return SimpleNamespace(
        session=AsyncMock(),
        gnani_client=AsyncMock(),
        _get_job_resp=get_job_resp,
        _get_job_exc=get_job_exc,
        _get_files_resp=get_files_resp,
        _get_files_exc=get_files_exc,
    )


# ---------------------------------------------------------------------------
# Fixtures: read the START_FAILED fixture from docs/fixtures (read-only)
# ---------------------------------------------------------------------------

@pytest.fixture()
def start_failed_fixture() -> dict:
    import pathlib
    path = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "docs" / "fixtures" / "gnani_get_job_start_failed.json"
    )
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Patch helpers — we patch gnani.get_job and gnani.get_files at module level
# in transcribing.py so they use our canned responses.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    """Ensure TRANSCRIBE_TIMEOUT_S is predictable in tests."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "TRANSCRIBE_TIMEOUT_S", 3600)
    # Also patch it where transcribing.py imported it
    import app.steps.transcribing as mod
    monkeypatch.setattr(mod, "TRANSCRIBE_TIMEOUT_S", 3600)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_terminal_in_progress():
    """IN_PROGRESS → reschedule 10 s, persist gnani_status."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gj.return_value = {"status": "IN_PROGRESS"}
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fs.assert_awaited_once()
        call_kwargs = mock_fs.call_args
        assert call_kwargs[1]["fields"]["gnani_status"] == "IN_PROGRESS"
        assert call_kwargs[1]["next_run_in_s"] == 10


@pytest.mark.asyncio
async def test_non_terminal_starting():
    """STARTING → reschedule 10 s."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gj.return_value = {"status": "STARTING"}
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fs.assert_awaited_once()
        assert mock_fs.call_args[1]["fields"]["gnani_status"] == "STARTING"
        assert mock_fs.call_args[1]["next_run_in_s"] == 10


@pytest.mark.asyncio
async def test_non_terminal_queued():
    """QUEUED → reschedule 10 s."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gj.return_value = {"status": "QUEUED"}
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fs.assert_awaited_once()
        assert mock_fs.call_args[1]["fields"]["gnani_status"] == "QUEUED"
        assert mock_fs.call_args[1]["next_run_in_s"] == 10


@pytest.mark.asyncio
async def test_non_terminal_created():
    """CREATED → reschedule 10 s."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gj.return_value = {"status": "CREATED"}
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fs.assert_awaited_once()
        assert mock_fs.call_args[1]["fields"]["gnani_status"] == "CREATED"
        assert mock_fs.call_args[1]["next_run_in_s"] == 10


@pytest.mark.asyncio
async def test_timeout():
    """processing_started_at older than TRANSCRIBE_TIMEOUT_S → PROVIDER_TIMEOUT."""
    job = _make_job(
        processing_started_at=datetime.now(timezone.utc) - timedelta(seconds=3700),
    )
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.fail_job", new_callable=AsyncMock) as mock_fj,
        patch("app.steps.transcribing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gj.return_value = {"status": "IN_PROGRESS"}
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fj.assert_awaited_once()
        args = mock_fj.call_args
        assert args[0][2] == "PROVIDER_TIMEOUT"  # error_code
        assert args[1]["retryable"] is True
        # finish_step should NOT have been called
        mock_fs.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_calls_handle_completed():
    """COMPLETED → persist gnani_status then call handle_completed."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.transcribing.handle_completed", new_callable=AsyncMock) as mock_hc,
    ):
        mock_gj.return_value = {"status": "COMPLETED"}
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        # gnani_status persisted
        mock_fs.assert_awaited_once()
        assert mock_fs.call_args[1]["fields"]["gnani_status"] == "COMPLETED"

        # handle_completed called with same job and deps
        mock_hc.assert_awaited_once_with(job, deps)


@pytest.mark.asyncio
async def test_start_failed_from_fixture(start_failed_fixture):
    """START_FAILED fixture → failed + clear_gnani."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.transcribing.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gj.return_value = start_failed_fixture
        # get_files returns empty data for START_FAILED (no files processed)
        mock_gf.return_value = {"data": []}
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fj.assert_awaited_once()
        args, kwargs = mock_fj.call_args
        assert kwargs["retryable"] is True
        assert kwargs["clear_gnani"] is True
        assert args[2] == "PROVIDER_ERROR"  # error_code


@pytest.mark.asyncio
async def test_failed_status():
    """FAILED → best-effort get_files then fail_job with clear_gnani."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.transcribing.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gj.return_value = {"status": "FAILED"}
        mock_gf.return_value = {
            "data": [{"error_message": "Could not decode audio"}]
        }
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fj.assert_awaited_once()
        _, kwargs = mock_fj.call_args
        assert kwargs["retryable"] is True
        assert kwargs["clear_gnani"] is True


@pytest.mark.asyncio
async def test_partial_failure():
    """PARTIAL_FAILURE → fail_job with clear_gnani."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.transcribing.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gj.return_value = {"status": "PARTIAL_FAILURE"}
        mock_gf.return_value = {"data": []}
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fj.assert_awaited_once()
        _, kwargs = mock_fj.call_args
        assert kwargs["retryable"] is True
        assert kwargs["clear_gnani"] is True


@pytest.mark.asyncio
async def test_cancelled():
    """CANCELLED → fail_job with clear_gnani."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.transcribing.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gj.return_value = {"status": "CANCELLED"}
        mock_gf.return_value = {"data": []}
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fj.assert_awaited_once()
        _, kwargs = mock_fj.call_args
        assert kwargs["retryable"] is True
        assert kwargs["clear_gnani"] is True


@pytest.mark.asyncio
async def test_get_files_failure_during_error_lookup():
    """get_files exception during FAILED lookup does NOT break the fail path."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.get_files", new_callable=AsyncMock) as mock_gf,
        patch("app.steps.transcribing.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gj.return_value = {"status": "FAILED"}
        # get_files raises — should be swallowed
        mock_gf.side_effect = GnaniAPIError(
            GnaniError("RATE_LIMITED", "Too many requests", 429, True)
        )
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        # fail_job must still be called
        mock_fj.assert_awaited_once()
        _, kwargs = mock_fj.call_args
        assert kwargs["retryable"] is True
        assert kwargs["clear_gnani"] is True


@pytest.mark.asyncio
async def test_transient_error_reschedule_30s():
    """Transient Gnani error (e.g. 500) → reschedule 30 s."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.finish_step", new_callable=AsyncMock) as mock_fs,
        patch("app.steps.transcribing.fail_job", new_callable=AsyncMock) as mock_fj,
    ):
        mock_gj.side_effect = GnaniAPIError(
            GnaniError("PROVIDER_ERROR", "Internal Server Error", 500, True)
        )
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        # Should reschedule, not fail
        mock_fs.assert_awaited_once()
        assert mock_fs.call_args[1]["next_run_in_s"] == 30
        mock_fj.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_auth_error_non_retryable():
    """PROVIDER_AUTH error → non-retryable fail_job."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.transcribing.get_job", new_callable=AsyncMock) as mock_gj,
        patch("app.steps.transcribing.fail_job", new_callable=AsyncMock) as mock_fj,
        patch("app.steps.transcribing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_gj.side_effect = GnaniAPIError(
            GnaniError("PROVIDER_AUTH", "Bad API key", 401, False)
        )
        from app.steps.transcribing import step_transcribing
        await step_transcribing(job, deps)

        mock_fj.assert_awaited_once()
        args = mock_fj.call_args
        assert args[0][2] == "PROVIDER_AUTH"
        assert args[1]["retryable"] is False
        mock_fs.assert_not_awaited()
