"""
Integration tests for the worker DB helpers (claim_job, finish_step, fail_job, release_lease).

These tests run against a REAL Postgres database (the one in .env).
They are excluded by default via pytest addopts = -m "not integration".
Run with:  pytest -m integration tests/integration/test_claim.py -v --tb=long

ISOLATION: We never touch public.uploads. Instead, each test module creates a
throwaway schema (itest_<random>) with a table that mirrors public.uploads
(LIKE public.uploads INCLUDING ALL), and drops the schema CASCADE on teardown.
Between tests the table is TRUNCATEd rather than rebuilt.

The search_path is set via a connect event listener so every connection the
engine opens automatically resolves "uploads" to the throwaway schema.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

# Worker functions under test
from app.worker import claim_job, finish_step, fail_job, release_lease

# All tests in this file share one event loop (module-scoped) and are integration tests
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_url() -> str:
    """Build the asyncpg connection URL from DATABASE_URL in env."""
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        pytest.skip("DATABASE_URL not set — cannot run integration tests")
    url = raw
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def _validate_schema_name(name: str) -> str:
    """Safety check: only allow itest_<lowercase-hex> schema names."""
    if not re.fullmatch(r"itest_[a-z0-9]+", name):
        raise ValueError(f"Refusing unsafe schema name: {name!r}")
    return name


# ---------------------------------------------------------------------------
# Fixtures (module-scoped engine + schema, per-test truncation)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def schema_name():
    """Generate a unique schema name for this test module."""
    return f"itest_{uuid.uuid4().hex[:12]}"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(schema_name):
    """
    Create a NullPool async engine with search_path wired to the throwaway
    schema on every connection, so all unqualified 'uploads' references
    resolve there. statement_cache_size=0 for Supabase session-pooler compat.
    """
    safe_schema = _validate_schema_name(schema_name)
    eng = create_async_engine(
        _db_url(),
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0},
    )

    # Set search_path on every raw connection the engine opens
    @event.listens_for(eng.sync_engine, "connect")
    def _set_search_path(dbapi_conn, connection_record):
        # Use the raw dbapi (asyncpg) cursor to run SET synchronously
        # at connection init, before any SA transaction begins.
        # asyncpg's Connection doesn't have a cursor(); we must go via
        # a psycopg-style execute. But asyncpg connections exposed by
        # SA are proxied — use the underlying connection's server_settings
        # or issue SET via SA event. For asyncpg we need to run it via
        # SA's "first_connect" or "connect" + text(). But those are sync.
        # Instead, we'll do it per-session below.
        pass

    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def setup_schema(engine, schema_name):
    """
    Create the throwaway schema + uploads table (mirroring public.uploads).
    Dropped CASCADE on teardown via a fresh AUTOCOMMIT connection.
    """
    safe_schema = _validate_schema_name(schema_name)

    # Create schema
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA {safe_schema}"))
        await conn.execute(text(
            f"CREATE TABLE {safe_schema}.uploads "
            f"(LIKE public.uploads INCLUDING ALL)"
        ))

    yield safe_schema

    # Teardown: always drop, even if tests errored. Use AUTOCOMMIT so we
    # don't need a transaction and can't mask the original exception.
    try:
        drop_engine = create_async_engine(
            _db_url(),
            poolclass=NullPool,
            connect_args={"statement_cache_size": 0},
            isolation_level="AUTOCOMMIT",
        )
        try:
            async with drop_engine.connect() as conn:
                await conn.execute(
                    text(f"DROP SCHEMA IF EXISTS {safe_schema} CASCADE")
                )
        finally:
            await drop_engine.dispose()
    except Exception:
        # Never mask the original fixture exception with a cleanup error
        pass


@pytest_asyncio.fixture(loop_scope="module")
async def session(engine, setup_schema, schema_name):
    """
    Yield a session per test. The search_path is set BEFORE any autobegin
    transaction by using conn.begin() first, then SET inside it.

    After the test, we ROLLBACK (undo test changes) then TRUNCATE the table
    on a fresh connection to guarantee a clean slate.
    """
    safe_schema = _validate_schema_name(schema_name)

    conn = await engine.connect()
    # Start an explicit transaction FIRST, then SET inside it.
    # This avoids the autobegin + begin() conflict.
    trans = await conn.begin()
    await conn.execute(text(f"SET search_path TO {safe_schema}, public"))
    sess = AsyncSession(bind=conn, expire_on_commit=False)

    yield sess

    # Cleanup: close session, rollback transaction, close connection
    await sess.close()
    try:
        await trans.rollback()
    except Exception:
        pass  # Transaction may already be aborted
    await conn.close()

    # TRUNCATE on a fresh connection to ensure clean state for next test
    async with engine.connect() as trunc_conn:
        await trunc_conn.execute(text(f"SET search_path TO {safe_schema}, public"))
        await trunc_conn.execute(text("TRUNCATE uploads"))
        await trunc_conn.commit()


# ---------------------------------------------------------------------------
# Insert / read helpers
# ---------------------------------------------------------------------------

async def _insert_job(session: AsyncSession, **overrides) -> uuid.UUID:
    """Insert a row into the throwaway uploads table and return its id."""
    from sqlalchemy.sql.elements import TextClause
    
    job_id = overrides.pop("id", uuid.uuid4())
    defaults = {
        "session_id": f"test-{uuid.uuid4().hex[:8]}",
        "filename": "test.mp3",
        "size_bytes": 1024,
        "content_type": "audio/mpeg",
        "language_code": "en-IN",
        "storage_path": f"test/{uuid.uuid4().hex}/test.mp3",
        "status": "queued",
        "summary_status": "pending",
        "gnani_started": False,
        "files_attempts": 0,
        "attempts": 0,
        "next_run_at": text("now()"),
        "created_at": text("now()"),
        "updated_at": text("now()"),
    }
    defaults.update(overrides)

    cols = ["id"]
    placeholders = [":id"]
    params = {"id": job_id}

    for k, v in defaults.items():
        cols.append(k)
        if v is None:
            placeholders.append("NULL")
        elif isinstance(v, TextClause):
            placeholders.append(v.text)
        else:
            placeholders.append(f":{k}")
            params[k] = v

    await session.execute(
        text(f"INSERT INTO uploads ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"),
        params,
    )
    return job_id


async def _get_job(session: AsyncSession, job_id: uuid.UUID) -> dict:
    """Read a row back from the throwaway table."""
    result = await session.execute(
        text("SELECT * FROM uploads WHERE id = :id"),
        {"id": job_id},
    )
    row = result.mappings().first()
    assert row is not None, f"Job {job_id} not found"
    return dict(row)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestClaimJob:
    """Tests for claim_job()."""

    async def test_null_lease_claimed(self, session):
        """A job with NULL lease_expires_at is claimable."""
        job_id = await _insert_job(session, status="queued", lease_expires_at=None)
        claimed = await claim_job(session)
        assert claimed is not None
        assert claimed["id"] == job_id
        assert claimed["status"] == "queued"

        row = await _get_job(session, job_id)
        assert row["lease_expires_at"] is not None
        assert row["attempts"] == 1

    async def test_live_lease_skipped(self, session):
        """A job with a lease expiring in the future is NOT claimable."""
        future = text("now() + interval '300 seconds'")
        await _insert_job(session, status="queued", lease_expires_at=future)
        claimed = await claim_job(session)
        assert claimed is None

    async def test_expired_lease_reclaimed(self, session):
        """A job whose lease has expired IS claimable."""
        past = text("now() - interval '10 seconds'")
        job_id = await _insert_job(session, status="queued", lease_expires_at=past)
        claimed = await claim_job(session)
        assert claimed is not None
        assert claimed["id"] == job_id

    async def test_future_next_run_at_skipped(self, session):
        """A job whose next_run_at is in the future is NOT claimable."""
        future = text("now() + interval '300 seconds'")
        await _insert_job(session, status="queued", next_run_at=future)
        claimed = await claim_job(session)
        assert claimed is None

    async def test_non_worker_statuses_skipped(self, session):
        """Jobs in awaiting_upload, completed, or failed are NOT claimable."""
        for status in ("awaiting_upload", "completed", "failed"):
            await _insert_job(session, status=status)
        claimed = await claim_job(session)
        assert claimed is None

    async def test_smoke_session_skipped(self, session):
        """Jobs with session_id starting with 'smoke-' are skipped."""
        await _insert_job(session, status="queued", session_id="smoke-test-123")
        claimed = await claim_job(session)
        assert claimed is None

    async def test_oldest_next_run_at_first(self, session):
        """The job with the earliest next_run_at is claimed first."""
        old = text("now() - interval '60 seconds'")
        recent = text("now() - interval '5 seconds'")

        id_old = await _insert_job(session, status="queued", next_run_at=old)
        _id_recent = await _insert_job(session, status="queued", next_run_at=recent)

        claimed = await claim_job(session)
        assert claimed is not None
        assert claimed["id"] == id_old

    async def test_concurrent_claims_different_rows(
        self, engine, setup_schema, schema_name
    ):
        """Two concurrent claims on separate connections get DIFFERENT rows.
        A third claim gets None."""
        safe_schema = _validate_schema_name(schema_name)

        # Insert two rows on a dedicated connection (committed so other conns see them)
        async with engine.connect() as setup_conn:
            await setup_conn.execute(text(f"SET search_path TO {safe_schema}, public"))
            id1 = uuid.uuid4()
            id2 = uuid.uuid4()
            for jid, offset in [(id1, 2), (id2, 1)]:
                await setup_conn.execute(
                    text(
                        "INSERT INTO uploads "
                        "(id, session_id, filename, size_bytes, language_code, "
                        "storage_path, status, summary_status, gnani_started, "
                        "files_attempts, attempts, next_run_at, created_at, updated_at) "
                        "VALUES (:id, :sid, :fn, :sz, :lc, :sp, :st, :ss, "
                        f":gs, :fa, :att, now() - interval '{offset} seconds', now(), now())"
                    ),
                    {
                        "id": jid, "sid": f"test-{uuid.uuid4().hex[:8]}",
                        "fn": "test.mp3", "sz": 1024, "lc": "en-IN",
                        "sp": f"test/{uuid.uuid4().hex}/test.mp3",
                        "st": "queued", "ss": "pending", "gs": False,
                        "fa": 0, "att": 0,
                    },
                )
            await setup_conn.commit()

        # Two concurrent claims on separate connections
        results: list[dict | None] = []

        async def do_claim():
            conn = await engine.connect()
            try:
                trans = await conn.begin()
                await conn.execute(text(f"SET search_path TO {safe_schema}, public"))
                s = AsyncSession(bind=conn, expire_on_commit=False)
                try:
                    r = await claim_job(s)
                    results.append(r)
                    # Hold the transaction open briefly so both claims overlap
                    await asyncio.sleep(0.2)
                    await trans.commit()
                except Exception:
                    await trans.rollback()
                    raise
                finally:
                    await s.close()
            finally:
                await conn.close()

        await asyncio.gather(do_claim(), do_claim())

        claimed_ids = {r["id"] for r in results if r is not None}
        assert len(claimed_ids) == 2
        assert claimed_ids == {id1, id2}

        # Third claim: nothing left
        conn3 = await engine.connect()
        try:
            trans3 = await conn3.begin()
            await conn3.execute(text(f"SET search_path TO {safe_schema}, public"))
            s3 = AsyncSession(bind=conn3, expire_on_commit=False)
            try:
                third = await claim_job(s3)
                assert third is None
            finally:
                await s3.close()
                await trans3.rollback()
        finally:
            await conn3.close()

        # Cleanup: truncate the rows we committed
        async with engine.connect() as cleanup_conn:
            await cleanup_conn.execute(text(f"SET search_path TO {safe_schema}, public"))
            await cleanup_conn.execute(text("TRUNCATE uploads"))
            await cleanup_conn.commit()


class TestAttemptsAndFinishStep:
    """Tests for attempts increment/reset and finish_step."""

    async def test_attempts_increments_on_claim(self, session):
        """Each claim increments attempts by 1."""
        job_id = await _insert_job(session, status="queued", attempts=0)
        claimed = await claim_job(session)
        assert claimed is not None

        row = await _get_job(session, job_id)
        assert row["attempts"] == 1

    async def test_attempts_resets_in_finish_step(self, session):
        """finish_step resets attempts to 0."""
        job_id = await _insert_job(session, status="queued", attempts=0)
        claimed = await claim_job(session)
        assert claimed is not None

        row = await _get_job(session, job_id)
        assert row["attempts"] == 1

        await finish_step(
            session, job_id,
            fields={"status": "transcribing", "gnani_status": "STARTING"},
            next_run_in_s=10,
        )

        row = await _get_job(session, job_id)
        assert row["attempts"] == 0
        assert row["status"] == "transcribing"
        assert row["gnani_status"] == "STARTING"
        assert row["lease_expires_at"] is None

    async def test_exceeding_max_claims_fails_worker_stuck(self, session, monkeypatch):
        """When attempts after increment > MAX_CLAIMS, job is failed WORKER_STUCK."""
        import app.worker as worker_mod
        monkeypatch.setattr(worker_mod, "MAX_CLAIMS", 2)

        job_id = await _insert_job(session, status="queued", attempts=2)
        claimed = await claim_job(session)
        # Should return None because the job was failed
        assert claimed is None

        row = await _get_job(session, job_id)
        assert row["status"] == "failed"
        assert row["error_code"] == "WORKER_STUCK"
        assert row["retryable"] is True
        assert row["lease_expires_at"] is None


class TestFinishStepUpdatedAt:
    """finish_step sets updated_at explicitly."""

    async def test_updated_at_advances(self, session):
        """updated_at should advance after finish_step."""
        job_id = await _insert_job(session, status="queued")
        row_before = await _get_job(session, job_id)
        old_updated = row_before["updated_at"]

        # Claim first
        await claim_job(session)
        # Small delay to ensure distinct timestamps
        await asyncio.sleep(0.05)

        await finish_step(
            session, job_id,
            fields={"status": "transcribing"},
            next_run_in_s=0,
        )

        row_after = await _get_job(session, job_id)
        assert row_after["updated_at"] is not None
        assert row_after["status"] == "transcribing"
        assert row_after["updated_at"] >= old_updated - timedelta(seconds=2)


class TestFailJob:
    """Tests for fail_job()."""

    async def test_fail_job_basic(self, session):
        """fail_job sets status, error fields, and clears lease."""
        job_id = await _insert_job(session, status="transcribing")
        # Claim first so there's a lease
        await claim_job(session)

        await fail_job(
            session, job_id,
            error_code="PROVIDER_ERROR",
            message="Something went wrong",
            retryable=True,
        )

        row = await _get_job(session, job_id)
        assert row["status"] == "failed"
        assert row["error_code"] == "PROVIDER_ERROR"
        assert row["error_message"] == "Something went wrong"
        assert row["retryable"] is True
        assert row["lease_expires_at"] is None

    async def test_fail_job_clear_gnani(self, session):
        """fail_job(clear_gnani=True) clears gnani fields."""
        job_id = await _insert_job(
            session,
            status="transcribing",
            gnani_job_id="gnani-123",
            gnani_started=True,
            gnani_status="START_FAILED",
            files_attempts=3,
        )
        await claim_job(session)

        await fail_job(
            session, job_id,
            error_code="PROVIDER_ERROR",
            message="Start failed",
            retryable=True,
            clear_gnani=True,
        )

        row = await _get_job(session, job_id)
        assert row["status"] == "failed"
        assert row["gnani_job_id"] is None
        assert row["gnani_started"] is False
        assert row["gnani_status"] is None
        assert row["files_attempts"] == 0
        assert row["lease_expires_at"] is None

    async def test_fail_job_updated_at_advances(self, session):
        """fail_job should advance updated_at."""
        job_id = await _insert_job(session, status="queued")
        row_before = await _get_job(session, job_id)
        old_updated = row_before["updated_at"]

        await claim_job(session)
        await asyncio.sleep(0.05)

        await fail_job(
            session, job_id,
            error_code="PROVIDER_ERROR",
            message="error",
            retryable=False,
        )

        row_after = await _get_job(session, job_id)
        assert row_after["updated_at"] is not None
        assert row_after["status"] == "failed"
        assert row_after["updated_at"] >= old_updated - timedelta(seconds=2)


class TestReleaseLease:
    """Tests for release_lease()."""

    async def test_release_clears_lease(self, session):
        """release_lease sets lease_expires_at to NULL."""
        job_id = await _insert_job(session, status="queued")
        await claim_job(session)

        row = await _get_job(session, job_id)
        assert row["lease_expires_at"] is not None

        await release_lease(session, job_id)

        row = await _get_job(session, job_id)
        assert row["lease_expires_at"] is None

    async def test_release_lease_updated_at_advances(self, session):
        """release_lease should advance updated_at."""
        job_id = await _insert_job(session, status="queued")
        await claim_job(session)
        row_before = await _get_job(session, job_id)
        old_updated = row_before["updated_at"]

        await asyncio.sleep(0.05)
        await release_lease(session, job_id)

        row_after = await _get_job(session, job_id)
        assert row_after["updated_at"] is not None
        assert row_after["lease_expires_at"] is None
        assert row_after["updated_at"] >= old_updated - timedelta(seconds=2)


class TestClaimUpdatedAt:
    """claim_job sets updated_at explicitly."""

    async def test_claim_updated_at_advances(self, session):
        """updated_at should advance after claim_job."""
        job_id = await _insert_job(session, status="queued")
        row_before = await _get_job(session, job_id)
        old_updated = row_before["updated_at"]

        await asyncio.sleep(0.05)
        await claim_job(session)

        row_after = await _get_job(session, job_id)
        assert row_after["updated_at"] is not None
        assert row_after["attempts"] == 1
        assert row_after["updated_at"] >= old_updated - timedelta(seconds=2)
