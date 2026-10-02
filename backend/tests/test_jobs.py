"""
Tests for job validation, session scoping, and API endpoints.

All tests use the in-memory SQLite DB and fake storage (see conftest.py).
No real Supabase DB or Storage is touched, so test rows don't count toward daily caps.
"""

import pytest
from httpx import AsyncClient

SESSION = "test-session-abc"
HEADERS = {"X-Session-Id": SESSION}


# ---------- GET /api/config ----------

@pytest.mark.asyncio
async def test_config_returns_languages_and_extensions(client: AsyncClient):
    resp = await client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "languages" in data
    assert len(data["languages"]) == 8  # 8 Gnani-supported languages
    assert "extensions" in data
    assert "mp3" in data["extensions"]
    assert data["max_upload_bytes"] == 52428800
    assert data["est_ratio"] == 0.13


@pytest.mark.asyncio
async def test_config_no_session_header_needed(client: AsyncClient):
    """GET /api/config should work without X-Session-Id."""
    resp = await client.get("/api/config")
    assert resp.status_code == 200


# ---------- POST /api/jobs/initiate — validation ----------

@pytest.mark.asyncio
async def test_initiate_success(client: AsyncClient):
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={
            "filename": "test.mp3",
            "size_bytes": 1024,
            "content_type": "audio/mpeg",
            "language_code": "en-IN",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert "upload_url" in data
    assert "expires_in" in data


@pytest.mark.asyncio
async def test_initiate_unsupported_extension(client: AsyncClient):
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "test.exe", "size_bytes": 1024},
    )
    assert resp.status_code == 415
    assert resp.json()["detail"]["error_code"] == "UNSUPPORTED_FORMAT"


@pytest.mark.asyncio
async def test_initiate_no_extension(client: AsyncClient):
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "noext", "size_bytes": 1024},
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_initiate_file_too_large(client: AsyncClient):
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "big.mp3", "size_bytes": 99999999},
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["error_code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_initiate_duration_too_long(client: AsyncClient):
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "long.mp3", "size_bytes": 1024, "duration_hint_s": 99999},
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["error_code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_initiate_bad_language_code(client: AsyncClient):
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "test.mp3", "size_bytes": 1024, "language_code": "xx-XX"},
    )
    assert resp.status_code == 415
    assert resp.json()["detail"]["error_code"] == "UNSUPPORTED_FORMAT"


@pytest.mark.asyncio
async def test_initiate_missing_session_header(client: AsyncClient):
    """Requests without X-Session-Id should fail (FastAPI enforces Header(...))."""
    resp = await client.post(
        "/api/jobs/initiate",
        json={"filename": "test.mp3", "size_bytes": 1024},
    )
    assert resp.status_code == 422  # validation error


# ---------- Daily caps ----------

@pytest.mark.asyncio
async def test_initiate_session_daily_cap(client: AsyncClient, monkeypatch):
    """After JOBS_PER_SESSION_PER_DAY jobs, further initiates should be rejected."""
    # Set cap to 2 for quick testing
    import app.routes.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "JOBS_PER_SESSION_PER_DAY", 2)

    for _ in range(2):
        resp = await client.post(
            "/api/jobs/initiate",
            headers=HEADERS,
            json={"filename": "test.mp3", "size_bytes": 1024},
        )
        assert resp.status_code == 201

    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "test.mp3", "size_bytes": 1024},
    )
    assert resp.status_code == 429
    assert resp.json()["detail"]["error_code"] == "RATE_LIMITED_SESSION"


@pytest.mark.asyncio
async def test_initiate_global_daily_cap(client: AsyncClient, monkeypatch):
    """After JOBS_GLOBAL_PER_DAY jobs, further initiates should be rejected."""
    import app.routes.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "JOBS_GLOBAL_PER_DAY", 2)
    monkeypatch.setattr(jobs_mod, "JOBS_PER_SESSION_PER_DAY", 100)

    for i in range(2):
        resp = await client.post(
            "/api/jobs/initiate",
            headers={**HEADERS, "X-Session-Id": f"session-{i}"},
            json={"filename": "test.mp3", "size_bytes": 1024},
        )
        assert resp.status_code == 201

    resp = await client.post(
        "/api/jobs/initiate",
        headers={**HEADERS, "X-Session-Id": "session-new"},
        json={"filename": "test.mp3", "size_bytes": 1024},
    )
    assert resp.status_code == 429
    assert resp.json()["detail"]["error_code"] == "DAILY_CAP_REACHED"


# ---------- POST /api/jobs/{id}/complete ----------

@pytest.mark.asyncio
async def test_complete_success(client: AsyncClient):
    # First initiate
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "test.mp3", "size_bytes": 1024},
    )
    job_id = resp.json()["id"]

    # Then complete
    resp = await client.post(f"/api/jobs/{job_id}/complete", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_complete_wrong_state(client: AsyncClient):
    """Completing an already-queued job should return 409."""
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "test.mp3", "size_bytes": 1024},
    )
    job_id = resp.json()["id"]

    # Complete once
    await client.post(f"/api/jobs/{job_id}/complete", headers=HEADERS)

    # Try again
    resp = await client.post(f"/api/jobs/{job_id}/complete", headers=HEADERS)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_complete_object_missing(client: AsyncClient, monkeypatch):
    """If the storage object doesn't exist, /complete should return 409."""
    from app.services import storage as storage_svc

    async def _not_exists(path, *, client=None):
        return False

    monkeypatch.setattr(storage_svc, "object_exists", _not_exists)

    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "test.mp3", "size_bytes": 1024},
    )
    job_id = resp.json()["id"]

    resp = await client.post(f"/api/jobs/{job_id}/complete", headers=HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "UPLOAD_INCOMPLETE"


# ---------- Session scoping ----------

@pytest.mark.asyncio
async def test_get_job_wrong_session(client: AsyncClient):
    """Accessing a job from a different session should return 404."""
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "test.mp3", "size_bytes": 1024},
    )
    job_id = resp.json()["id"]

    # Try from different session
    resp = await client.get(
        f"/api/jobs/{job_id}",
        headers={"X-Session-Id": "other-session"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_jobs_only_own_session(client: AsyncClient):
    """GET /api/jobs should only return jobs for the requesting session."""
    # Create jobs for two sessions
    for _ in range(2):
        await client.post(
            "/api/jobs/initiate",
            headers={"X-Session-Id": "session-A"},
            json={"filename": "a.mp3", "size_bytes": 1024},
        )
    await client.post(
        "/api/jobs/initiate",
        headers={"X-Session-Id": "session-B"},
        json={"filename": "b.mp3", "size_bytes": 1024},
    )

    # List for session A
    resp = await client.get("/api/jobs", headers={"X-Session-Id": "session-A"})
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 2
    assert all(j["filename"] == "a.mp3" for j in jobs)

    # List for session B
    resp = await client.get("/api/jobs", headers={"X-Session-Id": "session-B"})
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 1


# ---------- GET /api/jobs/{id} ----------

@pytest.mark.asyncio
async def test_get_job_detail(client: AsyncClient):
    resp = await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={
            "filename": "test.wav",
            "size_bytes": 2048,
            "content_type": "audio/wav",
            "duration_hint_s": 60.0,
            "language_code": "hi-IN",
        },
    )
    job_id = resp.json()["id"]

    resp = await client.get(f"/api/jobs/{job_id}", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "test.wav"
    assert data["size_bytes"] == 2048
    assert data["language_code"] == "hi-IN"
    assert data["status"] == "awaiting_upload"
    assert data["duration_hint_s"] == 60.0


# ---------- GET /api/jobs (list) ----------

@pytest.mark.asyncio
async def test_list_jobs_newest_first(client: AsyncClient):
    """Jobs should be returned newest first."""
    for i in range(3):
        await client.post(
            "/api/jobs/initiate",
            headers=HEADERS,
            json={"filename": f"file{i}.mp3", "size_bytes": 1024},
        )

    resp = await client.get("/api/jobs", headers=HEADERS)
    jobs = resp.json()
    assert len(jobs) == 3
    # Newest first (file2 was created last)
    assert jobs[0]["filename"] == "file2.mp3"
    assert jobs[2]["filename"] == "file0.mp3"


@pytest.mark.asyncio
async def test_list_jobs_no_transcript_body(client: AsyncClient):
    """List endpoint should not include transcript or summary bodies."""
    await client.post(
        "/api/jobs/initiate",
        headers=HEADERS,
        json={"filename": "test.mp3", "size_bytes": 1024},
    )

    resp = await client.get("/api/jobs", headers=HEADERS)
    jobs = resp.json()
    assert len(jobs) == 1
    assert "transcript" not in jobs[0]
    assert "summary" not in jobs[0]
    assert "summary_snippet" in jobs[0]


# ---------- /health still works ----------

@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
