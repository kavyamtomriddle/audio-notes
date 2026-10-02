"""
Phase 2b-ii-d tests: step_summarizing + redact().

All tests use mocks — no real network, DB, or .env.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.llm import LLMError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(*, transcript: str = "hello world transcript") -> dict:
    return {
        "id": uuid.uuid4(),
        "transcript": transcript,
    }


def _make_deps() -> SimpleNamespace:
    return SimpleNamespace(
        session=AsyncMock(),
        llm_client=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Tests: step_summarizing success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_success_stores_summary():
    """Happy path: LLM returns summary → finish_step with done fields."""
    job = _make_job()
    deps = _make_deps()
    expected_summary = "## TL;DR\nHello world."

    with (
        patch("app.steps.summarizing.summarize", new_callable=AsyncMock) as mock_sum,
        patch("app.steps.summarizing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_sum.return_value = expected_summary
        from app.steps.summarizing import step_summarizing
        await step_summarizing(job, deps)

    mock_fs.assert_awaited_once()
    call = mock_fs.call_args
    fields = call[1]["fields"]
    assert fields["summary"] == expected_summary
    assert fields["summary_status"] == "done"
    assert fields["status"] == "completed"
    assert isinstance(fields["completed_at"], datetime)
    assert call[1]["next_run_in_s"] == 0


@pytest.mark.asyncio
async def test_summary_success_passes_transcript_to_llm():
    """summarize() must be called with the job's transcript."""
    transcript = "the quick brown fox"
    job = _make_job(transcript=transcript)
    deps = _make_deps()

    with (
        patch("app.steps.summarizing.summarize", new_callable=AsyncMock) as mock_sum,
        patch("app.steps.summarizing.finish_step", new_callable=AsyncMock),
    ):
        mock_sum.return_value = "summary text"
        from app.steps.summarizing import step_summarizing
        await step_summarizing(job, deps)

    # The first positional arg to summarize must be the transcript
    assert mock_sum.call_args[0][0] == transcript


# ---------------------------------------------------------------------------
# Tests: LLM error → completed + summary_status=failed + transcript untouched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_error_sets_summary_failed():
    """LLMError → status=completed, summary_status=failed, transcript NOT touched."""
    job = _make_job(transcript="original transcript")
    deps = _make_deps()

    with (
        patch("app.steps.summarizing.summarize", new_callable=AsyncMock) as mock_sum,
        patch("app.steps.summarizing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_sum.side_effect = LLMError("Gemini timeout after 3 attempts")
        from app.steps.summarizing import step_summarizing
        await step_summarizing(job, deps)

    mock_fs.assert_awaited_once()
    call = mock_fs.call_args
    fields = call[1]["fields"]
    assert fields["summary_status"] == "failed"
    assert fields["status"] == "completed"
    assert "summary_error" in fields
    # transcript must NOT be in fields (never touch transcript)
    assert "transcript" not in fields
    # summary must NOT be set on failure
    assert "summary" not in fields


@pytest.mark.asyncio
async def test_llm_error_stores_redacted_message():
    """summary_error is stored as ≤200 chars, URLs redacted."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.summarizing.summarize", new_callable=AsyncMock) as mock_sum,
        patch("app.steps.summarizing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        # Error message contains a URL that must be redacted
        mock_sum.side_effect = LLMError(
            "Failed to reach https://api.example.com/v1/generate?token=abc123"
        )
        from app.steps.summarizing import step_summarizing
        await step_summarizing(job, deps)

    fields = mock_fs.call_args[1]["fields"]
    stored_error = fields["summary_error"]
    assert len(stored_error) <= 200
    # URL should be redacted
    assert "https://" not in stored_error
    assert "token=abc123" not in stored_error


@pytest.mark.asyncio
async def test_unexpected_exception_inside_step():
    """Unexpected non-LLM exception → completed + summary_status=failed."""
    job = _make_job()
    deps = _make_deps()

    with (
        patch("app.steps.summarizing.summarize", new_callable=AsyncMock) as mock_sum,
        patch("app.steps.summarizing.finish_step", new_callable=AsyncMock) as mock_fs,
    ):
        mock_sum.side_effect = RuntimeError("unexpected crash")
        from app.steps.summarizing import step_summarizing
        await step_summarizing(job, deps)

    fields = mock_fs.call_args[1]["fields"]
    assert fields["summary_status"] == "failed"
    assert fields["status"] == "completed"
    assert "transcript" not in fields


# ---------------------------------------------------------------------------
# Tests: redact()
# ---------------------------------------------------------------------------

def test_redact_removes_https_url():
    from app.steps.summarizing import _redact
    result = _redact("Error reaching https://api.example.com/v1/generate")
    assert "https://" not in result
    assert "[URL]" in result


def test_redact_removes_http_url():
    from app.steps.summarizing import _redact
    result = _redact("http://example.com/path is unreachable")
    assert "http://" not in result
    assert "[URL]" in result


def test_redact_removes_token_value():
    from app.steps.summarizing import _redact
    result = _redact("request failed: token=eyJhbGciOiJIUzI1NiJ9abc123")
    assert "eyJhbGciOiJIUzI1NiJ9abc123" not in result
    assert "token=" not in result.lower() or "[REDACTED]" in result


def test_redact_removes_api_key_like_string():
    from app.steps.summarizing import _redact
    # 32-char key-like string
    result = _redact("key: AIzaSyDExAmPlEkEy1234567890123456")
    assert "AIzaSyDExAmPlEkEy1234567890123456" not in result


def test_redact_preserves_short_strings():
    from app.steps.summarizing import _redact
    # Normal error message — short words should not be mangled
    result = _redact("LLM timed out after 3 retries")
    assert "LLM" in result
    assert "timed" in result
    assert "retries" in result


def test_redact_on_signed_url():
    from app.steps.summarizing import _redact
    signed = (
        "https://example.supabase.co/storage/v1/object/sign/bucket/path.mp3"
        "?token=eyJhbGciOiJIUzI1NiJ9.LONG_PAYLOAD_HERE.SIGNATURE_HERE"
    )
    result = _redact(signed)
    assert "supabase.co" not in result
    assert "eyJhbGciOiJIUzI1NiJ9" not in result
