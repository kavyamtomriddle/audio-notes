"""
Tests for Fix D — worker wiring: build_default_worker must create a real
httpx.AsyncClient shared by both gnani and LLM calls, never a module object.

Five test groups:
  1. Build the worker with httpx.MockTransport, call real gnani.get_job,
     gnani.get_files, and llm.summarize with the Deps clients → parsed results.
  2. The shared client is an httpx.AsyncClient, not a module.
  3. Client is closed after shutdown_worker + lifespan close.
  4. No URL, token, or key appears in caplog during the above.
  5. Static check: no module is passed as gnani_client= or llm_client= in
     app/loop.py or app/steps/deps.py (AST scan).
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.loop import build_default_worker


# ---------------------------------------------------------------------------
# Fixture responses (scrubbed, matching Gnani / Gemini shapes)
# ---------------------------------------------------------------------------

_GET_JOB_RESP = {
    "job_id": "job-aaa",
    "status": "COMPLETED",
    "progress": {"total_files": 1, "completed_files": 1, "failed_files": 0, "percent": 100},
}

_GET_FILES_RESP = {
    "data": [
        {
            "file_id": "file-bbb",
            "status": "COMPLETED",
            "duration_seconds": "12.5",
            "error_message": None,
            "transcript_url": "https://example.invalid/transcript.json",
        }
    ]
}

_GEMINI_RESP = {
    "candidates": [
        {
            "content": {
                "parts": [{"text": "## Summary\nKey points here."}]
            }
        }
    ]
}


# ---------------------------------------------------------------------------
# MockTransport that routes Gnani + Gemini URLs
# ---------------------------------------------------------------------------

def _mock_handler(request: httpx.Request) -> httpx.Response:
    """Route mock HTTP requests to canned responses."""
    url = str(request.url)
    # Gnani get_job
    if "/stt/v3/batch/jobs/" in url and url.endswith("/files"):
        return httpx.Response(200, json=_GET_FILES_RESP)
    if "/stt/v3/batch/jobs/" in url:
        return httpx.Response(200, json=_GET_JOB_RESP)
    # Gemini generateContent
    if "generativelanguage.googleapis.com" in url:
        return httpx.Response(200, json=_GEMINI_RESP)
    return httpx.Response(404, text="Not found")


def _make_mock_client() -> httpx.AsyncClient:
    """Create an httpx.AsyncClient backed by a mock transport."""
    transport = httpx.MockTransport(_mock_handler)
    return httpx.AsyncClient(transport=transport, timeout=60)


# ---------------------------------------------------------------------------
# Test 1: Real service calls with the Deps clients parse correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deps_clients_work_with_real_service_calls(monkeypatch):
    """Build worker → capture Deps → call real gnani.get_job, gnani.get_files,
    llm.summarize with deps.gnani_client / deps.llm_client, assert parsed."""
    import app.steps.deps as deps_mod
    import app.steps.dispatch as dispatch_mod
    import app.worker as worker_mod

    mock_client = _make_mock_client()
    captured_deps: list[Any] = []

    @asynccontextmanager
    async def fake_ts():
        yield MagicMock(name="fake_session")

    async def fake_claim_job(s):
        return None

    # Intercept dispatch.run_step to capture deps
    original_run_step = dispatch_mod.run_step

    async def capture_run_step(job, deps):
        captured_deps.append(deps)

    monkeypatch.setattr(deps_mod, "transactional_session", fake_ts)
    monkeypatch.setattr(worker_mod, "claim_job", fake_claim_job)
    monkeypatch.setattr(dispatch_mod, "run_step", capture_run_step)

    closures = build_default_worker()

    # Replace the real httpx client with our mock
    # The closures capture 'client' by reference in the _run_step closure.
    # We need to patch the client inside the closures dict AND inside the closure's cell.
    # Since the closure captures `client` as a local, we patch via the returned dict.
    # But the closure also captured the same object — so we must use the real client
    # from build_default_worker and rely on the mock transport approach differently.
    # 
    # Better approach: close the real client, monkeypatch at a lower level.
    await closures["client"].aclose()

    # Instead, call _run_step with a job and check what make_deps produced.
    # We need to intercept make_deps to inject our mock client.
    original_make_deps = deps_mod.make_deps

    def patched_make_deps(session, *, gnani_client=None, llm_client=None):
        # Verify they are httpx.AsyncClient, not modules
        assert isinstance(gnani_client, httpx.AsyncClient)
        assert isinstance(llm_client, httpx.AsyncClient)
        # Replace with our mock client for the actual service calls
        return original_make_deps(
            session,
            gnani_client=mock_client,
            llm_client=mock_client,
        )

    monkeypatch.setattr(deps_mod, "make_deps", patched_make_deps)

    closures2 = build_default_worker()
    await closures2["run_step"]({"id": "test-1", "status": "queued"})
    await closures2["client"].aclose()

    assert len(captured_deps) == 1
    deps = captured_deps[0]

    # Now call the REAL service functions with the mock client
    from app.services.gnani import get_job, get_files

    job_resp = await get_job("job-aaa", client=deps.gnani_client)
    assert job_resp["status"] == "COMPLETED"
    assert job_resp["job_id"] == "job-aaa"

    files_resp = await get_files("job-aaa", client=deps.gnani_client)
    assert files_resp["data"][0]["status"] == "COMPLETED"

    from app.services.llm import summarize
    summary = await summarize("hello world test", client=deps.llm_client)
    assert "Summary" in summary or "Key points" in summary

    await mock_client.aclose()


# ---------------------------------------------------------------------------
# Test 2: Shared client is httpx.AsyncClient, not a module
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shared_client_is_httpx_async_client(monkeypatch):
    """build_default_worker returns an httpx.AsyncClient under 'client'."""
    import app.steps.deps as deps_mod
    import app.worker as worker_mod

    @asynccontextmanager
    async def fake_ts():
        yield MagicMock()

    monkeypatch.setattr(deps_mod, "transactional_session", fake_ts)
    monkeypatch.setattr(worker_mod, "claim_job", AsyncMock(return_value=None))
    monkeypatch.setattr(worker_mod, "release_lease", AsyncMock())

    closures = build_default_worker()
    client = closures["client"]

    assert isinstance(client, httpx.AsyncClient), (
        f"Expected httpx.AsyncClient, got {type(client)}"
    )
    # Must NOT be a module
    import types
    assert not isinstance(client, types.ModuleType), (
        "client must not be a module object"
    )

    await client.aclose()


# ---------------------------------------------------------------------------
# Test 3: Client is closed after shutdown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_closed_after_shutdown(monkeypatch):
    """After the lifespan shuts down, the shared httpx client is closed."""
    import app.steps.deps as deps_mod
    import app.worker as worker_mod
    import app.config as config_mod
    import app.main as main_mod

    @asynccontextmanager
    async def fake_ts():
        yield MagicMock()

    monkeypatch.setattr(deps_mod, "transactional_session", fake_ts)
    monkeypatch.setattr(worker_mod, "claim_job", AsyncMock(return_value=None))
    monkeypatch.setattr(worker_mod, "release_lease", AsyncMock())
    monkeypatch.setattr(config_mod, "ENABLE_WORKER", True)

    # Track the client that build_default_worker creates
    from app.main import app, lifespan

    captured_client: list[httpx.AsyncClient] = []
    original_build = build_default_worker

    def capturing_build():
        result = original_build()
        captured_client.append(result["client"])
        return result

    monkeypatch.setattr(main_mod, "build_default_worker", capturing_build)

    async def fake_shutdown(task, stop, state, release, **kw):
        stop.set()
        try:
            await task
        except asyncio.CancelledError:
            pass

    monkeypatch.setattr(main_mod, "shutdown_worker", fake_shutdown)

    async with lifespan(app):
        pass  # yield point

    assert len(captured_client) == 1
    assert captured_client[0].is_closed, "httpx client must be closed after lifespan exit"


# ---------------------------------------------------------------------------
# Test 4: No URL, token or key in caplog
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_secrets_in_caplog(monkeypatch, caplog):
    """Building and running the worker must not log any secrets."""
    import app.steps.deps as deps_mod
    import app.worker as worker_mod
    import app.steps.dispatch as dispatch_mod

    @asynccontextmanager
    async def fake_ts():
        yield MagicMock()

    monkeypatch.setattr(deps_mod, "transactional_session", fake_ts)
    monkeypatch.setattr(worker_mod, "claim_job", AsyncMock(return_value=None))
    monkeypatch.setattr(dispatch_mod, "run_step", AsyncMock())

    with caplog.at_level(logging.DEBUG):
        closures = build_default_worker()
        await closures["claim"]()
        await closures["run_step"]({"id": "j1", "status": "queued"})
        await closures["client"].aclose()

    full_log = caplog.text
    # Must not contain real-looking tokens, keys, or signed URLs
    assert "token=" not in full_log.lower(), "Signed URL token leaked in logs"
    assert "apikey" not in full_log.lower(), "API key leaked in logs"
    assert "x-api-key-id" not in full_log.lower(), "Gnani key header leaked in logs"
    assert "x-goog-api-key" not in full_log.lower(), "LLM key header leaked in logs"


# ---------------------------------------------------------------------------
# Test 5: Static AST check — no module passed as gnani_client or llm_client
# ---------------------------------------------------------------------------

def _find_module_client_kwargs(filepath: str) -> list[str]:
    """Parse filepath and return any keyword arg that assigns a module to
    gnani_client= or llm_client= (e.g. gnani_client=gnani_mod)."""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=filepath)
    issues: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in ("gnani_client", "llm_client"):
                    val = kw.value
                    # Reject: module-level name that is an import alias
                    # (e.g. gnani_mod, None)
                    if isinstance(val, ast.Constant) and val.value is None:
                        issues.append(
                            f"{filepath}:{kw.col_offset} "
                            f"{kw.arg}=None (should be httpx.AsyncClient)"
                        )
                    elif isinstance(val, ast.Name):
                        name = val.id
                        # Check if this name was imported as a module alias
                        # (heuristic: ends in _mod or _svc, or matches a known module)
                        if name.endswith("_mod") or name.endswith("_svc"):
                            issues.append(
                                f"{filepath}:{kw.col_offset} "
                                f"{kw.arg}={name} (looks like a module alias)"
                            )
    return issues


def test_no_module_object_as_client_kwarg():
    """Static check: app/loop.py and app/steps/deps.py must not pass a module
    or None as gnani_client= or llm_client=."""
    backend_dir = os.path.join(os.path.dirname(__file__), "..")
    files_to_check = [
        os.path.join(backend_dir, "app", "loop.py"),
        os.path.join(backend_dir, "app", "steps", "deps.py"),
    ]
    all_issues: list[str] = []
    for fp in files_to_check:
        abs_fp = os.path.abspath(fp)
        assert os.path.isfile(abs_fp), f"File not found: {abs_fp}"
        all_issues.extend(_find_module_client_kwargs(abs_fp))

    assert all_issues == [], (
        "Found module or None passed as client kwarg:\n" + "\n".join(all_issues)
    )
