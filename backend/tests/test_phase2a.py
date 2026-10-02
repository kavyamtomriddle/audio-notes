"""
Phase 2a tests: storage URL builders, delete_object, Gnani error normalisation,
empty transcript, LLM chunking.

All tests use fakes/mocks — no real Supabase, Gnani or Gemini calls.
"""

import json

import httpx
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Storage tests (signed-URL prefix, delete_object success/failure)
# ---------------------------------------------------------------------------

# Patch SUPABASE_URL / SUPABASE_SERVICE_KEY / SUPABASE_BUCKET at module level
# so storage.py functions see them.  We do this via monkeypatch in a fixture.


@pytest.fixture(autouse=True)
def _patch_storage_config(monkeypatch):
    """Set storage config and restore real function implementations.

    The conftest.py autouse patch_storage replaces storage functions with fakes.
    We need the real implementations here since we provide our own fake HTTP
    transports.  We re-import from the original module to get the real functions.
    """
    import importlib
    import app.services.storage as st
    import app.config as cfg

    # Reload storage module to get original function objects
    # (conftest may have already patched them by the time we run)
    importlib.reload(st)

    monkeypatch.setattr(cfg, "SUPABASE_URL", "https://test-project.supabase.co")
    monkeypatch.setattr(cfg, "SUPABASE_SERVICE_KEY", "fake-service-key")
    monkeypatch.setattr(cfg, "SUPABASE_BUCKET", "private_AUDIO")
    monkeypatch.setattr(st, "SUPABASE_URL", "https://test-project.supabase.co")
    monkeypatch.setattr(st, "SUPABASE_SERVICE_KEY", "fake-service-key")
    monkeypatch.setattr(st, "SUPABASE_BUCKET", "private_AUDIO")


class FakeTransport(httpx.AsyncBaseTransport):
    """Return a canned response for any request."""

    def __init__(self, status_code: int, json_body=None, text_body: str = ""):
        self._status = status_code
        if json_body is not None:
            self._content = json.dumps(json_body).encode()
            self._headers = {"content-type": "application/json"}
        else:
            self._content = text_body.encode()
            self._headers = {"content-type": "text/plain"}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=self._status,
            content=self._content,
            headers=self._headers,
            request=request,
        )


# --- Signed-URL prefix tests ---

@pytest.mark.asyncio
async def test_signed_upload_url_starts_with_storage_v1():
    """Upload URL must start with {SUPABASE_URL}/storage/v1/."""
    from app.services.storage import create_signed_upload_url

    # Supabase returns a relative URL without /storage/v1 prefix
    transport = FakeTransport(200, json_body={
        "url": "/object/upload/sign/private_AUDIO/session/job/file.mp3?token=tok123",
    })
    async with httpx.AsyncClient(transport=transport) as client:
        url, _ = await create_signed_upload_url(
            "session/job/file.mp3", client=client,
        )
    assert url.startswith("https://test-project.supabase.co/storage/v1/")


@pytest.mark.asyncio
async def test_signed_download_url_starts_with_storage_v1():
    """Download URL must start with {SUPABASE_URL}/storage/v1/."""
    from app.services.storage import create_signed_download_url

    transport = FakeTransport(200, json_body={
        "signedURL": "/object/sign/private_AUDIO/session/job/file.mp3?token=tok456",
    })
    async with httpx.AsyncClient(transport=transport) as client:
        url = await create_signed_download_url(
            "session/job/file.mp3", client=client,
        )
    assert url.startswith("https://test-project.supabase.co/storage/v1/")

# --- object_exists tests ---

@pytest.mark.asyncio
async def test_object_exists_present():
    """object_exists returns True if object is in LIST array."""
    from app.services.storage import object_exists
    transport = FakeTransport(200, json_body=[
        {"name": "file.mp3", "id": "abc"},
    ])
    async with httpx.AsyncClient(transport=transport) as client:
        result = await object_exists("folder/file.mp3", client=client)
    assert result is True


@pytest.mark.asyncio
async def test_object_exists_absent():
    """object_exists returns False if object is missing from LIST array."""
    from app.services.storage import object_exists
    transport = FakeTransport(200, json_body=[
        {"name": "other.mp3", "id": "def"},
    ])
    async with httpx.AsyncClient(transport=transport) as client:
        result = await object_exists("folder/file.mp3", client=client)
    assert result is False


@pytest.mark.asyncio
async def test_object_exists_404():
    """object_exists returns False on 404/400 from LIST API."""
    from app.services.storage import object_exists
    transport = FakeTransport(404, json_body={"error": "Bucket not found"})
    async with httpx.AsyncClient(transport=transport) as client:
        result = await object_exists("folder/file.mp3", client=client)
    assert result is False


# --- delete_object tests ---

@pytest.mark.asyncio
async def test_delete_object_success():
    """delete_object returns True when Supabase reports a non-empty deleted list."""
    from app.services.storage import delete_object

    # Supabase returns an array of deleted object records
    transport = FakeTransport(200, json_body=[
        {"name": "session/job/file.mp3", "bucket_id": "private_AUDIO", "id": "abc"},
    ])
    async with httpx.AsyncClient(transport=transport) as client:
        result = await delete_object("session/job/file.mp3", client=client)
    assert result is True


@pytest.mark.asyncio
async def test_delete_object_nothing_deleted():
    """delete_object returns False when Supabase returns an empty list (path not matched)."""
    from app.services.storage import delete_object

    # Supabase returns 200 but empty array — nothing was actually deleted
    transport = FakeTransport(200, json_body=[])
    async with httpx.AsyncClient(transport=transport) as client:
        result = await delete_object("nonexistent/path.mp3", client=client)
    assert result is False


@pytest.mark.asyncio
async def test_delete_object_http_error_raises():
    """delete_object raises on HTTP errors (no silent swallowing)."""
    from app.services.storage import delete_object

    transport = FakeTransport(500, text_body="Internal Server Error")
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await delete_object("some/path.mp3", client=client)


# ---------------------------------------------------------------------------
# Gnani error normalisation tests (4 shapes from Context.md §3)
# ---------------------------------------------------------------------------

def _make_response(status_code: int, body=None, text: str = "") -> httpx.Response:
    """Build a fake httpx.Response for normalize_gnani_error."""
    if body is not None:
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = text.encode()
        headers = {"content-type": "text/plain"}
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers=headers,
        request=httpx.Request("GET", "https://api.vachana.ai/test"),
    )


def test_normalize_shape1_plain_text():
    """Shape 1: plain text body (e.g. 500 'Internal Server Error')."""
    from app.services.gnani import normalize_gnani_error

    resp = _make_response(500, text="Internal Server Error")
    err = normalize_gnani_error(resp)
    assert err.code == "PROVIDER_ERROR"
    assert "Internal Server Error" in err.message
    assert err.http_status == 500
    assert err.retryable is True


def test_normalize_shape2_detail_dict():
    """Shape 2: {"detail":{"error_code":"RATE_LIMITED","message":"…","status_code":429}}"""
    from app.services.gnani import normalize_gnani_error

    resp = _make_response(429, body={
        "detail": {
            "error_code": "RATE_LIMITED",
            "message": "Rate limit exceeded",
            "status_code": 429,
        },
    })
    err = normalize_gnani_error(resp)
    assert err.code == "RATE_LIMITED"
    assert "Rate limit" in err.message
    assert err.http_status == 429
    assert err.retryable is True


def test_normalize_shape3_error_string():
    """Shape 3: {"error":"UNSUPPORTED_LANGUAGE","message":"…"}"""
    from app.services.gnani import normalize_gnani_error

    resp = _make_response(400, body={
        "error": "UNSUPPORTED_LANGUAGE",
        "message": "Language xx-XX not supported",
    })
    err = normalize_gnani_error(resp)
    assert err.code == "UNSUPPORTED_LANGUAGE"
    assert "xx-XX" in err.message
    assert err.http_status == 400
    assert err.retryable is False  # 400 is not retryable


def test_normalize_shape4_success_false():
    """Shape 4: {"success":false,"error":{"type":"…","message":"…"}}"""
    from app.services.gnani import normalize_gnani_error

    resp = _make_response(400, body={
        "success": False,
        "error": {
            "type": "VALIDATION_ERROR",
            "message": "Invalid config parameter",
        },
    })
    err = normalize_gnani_error(resp)
    assert err.code == "VALIDATION_ERROR"
    assert "Invalid config" in err.message
    assert err.retryable is False


def test_normalize_401_provider_auth():
    """401 → PROVIDER_AUTH, non-retryable."""
    from app.services.gnani import normalize_gnani_error

    resp = _make_response(401, body={
        "detail": {
            "error_code": "MISSING_API_KEY",
            "message": "Missing API key",
            "status_code": 401,
        },
    })
    err = normalize_gnani_error(resp)
    assert err.code == "PROVIDER_AUTH"
    assert err.retryable is False


def test_normalize_unknown_shape_fallback():
    """Unknown JSON shape → PROVIDER_ERROR with truncated text."""
    from app.services.gnani import normalize_gnani_error

    resp = _make_response(502, body={"something": "unexpected"})
    err = normalize_gnani_error(resp)
    assert err.code == "PROVIDER_ERROR"
    assert err.http_status == 502
    assert err.retryable is True


@pytest.mark.asyncio
async def test_start_job_202_success():
    """start_job should treat 202 as success."""
    from app.services.gnani import start_job
    transport = FakeTransport(202, json_body={
        "job_id": "123", "status": "STARTING",
    })
    async with httpx.AsyncClient(transport=transport) as client:
        result = await start_job("123", client=client)
    assert result["status"] == "STARTING"


@pytest.mark.asyncio
async def test_start_job_409_conflict():
    """start_job should treat 409 as a conflict requiring get_job."""
    from app.services.gnani import start_job
    transport = FakeTransport(409, json_body={
        "error": "JOB_ALREADY_STARTED",
        "message": "Job cannot be restarted from state 'FAILED'"
    })
    async with httpx.AsyncClient(transport=transport) as client:
        result = await start_job("123", client=client)
    assert result["status"] == "CONFLICT"
    assert "Call get_job" in result["message"]


@pytest.mark.asyncio
async def test_get_job_start_failed():
    """get_job handles START_FAILED (e.g. invalid URL) correctly."""
    from app.services.gnani import get_job
    transport = FakeTransport(200, json_body={
        "job_id": "123",
        "status": "START_FAILED",
        "cancel_reason": "All provided paths were invalid — nothing to process.",
        "started_at": None,
        "progress": {"percent": 100}
    })
    async with httpx.AsyncClient(transport=transport) as client:
        result = await get_job("123", client=client)
    
    assert result["status"] == "START_FAILED"
    assert "invalid" in result["cancel_reason"]
    assert result["started_at"] is None
    assert result["progress"]["percent"] == 100


# ---------------------------------------------------------------------------
# Gnani: empty transcript → NO_SPEECH_DETECTED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_transcript_empty_raises():
    """An empty/whitespace full_transcript should raise EmptyTranscriptError."""
    from app.services.gnani import EmptyTranscriptError, fetch_transcript

    transport = FakeTransport(200, json_body={
        "full_transcript": "   ",
        "segments": [],
    })
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(EmptyTranscriptError):
            await fetch_transcript("https://example.com/transcript.json", client=client)


@pytest.mark.asyncio
async def test_fetch_transcript_success():
    """A non-empty transcript is returned as-is (stripped)."""
    from app.services.gnani import fetch_transcript

    text = "this is a valid transcript with actual speech"
    transport = FakeTransport(200, json_body={
        "full_transcript": f"  {text}  ",
        "segments": [],
    })
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_transcript(
            "https://example.com/transcript.json", client=client,
        )
    assert result == text


# ---------------------------------------------------------------------------
# Gnani: parse_duration_seconds (defensive, handles string)
# ---------------------------------------------------------------------------

def test_parse_duration_seconds_string():
    """duration_seconds comes as a STRING from Gnani; must parse to float."""
    from app.services.gnani import parse_duration_seconds

    assert parse_duration_seconds("92.29") == 92.29
    assert parse_duration_seconds("0") == 0.0


def test_parse_duration_seconds_none():
    from app.services.gnani import parse_duration_seconds
    assert parse_duration_seconds(None) is None


def test_parse_duration_seconds_bad_value():
    from app.services.gnani import parse_duration_seconds
    assert parse_duration_seconds("not-a-number") is None


# ---------------------------------------------------------------------------
# LLM: chunking logic
# ---------------------------------------------------------------------------

def test_split_chunks_short_text():
    """Short text should produce a single chunk."""
    from app.services.llm import _split_chunks

    text = "short text"
    chunks = _split_chunks(text, chunk_size=1000)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_chunks_long_text():
    """Long text should be split into multiple chunks."""
    from app.services.llm import _split_chunks

    # Create text that exceeds chunk_size
    text = " ".join([f"word{i}" for i in range(200)])  # ~1000+ chars
    chunks = _split_chunks(text, chunk_size=100)
    assert len(chunks) > 1
    # Recombined chunks should contain all original content
    recombined = "".join(chunks)
    assert recombined == text


def test_split_chunks_prefers_paragraph_boundaries():
    """Chunker should prefer splitting on paragraph boundaries."""
    from app.services.llm import _split_chunks

    # Two paragraphs, each about 50 chars
    para1 = "a" * 45
    para2 = "b" * 45
    text = f"{para1}\n\n{para2}"
    chunks = _split_chunks(text, chunk_size=60)
    assert len(chunks) == 2
    assert chunks[0].strip() == para1
    assert chunks[1].strip() == para2


@pytest.mark.asyncio
async def test_summarize_single_shot():
    """Short transcript should use single-shot (not map-reduce)."""
    from app.services.llm import summarize
    import app.services.llm as llm_mod

    gemini_response = {
        "candidates": [{
            "content": {
                "parts": [{"text": "## TL;DR\nThis is a summary."}],
            },
        }],
    }
    transport = FakeTransport(200, json_body=gemini_response)
    async with httpx.AsyncClient(transport=transport) as client:
        # Patch config values so the threshold is very high
        original_chunk = llm_mod.SUMMARY_CHUNK_CHARS
        try:
            llm_mod.SUMMARY_CHUNK_CHARS = 100000
            result = await summarize("short transcript text", client=client)
        finally:
            llm_mod.SUMMARY_CHUNK_CHARS = original_chunk

    assert "TL;DR" in result


class ChunkCountingTransport(httpx.AsyncBaseTransport):
    """Count how many Gemini calls are made (to verify map-reduce)."""

    def __init__(self):
        self.call_count = 0
        self._response_body = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{"text": "Section summary bullet points."}],
                },
            }],
        }).encode()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        return httpx.Response(
            status_code=200,
            content=self._response_body,
            headers={"content-type": "application/json"},
            request=request,
        )


@pytest.mark.asyncio
async def test_summarize_map_reduce_for_long_text():
    """Transcript longer than SUMMARY_CHUNK_CHARS*2 should trigger map-reduce."""
    from app.services.llm import summarize
    import app.services.llm as llm_mod

    transport = ChunkCountingTransport()

    # Make a very long transcript: 3 chunks of 100 chars each
    # Set chunk size to 100, so threshold = 200, and text > 200 triggers map-reduce
    original_chunk = llm_mod.SUMMARY_CHUNK_CHARS
    try:
        llm_mod.SUMMARY_CHUNK_CHARS = 100
        long_text = " ".join(["word"] * 200)  # ~1000 chars, > 200 threshold
        async with httpx.AsyncClient(transport=transport) as client:
            result = await summarize(long_text, client=client)
    finally:
        llm_mod.SUMMARY_CHUNK_CHARS = original_chunk

    # Should have made multiple calls: N map calls + 1 reduce call
    assert transport.call_count > 1
    assert isinstance(result, str)
    assert len(result) > 0
