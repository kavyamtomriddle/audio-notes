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


# --- CORS ---
CORS_ORIGINS: list[str] = _csv_list("CORS_ORIGINS", "http://localhost:3000")
