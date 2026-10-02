"""
LLM summary service — Gemini via REST generateContent (no SDK).

Context.md §10: summarize raw ASR transcripts via map-reduce for long texts.
The prompt explicitly tells the model the input is unpunctuated ASR output.

Uses httpx async.  Chunked map-reduce when transcript length exceeds
SUMMARY_CHUNK_CHARS * 2.
"""

import asyncio
import logging
from textwrap import dedent

import httpx

from app.config import LLM_API_KEY, LLM_MODEL, SUMMARY_CHUNK_CHARS

logger = logging.getLogger(__name__)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_CALL_TIMEOUT = 60  # per-call timeout in seconds
_MAX_RETRIES = 2
_RETRY_BACKOFF_BASE = 2  # seconds; exponential: 2, 4

# Retryable HTTP status codes from Gemini
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# ---------- Prompt templates ----------

_SYSTEM_PROMPT = dedent("""\
    The input is raw ASR output: lowercase, no punctuation, may contain \
    misheard words or names. Do not invent facts. Produce:
    1) a 2–3 sentence TL;DR,
    2) key points as bullets,
    3) action items / decisions if any (omit the section if none),
    4) topics discussed.
    Output Markdown.""")

_CHUNK_PROMPT = dedent("""\
    Summarize this section of a longer transcript. \
    The input is raw ASR output: lowercase, no punctuation, may contain \
    misheard words or names. Do not invent facts.
    Produce key points as bullets and note any action items or decisions.
    Output Markdown.

    TRANSCRIPT SECTION:
    {chunk}""")

_REDUCE_PROMPT = dedent("""\
    Below are summaries of consecutive sections of one transcript. \
    Merge them into a single coherent summary. Produce:
    1) a 2–3 sentence TL;DR,
    2) key points as bullets,
    3) action items / decisions if any (omit the section if none),
    4) topics discussed.
    Remove duplicates. Output Markdown.

    SECTION SUMMARIES:
    {merged}""")


# ---------- Internal helpers ----------

def _endpoint(model: str) -> str:
    """Build the Gemini generateContent URL."""
    return f"{_GEMINI_URL}/{model}:generateContent"


def _headers() -> dict[str, str]:
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY not configured")
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": LLM_API_KEY,
    }


def _build_payload(
    user_text: str,
    system_text: str | None = None,
) -> dict:
    """Build the generateContent request payload."""
    payload: dict = {
        "contents": [
            {"role": "user", "parts": [{"text": user_text}]},
        ],
    }
    if system_text:
        payload["systemInstruction"] = {
            "parts": [{"text": system_text}],
        }
    return payload


async def _call_gemini(
    prompt: str,
    *,
    system: str | None = None,
    client: httpx.AsyncClient,
) -> str:
    """Make one generateContent call with retries on 429/5xx.

    Returns the text output. Raises LLMError on persistent failure.
    """
    url = _endpoint(LLM_MODEL)
    payload = _build_payload(prompt, system)

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.post(
                url,
                headers=_headers(),
                json=payload,
                timeout=_CALL_TIMEOUT,
            )

            if resp.status_code in _RETRYABLE_STATUSES and attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Gemini returned %s, retrying in %ss (attempt %d/%d)",
                    resp.status_code, wait, attempt + 1, _MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            return _extract_text(data)

        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning("Gemini timeout, retrying in %ss", wait)
                await asyncio.sleep(wait)
                continue
            raise LLMError(f"Gemini timeout after {_MAX_RETRIES + 1} attempts") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"Gemini HTTP {exc.response.status_code}: "
                f"{exc.response.text[:200]}"
            ) from exc

    raise LLMError(f"Gemini failed after retries: {last_error}")


def _extract_text(data: dict) -> str:
    """Extract the generated text from the Gemini response JSON.

    Response shape:
      {"candidates": [{"content": {"parts": [{"text": "…"}]}}]}
    """
    try:
        candidates = data.get("candidates", [])
        if not candidates:
            raise LLMError("No candidates in Gemini response")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise LLMError("No parts in Gemini response candidate")
        text = parts[0].get("text", "")
        if not text.strip():
            raise LLMError("Empty text in Gemini response")
        return text.strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected Gemini response structure: {data!r:.200}") from exc


# ---------- Chunking ----------

def _split_chunks(text: str, chunk_size: int) -> list[str]:
    """Split text into chunks of roughly `chunk_size` chars.

    Tries to split on paragraph boundaries (double newline), falling back
    to splitting on single newlines, then on spaces.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            break

        # Try to find a good split point near chunk_size
        split_at = chunk_size
        # Prefer paragraph boundary
        para_break = remaining.rfind("\n\n", 0, chunk_size)
        if para_break > chunk_size // 2:
            split_at = para_break + 2
        else:
            # Try newline
            nl = remaining.rfind("\n", 0, chunk_size)
            if nl > chunk_size // 2:
                split_at = nl + 1
            else:
                # Try space
                sp = remaining.rfind(" ", 0, chunk_size)
                if sp > chunk_size // 2:
                    split_at = sp + 1

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]

    return chunks


# ---------- Public API ----------

async def summarize(
    transcript: str,
    *,
    client: httpx.AsyncClient,
) -> str:
    """Summarize a transcript, using map-reduce for long texts.

    Returns the summary as Markdown text.
    Raises LLMError on failure.
    """
    threshold = SUMMARY_CHUNK_CHARS * 2

    if len(transcript) <= threshold:
        # Single-shot summarisation
        prompt = f"{_SYSTEM_PROMPT}\n\nTRANSCRIPT:\n{transcript}"
        return await _call_gemini(prompt, system=_SYSTEM_PROMPT, client=client)

    # Map-reduce for long transcripts
    chunks = _split_chunks(transcript, SUMMARY_CHUNK_CHARS)
    logger.info("Summarising %d chunks (total %d chars)", len(chunks), len(transcript))

    # Map: summarise each chunk
    chunk_summaries: list[str] = []
    for i, chunk in enumerate(chunks):
        prompt = _CHUNK_PROMPT.format(chunk=chunk)
        summary = await _call_gemini(prompt, client=client)
        chunk_summaries.append(f"--- Section {i + 1} ---\n{summary}")
        logger.info("Chunk %d/%d summarised", i + 1, len(chunks))

    # Reduce: merge chunk summaries
    merged = "\n\n".join(chunk_summaries)
    prompt = _REDUCE_PROMPT.format(merged=merged)
    return await _call_gemini(prompt, system=_SYSTEM_PROMPT, client=client)


# ---------- Exceptions ----------

class LLMError(Exception):
    """Raised when the LLM call fails after retries."""
    pass
