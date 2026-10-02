"""
Async database engine and session factory (SQLAlchemy 2.x + asyncpg).

The engine is created LAZILY on first use so the app can boot and serve
/health even when DATABASE_URL is not yet set (early deploys on Render).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import DATABASE_URL


class Base(DeclarativeBase):
    """Shared declarative base for all models."""
    pass


def _normalize_url(raw: str) -> str:
    """Ensure the URL uses the asyncpg driver scheme."""
    url = raw
    # Accept both postgresql:// and postgresql+asyncpg://
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


# Module-level singletons; populated on first call to get_engine().
_engine = None
_session_factory = None


def get_engine():
    """Return the async engine, creating it on first call.

    Raises RuntimeError if DATABASE_URL is not configured.
    """
    global _engine
    if _engine is not None:
        return _engine
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set — cannot create database engine"
        )
    _engine = create_async_engine(
        _normalize_url(DATABASE_URL),
        pool_size=5,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory, creating it (and the engine) on first call."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory
    engine = get_engine()
    _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async session and commits/rolls back."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
