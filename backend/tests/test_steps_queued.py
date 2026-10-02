"""
Phase 2b-ii-a tests: step_queued + deps.

All tests use mocks — no real network, DB, or .env.
Patch module-level names in app.steps.queued exactly like
test_steps_transcribing.py patches app.steps.transcribing.
"""

from __future__ import annotations

import importlib
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.gnani import GnaniAPIError, GnaniError


# ---------------------------------------------------------------------------
# Helpers: build a fake job dict and fake deps
# ---------------------------------------------------------------------------

def _make_job(
    *,
    gnani_job_id: str | None = None,
    gnani_started: bool = False,
    storage_path: str = "sess/job/audio.mp3",
    language_code: str = "en-IN",
) -> dict:
    """Minimal job dict as the worker passes to step_queued."""
    return {
        "id": uuid.uuid4(),
        "gnani_job_id": gnani_job_id,
        "gnani_started": gnani_started,
        "storage_path": storage_path,
        "language_code": language_code,
    }


def _make_deps() -> SimpleNamespace:
    return SimpleNamespace(
        session=AsyncMock(),
        gnani_client=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_saves_job_id_and_does_not_start():
    """No gnani_job_id → create signed URL + create_job; finish_step with
    gnani_job_id set; start_job must NOT be called."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.create_signed_download_url",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch(
            "app.steps.queued.create_job",
            new_callable=AsyncMock,
        ) as mock_cj,
        patch(
            "app.steps.queued.start_job",
            new_callable=AsyncMock,
        ) as mock_sj,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
    ):
        mock_dl.return_value = "https://signed-url.example.com/audio?token=t"
        mock_cj.return_value = {"job_id": "gnani-123", "status": "CREATED"}

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        # create_job called with the download URL and language
        mock_cj.assert_awaited_once()
        assert mock_cj.call_args[0][0] == "https://signed-url.example.com/audio?token=t"
        assert mock_cj.call_args[0][1] == "en-IN"

        # start_job NOT called
        mock_sj.assert_not_awaited()

        # finish_step called with gnani_job_id and next_run_in_s=0
        mock_fs.assert_awaited_once()
        assert mock_fs.call_args[1]["fields"]["gnani_job_id"] == "gnani-123"
        assert mock_fs.call_args[1]["next_run_in_s"] == 0


@pytest.mark.asyncio
async def test_existing_job_id_never_calls_create():
    """When gnani_job_id is already set, create_job must NOT be called."""
    job = _make_job(gnani_job_id="gnani-existing")
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.create_signed_download_url",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch(
            "app.steps.queued.create_job",
            new_callable=AsyncMock,
        ) as mock_cj,
        patch(
            "app.steps.queued.start_job",
            new_callable=AsyncMock,
        ) as mock_sj,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
    ):
        mock_sj.return_value = {"status": "STARTING"}

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        mock_dl.assert_not_awaited()
        mock_cj.assert_not_awaited()
        # start_job SHOULD be called (gnani_started is False)
        mock_sj.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_success_moves_to_transcribing():
    """Start succeeds (2xx) → finish_step with status=transcribing,
    gnani_started=True, processing_started_at set, next_run_in_s=10."""
    job = _make_job(gnani_job_id="gnani-abc")
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.start_job",
            new_callable=AsyncMock,
        ) as mock_sj,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
    ):
        mock_sj.return_value = {"status": "STARTING", "job_id": "gnani-abc"}

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        mock_fs.assert_awaited_once()
        fields = mock_fs.call_args[1]["fields"]
        assert fields["gnani_started"] is True
        assert fields["status"] == "transcribing"
        assert "processing_started_at" in fields
        assert mock_fs.call_args[1]["next_run_in_s"] == 10


@pytest.mark.asyncio
async def test_start_conflict_running_treated_as_started():
    """409 on start_job + get_job shows IN_PROGRESS → treat as started."""
    job = _make_job(gnani_job_id="gnani-abc")
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.start_job",
            new_callable=AsyncMock,
        ) as mock_sj,
        patch(
            "app.steps.queued.get_job",
            new_callable=AsyncMock,
        ) as mock_gj,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
    ):
        mock_sj.return_value = {"status": "CONFLICT"}
        mock_gj.return_value = {"status": "IN_PROGRESS"}

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        mock_fs.assert_awaited_once()
        fields = mock_fs.call_args[1]["fields"]
        assert fields["gnani_started"] is True
        assert fields["status"] == "transcribing"
        assert mock_fs.call_args[1]["next_run_in_s"] == 10


@pytest.mark.asyncio
async def test_start_conflict_failed_calls_fail_job_with_clear_gnani():
    """409 on start + get_job shows FAILED → fail_job with clear_gnani."""
    job = _make_job(gnani_job_id="gnani-abc")
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.start_job",
            new_callable=AsyncMock,
        ) as mock_sj,
        patch(
            "app.steps.queued.get_job",
            new_callable=AsyncMock,
        ) as mock_gj,
        patch(
            "app.steps.queued.fail_job",
            new_callable=AsyncMock,
        ) as mock_fj,
    ):
        mock_sj.return_value = {"status": "CONFLICT"}
        mock_gj.return_value = {"status": "FAILED"}

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        mock_fj.assert_awaited_once()
        args, kwargs = mock_fj.call_args
        assert args[2] == "PROVIDER_ERROR"
        assert kwargs["retryable"] is True
        assert kwargs["clear_gnani"] is True


@pytest.mark.asyncio
async def test_started_flag_already_true_just_advances():
    """gnani_job_id present + gnani_started=True (stale queued) → just
    finish_step to transcribing, next_run_in_s=0."""
    job = _make_job(gnani_job_id="gnani-abc", gnani_started=True)
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.start_job",
            new_callable=AsyncMock,
        ) as mock_sj,
        patch(
            "app.steps.queued.create_job",
            new_callable=AsyncMock,
        ) as mock_cj,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
    ):
        from app.steps.queued import step_queued
        await step_queued(job, deps)

        mock_sj.assert_not_awaited()
        mock_cj.assert_not_awaited()
        mock_fs.assert_awaited_once()
        assert mock_fs.call_args[1]["fields"]["status"] == "transcribing"
        assert mock_fs.call_args[1]["next_run_in_s"] == 0


@pytest.mark.asyncio
async def test_retryable_gnani_error_reschedules_30s():
    """Retryable GnaniAPIError during start → finish_step 30 s."""
    job = _make_job(gnani_job_id="gnani-abc")
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.start_job",
            new_callable=AsyncMock,
        ) as mock_sj,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
        patch(
            "app.steps.queued.fail_job",
            new_callable=AsyncMock,
        ) as mock_fj,
    ):
        mock_sj.side_effect = GnaniAPIError(
            GnaniError("PROVIDER_ERROR", "Internal Server Error", 500, True)
        )

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        mock_fs.assert_awaited_once()
        assert mock_fs.call_args[1]["next_run_in_s"] == 30
        mock_fj.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_error_fails_non_retryable():
    """401 GnaniAPIError → fail_job PROVIDER_AUTH, retryable=False."""
    job = _make_job(gnani_job_id="gnani-abc")
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.start_job",
            new_callable=AsyncMock,
        ) as mock_sj,
        patch(
            "app.steps.queued.fail_job",
            new_callable=AsyncMock,
        ) as mock_fj,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
    ):
        mock_sj.side_effect = GnaniAPIError(
            GnaniError("PROVIDER_AUTH", "Bad API key", 401, False)
        )

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        mock_fj.assert_awaited_once()
        args, kwargs = mock_fj.call_args
        assert args[2] == "PROVIDER_AUTH"
        assert kwargs["retryable"] is False
        mock_fs.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_non_retryable_error_fails():
    """Non-retryable GnaniAPIError (not auth) → fail_job PROVIDER_ERROR,
    retryable=False."""
    job = _make_job(gnani_job_id="gnani-abc")
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.start_job",
            new_callable=AsyncMock,
        ) as mock_sj,
        patch(
            "app.steps.queued.fail_job",
            new_callable=AsyncMock,
        ) as mock_fj,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
    ):
        mock_sj.side_effect = GnaniAPIError(
            GnaniError("PROVIDER_ERROR", "Bad request", 400, False)
        )

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        mock_fj.assert_awaited_once()
        args, kwargs = mock_fj.call_args
        assert args[2] == "PROVIDER_ERROR"
        assert kwargs["retryable"] is False
        mock_fs.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_signed_url_error_reschedules_30s():
    """Storage error getting signed download URL → finish_step 30 s."""
    job = _make_job()  # no gnani_job_id → will try to get signed URL
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.create_signed_download_url",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
        patch(
            "app.steps.queued.fail_job",
            new_callable=AsyncMock,
        ) as mock_fj,
    ):
        mock_dl.side_effect = RuntimeError("Storage unreachable")

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        mock_fs.assert_awaited_once()
        assert mock_fs.call_args[1]["next_run_in_s"] == 30
        mock_fj.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_signed_url_in_fail_messages_or_logs(caplog):
    """No signed URL or token appears in fail_job messages or logs."""
    signed_url = (
        "https://example.supabase.co/storage/v1/object/sign/bucket/audio.mp3"
        "?token=eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SIG"
    )
    job = _make_job()  # no gnani_job_id → will try to get signed URL
    deps = _make_deps()

    with (
        patch(
            "app.steps.queued.create_signed_download_url",
            new_callable=AsyncMock,
        ) as mock_dl,
        patch(
            "app.steps.queued.create_job",
            new_callable=AsyncMock,
        ) as mock_cj,
        patch(
            "app.steps.queued.finish_step",
            new_callable=AsyncMock,
        ) as mock_fs,
        patch(
            "app.steps.queued.fail_job",
            new_callable=AsyncMock,
        ) as mock_fj,
        caplog.at_level(logging.DEBUG, logger="app.steps.queued"),
    ):
        mock_dl.return_value = signed_url
        mock_cj.side_effect = GnaniAPIError(
            GnaniError("PROVIDER_ERROR", f"fetch failed: {signed_url}", 500, True)
        )

        from app.steps.queued import step_queued
        await step_queued(job, deps)

        # Check logs don't contain the signed URL or token
        for record in caplog.records:
            assert "supabase.co" not in record.message, (
                f"Signed URL domain leaked to log: {record.message}"
            )
            assert "eyJhbGciOiJIUzI1NiJ9" not in record.message, (
                f"Token leaked to log: {record.message}"
            )

        # Check fail_job message (if called) doesn't have URL
        if mock_fj.called:
            msg_arg = mock_fj.call_args[0][3]  # message positional
            assert "supabase.co" not in msg_arg
            assert "token=" not in msg_arg


@pytest.mark.asyncio
async def test_dispatch_routes_queued_to_step_queued():
    """dispatch.run_step with status='queued' and a patched step_queued
    proves the lazy import resolves and step_queued is awaited."""
    job = {"id": uuid.uuid4(), "status": "queued"}
    deps = _make_deps()

    mock_sq = AsyncMock()
    fake_module = SimpleNamespace(step_queued=mock_sq)

    with patch.dict("sys.modules", {"app.steps.queued": fake_module}):
        import app.steps.dispatch as dispatch_mod
        importlib.reload(dispatch_mod)
        await dispatch_mod.run_step(job, deps)

    mock_sq.assert_awaited_once_with(job, deps)


@pytest.mark.asyncio
async def test_transactional_session_commits_on_clean_exit_and_rolls_back_on_error():
    """transactional_session commits on clean exit, rolls back on exception.

    Uses monkeypatched SQLite sessionmaker — never touches Postgres.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import text

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create a simple test table
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT)"))

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Patch get_session_factory in app.steps.deps to return our factory
    with patch("app.steps.deps.get_session_factory", return_value=factory):
        from app.steps.deps import transactional_session

        # Clean exit → committed
        async with transactional_session() as session:
            await session.execute(
                text("INSERT INTO kv (k, v) VALUES (:k, :v)"),
                {"k": "hello", "v": "world"},
            )

        # Verify committed
        async with factory() as verify_session:
            result = await verify_session.execute(
                text("SELECT v FROM kv WHERE k = :k"),
                {"k": "hello"},
            )
            assert result.scalar_one() == "world"

        # Exception → rolled back
        try:
            async with transactional_session() as session:
                await session.execute(
                    text("INSERT INTO kv (k, v) VALUES (:k, :v)"),
                    {"k": "fail_key", "v": "fail_val"},
                )
                raise ValueError("deliberate error")
        except ValueError:
            pass

        # Verify rolled back
        async with factory() as verify_session:
            result = await verify_session.execute(
                text("SELECT v FROM kv WHERE k = :k"),
                {"k": "fail_key"},
            )
            assert result.scalar_one_or_none() is None

    await engine.dispose()
