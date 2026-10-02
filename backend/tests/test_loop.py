"""
Tests for app/loop.py — worker loop, shutdown, closures, lifespan.

All tests use fakes (no DB, no network, no real sleeps > 0.05 s).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.loop import LoopState, _idle, run_worker, shutdown_worker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_clock(start: float = 0.0, step: float = 1.0):
    """Return a callable that advances by *step* each call."""
    t = [start]
    def clock():
        val = t[0]
        t[0] += step
        return val
    return clock


async def _noop_idle(stop_event, seconds):
    """Instant idle — never really sleeps."""
    pass


# ---------------------------------------------------------------------------
# run_worker tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_processes_jobs_until_stopped():
    stop = asyncio.Event()
    state = LoopState()
    processed = []

    async def claim():
        if len(processed) >= 3:
            stop.set()
            return None
        return {"id": f"job-{len(processed) + 1}", "status": "queued"}

    async def run_step(job):
        processed.append(job["id"])

    await run_worker(
        stop, claim=claim, run_step=run_step, sweep=AsyncMock(),
        state=state, idle=_noop_idle, clock=_fake_clock(0, 1),
        sweep_every_s=9999,
    )
    assert processed == ["job-1", "job-2", "job-3"]


@pytest.mark.asyncio
async def test_idle_called_with_idle_s_when_nothing_claimable():
    stop = asyncio.Event()
    state = LoopState()
    idle_calls = []
    call_count = [0]

    async def claim():
        call_count[0] += 1
        if call_count[0] > 1:
            stop.set()
        return None

    async def fake_idle(ev, secs):
        idle_calls.append(secs)

    await run_worker(
        stop, claim=claim, run_step=AsyncMock(), sweep=AsyncMock(),
        state=state, idle=fake_idle, clock=_fake_clock(0, 1),
        sweep_every_s=9999, idle_s=2.0,
    )
    assert idle_calls[0] == 2.0


@pytest.mark.asyncio
async def test_stop_event_exits_promptly():
    stop = asyncio.Event()
    stop.set()
    state = LoopState()

    await run_worker(
        stop, claim=AsyncMock(return_value=None), run_step=AsyncMock(),
        sweep=AsyncMock(), state=state, idle=_noop_idle,
        clock=_fake_clock(), sweep_every_s=9999,
    )
    # Returns immediately — not hanging is the assertion


@pytest.mark.asyncio
async def test_claim_exception_does_not_kill_loop_and_backs_off():
    stop = asyncio.Event()
    state = LoopState()
    idle_calls = []
    call_count = [0]

    async def bad_claim():
        call_count[0] += 1
        if call_count[0] <= 1:
            raise RuntimeError("db error")
        stop.set()
        return None

    async def fake_idle(ev, secs):
        idle_calls.append(secs)

    await run_worker(
        stop, claim=bad_claim, run_step=AsyncMock(), sweep=AsyncMock(),
        state=state, idle=fake_idle, clock=_fake_clock(0, 1),
        sweep_every_s=9999, error_backoff_s=5.0,
    )
    assert 5.0 in idle_calls


@pytest.mark.asyncio
async def test_run_step_exception_does_not_kill_loop():
    stop = asyncio.Event()
    state = LoopState()
    call_count = [0]

    async def claim():
        call_count[0] += 1
        if call_count[0] > 1:
            stop.set()
            return None
        return {"id": "job-1", "status": "queued"}

    async def bad_run_step(job):
        raise ValueError("step boom")

    await run_worker(
        stop, claim=claim, run_step=bad_run_step, sweep=AsyncMock(),
        state=state, idle=_noop_idle, clock=_fake_clock(0, 1),
        sweep_every_s=9999,
    )


@pytest.mark.asyncio
async def test_sweep_runs_when_interval_elapsed():
    stop = asyncio.Event()
    state = LoopState()
    sweep_calls = [0]

    async def sweep():
        sweep_calls[0] += 1

    call_count = [0]
    async def claim():
        call_count[0] += 1
        if call_count[0] > 1:
            stop.set()
        return None

    # last_sweep starts as clock() - sweep_every_s → first check triggers
    await run_worker(
        stop, claim=claim, run_step=AsyncMock(), sweep=sweep,
        state=state, idle=_noop_idle, clock=_fake_clock(0, 1),
        sweep_every_s=300.0,
    )
    assert sweep_calls[0] >= 1


@pytest.mark.asyncio
async def test_sweep_exception_does_not_kill_loop():
    stop = asyncio.Event()
    state = LoopState()
    sweep_count = [0]

    async def bad_sweep():
        sweep_count[0] += 1
        if sweep_count[0] == 1:
            raise RuntimeError("sweep error")

    call_count = [0]
    async def claim():
        call_count[0] += 1
        if call_count[0] > 1:
            stop.set()
        return None

    await run_worker(
        stop, claim=claim, run_step=AsyncMock(), sweep=bad_sweep,
        state=state, idle=_noop_idle, clock=_fake_clock(0, 1),
        sweep_every_s=0.0,
    )


@pytest.mark.asyncio
async def test_current_job_id_set_during_step_and_cleared_after():
    stop = asyncio.Event()
    state = LoopState()
    observed = []
    call_count = [0]

    async def claim():
        call_count[0] += 1
        if call_count[0] > 1:
            stop.set()
            return None
        return {"id": "job-X", "status": "queued"}

    async def run_step(job):
        observed.append(state.current_job_id)

    await run_worker(
        stop, claim=claim, run_step=run_step, sweep=AsyncMock(),
        state=state, idle=_noop_idle, clock=_fake_clock(0, 1),
        sweep_every_s=9999,
    )
    assert observed == ["job-X"]
    assert state.current_job_id is None


@pytest.mark.asyncio
async def test_current_job_id_kept_when_cancelled_mid_step():
    stop = asyncio.Event()
    state = LoopState()

    async def claim():
        return {"id": "job-Z", "status": "queued"}

    async def slow_step(job):
        await asyncio.sleep(10)

    task = asyncio.create_task(run_worker(
        stop, claim=claim, run_step=slow_step, sweep=AsyncMock(),
        state=state, idle=_noop_idle, clock=_fake_clock(0, 1),
        sweep_every_s=9999,
    ))
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert state.current_job_id == "job-Z"


# ---------------------------------------------------------------------------
# shutdown_worker tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shutdown_waits_for_inflight_step():
    stop = asyncio.Event()
    state = LoopState()

    async def claim():
        return {"id": "job-1", "status": "queued"}

    async def slow_step(job):
        await asyncio.sleep(0.03)
        state.current_job_id = None
        stop.set()

    task = asyncio.create_task(run_worker(
        stop, claim=claim, run_step=slow_step, sweep=AsyncMock(),
        state=state, idle=_noop_idle, clock=_fake_clock(0, 1),
        sweep_every_s=9999,
    ))
    await asyncio.sleep(0.01)
    release = AsyncMock()
    await shutdown_worker(task, stop, state, release, timeout_s=2.0)
    assert state.current_job_id is None
    release.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_cancels_after_timeout_and_releases_lease():
    stop = asyncio.Event()
    state = LoopState()

    async def claim():
        return {"id": "job-T", "status": "queued"}

    async def forever_step(job):
        await asyncio.sleep(999)

    task = asyncio.create_task(run_worker(
        stop, claim=claim, run_step=forever_step, sweep=AsyncMock(),
        state=state, idle=_noop_idle, clock=_fake_clock(0, 1),
        sweep_every_s=9999,
    ))
    await asyncio.sleep(0.01)
    release = AsyncMock()
    await shutdown_worker(task, stop, state, release, timeout_s=0.05)
    release.assert_awaited_once_with("job-T")


@pytest.mark.asyncio
async def test_shutdown_release_failure_swallowed():
    stop = asyncio.Event()
    state = LoopState()

    async def claim():
        return {"id": "job-R", "status": "queued"}

    async def forever_step(job):
        await asyncio.sleep(999)

    task = asyncio.create_task(run_worker(
        stop, claim=claim, run_step=forever_step, sweep=AsyncMock(),
        state=state, idle=_noop_idle, clock=_fake_clock(0, 1),
        sweep_every_s=9999,
    ))
    await asyncio.sleep(0.01)
    release = AsyncMock(side_effect=RuntimeError("release boom"))
    await shutdown_worker(task, stop, state, release, timeout_s=0.05)
    release.assert_awaited_once()


# ---------------------------------------------------------------------------
# Closure tests (build_default_worker)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_closure_commits_before_returning(monkeypatch):
    """transactional_session exits (commits) before claim() returns."""
    import app.steps.deps as deps_mod
    import app.worker as worker_mod

    events = []

    @asynccontextmanager
    async def fake_ts():
        session = MagicMock()
        events.append("enter")
        yield session
        events.append("exit")

    fake_claim_job = AsyncMock(return_value={"id": "j1", "status": "queued"})

    monkeypatch.setattr(deps_mod, "transactional_session", fake_ts)
    monkeypatch.setattr(worker_mod, "claim_job", fake_claim_job)

    from app.loop import build_default_worker
    closures = build_default_worker()
    result = await closures["claim"]()

    assert result == {"id": "j1", "status": "queued"}
    assert events == ["enter", "exit"]
    fake_claim_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_step_closure_uses_one_transaction_and_passes_session_in_deps(monkeypatch):
    """One transactional_session, session passed to make_deps → dispatch."""
    import app.steps.deps as deps_mod
    import app.steps.dispatch as dispatch_mod

    events = []
    fake_session = MagicMock(name="fake_session")

    @asynccontextmanager
    async def fake_ts():
        events.append("enter")
        yield fake_session
        events.append("exit")

    real_dispatch_run_step = AsyncMock()

    monkeypatch.setattr(deps_mod, "transactional_session", fake_ts)
    monkeypatch.setattr(dispatch_mod, "run_step", real_dispatch_run_step)

    from app.loop import build_default_worker
    closures = build_default_worker()
    await closures["run_step"]({"id": "j1", "status": "queued"})

    assert events == ["enter", "exit"]
    real_dispatch_run_step.assert_awaited_once()
    call_args = real_dispatch_run_step.call_args
    deps = call_args[0][1]
    assert deps.session is fake_session


@pytest.mark.asyncio
async def test_sweep_closure_deletes_storage_only_after_commit(monkeypatch):
    """storage.delete_object called AFTER transactional_session exits."""
    import app.steps.deps as deps_mod
    import app.sweeper as sweeper_mod
    import app.services.storage as storage_mod
    from app.sweeper import SweepResult

    events = []

    @asynccontextmanager
    async def fake_ts():
        events.append("enter")
        yield MagicMock()
        events.append("commit")

    sr = SweepResult(
        job_id=uuid.uuid4(), action="abandoned", storage_path="a/b.mp3",
    )

    async def fake_sweep(s):
        return [sr]

    async def record_delete(path, **kw):
        events.append(f"delete:{path}")
        return True

    monkeypatch.setattr(deps_mod, "transactional_session", fake_ts)
    monkeypatch.setattr(sweeper_mod, "sweep", fake_sweep)
    monkeypatch.setattr(storage_mod, "delete_object", record_delete)

    from app.loop import build_default_worker
    closures = build_default_worker()
    await closures["sweep"]()

    assert events.index("commit") < events.index("delete:a/b.mp3")


@pytest.mark.asyncio
async def test_release_closure_commits(monkeypatch):
    """release closure calls release_lease inside transactional_session."""
    import app.steps.deps as deps_mod
    import app.worker as worker_mod

    events = []

    @asynccontextmanager
    async def fake_ts():
        events.append("enter")
        yield MagicMock()
        events.append("commit")

    fake_release = AsyncMock()

    monkeypatch.setattr(deps_mod, "transactional_session", fake_ts)
    monkeypatch.setattr(worker_mod, "release_lease", fake_release)

    from app.loop import build_default_worker
    closures = build_default_worker()
    jid = uuid.uuid4()
    await closures["release"](jid)

    assert events == ["enter", "commit"]
    fake_release.assert_awaited_once()


# ---------------------------------------------------------------------------
# Lifespan tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lifespan_no_task_when_worker_disabled(monkeypatch):
    """When ENABLE_WORKER is False, lifespan creates no worker task."""
    import app.config as config_mod
    import app.main as main_mod
    from app.main import app, lifespan

    monkeypatch.setattr(config_mod, "ENABLE_WORKER", False)
    build_called = []
    monkeypatch.setattr(main_mod, "build_default_worker",
                        lambda: build_called.append(1) or {})

    async with lifespan(app):
        pass
    assert build_called == []


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_exactly_one_task(monkeypatch):
    """When ENABLE_WORKER is True, lifespan starts one worker task
    and shuts it down on exit."""
    import app.config as config_mod
    import app.main as main_mod
    from app.main import app, lifespan

    monkeypatch.setattr(config_mod, "ENABLE_WORKER", True)

    fake_closures = {
        "claim": AsyncMock(return_value=None),
        "run_step": AsyncMock(),
        "sweep": AsyncMock(),
        "release": AsyncMock(),
    }
    monkeypatch.setattr(main_mod, "build_default_worker",
                        lambda: fake_closures)

    shutdown_calls = []

    async def fake_shutdown(task, stop, state, release, **kw):
        shutdown_calls.append(task)
        stop.set()
        try:
            await task
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(main_mod, "shutdown_worker", fake_shutdown)

    async with lifespan(app):
        tasks = [t for t in asyncio.all_tasks() if t.get_name() == "worker"]
        assert len(tasks) == 1
    assert len(shutdown_calls) == 1

