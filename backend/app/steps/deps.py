"""
Step dependencies: transactional_session and Deps dataclass.

transactional_session() wraps a session in a transaction that commits on
clean exit and rolls back on exception.  The worker loop creates a Deps
instance per step (a session only lives for one step).

deps.py does NOT own finish_step/fail_job — those live in app.worker and
are imported directly by each step module.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.db import get_session_factory


@asynccontextmanager
async def transactional_session():
    """Yield a session inside a begun transaction.

    Commits on clean exit, rolls back on exception.
    Usage::

        async with transactional_session() as session:
            await finish_step(session, ...)
    """
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            yield session
            # Exiting cleanly → commit is handled by session.begin() context


@dataclass
class Deps:
    """Bag of per-step dependencies injected by the worker loop."""
    session: Any
    gnani_client: Any = None
    llm_client: Any = None


def make_deps(
    session: Any,
    *,
    gnani_client: Any = None,
    llm_client: Any = None,
) -> Deps:
    """Construct a Deps instance.  Convenience wrapper."""
    return Deps(
        session=session,
        gnani_client=gnani_client,
        llm_client=llm_client,
    )
