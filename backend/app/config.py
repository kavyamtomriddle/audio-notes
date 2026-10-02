"""
Application configuration.

Loads environment variables from the repo-root .env file (for local dev).
On Render, real environment variables are used; a missing .env file must not crash.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo-root .env (two levels up from this file: backend/app/config.py -> repo root)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)  # silently does nothing if the file doesn't exist


def _csv_list(key: str, default: str = "") -> list[str]:
    """Split a comma-separated env var into a list of stripped, non-empty strings."""
    raw = os.getenv(key, default)
    return [s.strip() for s in raw.split(",") if s.strip()]


def _int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


# --- CORS ---
CORS_ORIGINS: list[str] = _csv_list("CORS_ORIGINS", "http://localhost:3000")

# --- Database ---
# May be absent on first deploy; engine is created lazily in db.py.
DATABASE_URL: str | None = os.getenv("DATABASE_URL")

# --- Supabase Storage ---
SUPABASE_URL: str | None = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY: str | None = os.getenv("SUPABASE_SERVICE_KEY")
SUPABASE_BUCKET: str = os.getenv("SUPABASE_BUCKET", "private_AUDIO")

# --- Gnani STT ---
GNANI_API_KEY: str | None = os.getenv("GNANI_API_KEY")
GNANI_BASE_URL: str = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai")

# --- LLM ---
LLM_API_KEY: str | None = os.getenv("LLM_API_KEY")
LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-3.8-flash")

# --- Uploads & limits ---
MAX_UPLOAD_BYTES: int = _int("MAX_UPLOAD_BYTES", 52428800)          # 50 MB
MAX_DURATION_HINT_S: int = _int("MAX_DURATION_HINT_S", 7200)       # 2 hours
JOBS_PER_SESSION_PER_DAY: int = _int("JOBS_PER_SESSION_PER_DAY", 10)
JOBS_GLOBAL_PER_DAY: int = _int("JOBS_GLOBAL_PER_DAY", 100)
EST_RATIO: float = _float("EST_RATIO", 0.13)

# --- Worker ---
TRANSCRIBE_TIMEOUT_S: int = _int("TRANSCRIBE_TIMEOUT_S", 3600)
MAX_CLAIMS: int = _int("MAX_CLAIMS", 5)
SUMMARY_CHUNK_CHARS: int = _int("SUMMARY_CHUNK_CHARS", 30000)
ENABLE_WORKER: bool = os.getenv("ENABLE_WORKER", "true").lower() in ("1", "true", "yes")
