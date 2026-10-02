"""
Pytest fixtures for the backend test suite.

Uses an in-memory SQLite DB (async) to avoid touching real Supabase.
Overrides the storage service with fakes so no real Supabase calls are made.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.services import storage as storage_svc


# ---------- In-memory SQLite async engine ----------
# aiosqlite is not required; we use the greenlet-based sync adapter.

_test_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create all tables before each test, drop after."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with _TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------- Fake storage service ----------

_FAKE_UPLOAD_URL = "https://fake-supabase.co/storage/v1/object/upload/sign/bucket/path?token=fake"


async def _fake_create_signed_upload_url(path, *, client=None):
    return _FAKE_UPLOAD_URL, 600


async def _fake_object_exists(path, *, client=None):
    """Default: pretend the object exists."""
    return True


async def _fake_delete_object(path, *, client=None):
    return True


async def _fake_create_signed_download_url(path, *, expires_in=10800, client=None):
    return "https://fake-supabase.co/storage/v1/object/sign/bucket/path?token=fake"


@pytest.fixture(autouse=True)
def patch_storage(monkeypatch):
    """Replace all storage service functions with fakes."""
    monkeypatch.setattr(storage_svc, "create_signed_upload_url", _fake_create_signed_upload_url)
    monkeypatch.setattr(storage_svc, "object_exists", _fake_object_exists)
    monkeypatch.setattr(storage_svc, "delete_object", _fake_delete_object)
    monkeypatch.setattr(storage_svc, "create_signed_download_url", _fake_create_signed_download_url)


# ---------- Override FastAPI dependencies ----------

app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def patch_config(monkeypatch):
    """Override config constants imported into the jobs module so tests use
    predictable defaults regardless of the user's real .env file."""
    import app.routes.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "MAX_UPLOAD_BYTES", 52428800)
    monkeypatch.setattr(jobs_mod, "MAX_DURATION_HINT_S", 7200)
    monkeypatch.setattr(jobs_mod, "JOBS_PER_SESSION_PER_DAY", 10)
    monkeypatch.setattr(jobs_mod, "JOBS_GLOBAL_PER_DAY", 100)
    monkeypatch.setattr(jobs_mod, "EST_RATIO", 0.13)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async test client that talks to the app via ASGI transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
