"""
Constants shared across validation, /api/config, and the worker.

Language codes and audio extensions come from the Gnani Batch STT docs
(Introduction.md §Supported languages / §Supported audio formats).
Error codes are the stable strings from Context.md §9.
"""

# ---------- Gnani Batch STT supported languages (Introduction.md) ----------
# Each tuple: (BCP-47 code, human-readable label for the frontend select).
LANGUAGES: list[tuple[str, str]] = [
    ("bn-IN", "Bengali (India)"),
    ("en-IN", "English (India)"),
    ("hi-IN", "Hindi"),
    ("kn-IN", "Kannada"),
    ("ml-IN", "Malayalam"),
    ("mr-IN", "Marathi"),
    ("ta-IN", "Tamil"),
    ("te-IN", "Telugu"),
]

LANGUAGE_CODES: set[str] = {code for code, _ in LANGUAGES}

# ---------- Gnani Batch STT supported audio formats (Introduction.md) ----------
ALLOWED_EXTENSIONS: list[str] = [
    "wav", "mp3", "mp4", "flac", "ogg", "opus", "m4a", "aac", "webm", "amr",
]

# ---------- Error codes (Context.md §9) ----------
# Stable strings; the UI maps each to friendly text.
ERR_FILE_TOO_LARGE = "FILE_TOO_LARGE"
ERR_UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
ERR_RATE_LIMITED_SESSION = "RATE_LIMITED_SESSION"
ERR_DAILY_CAP_REACHED = "DAILY_CAP_REACHED"
ERR_UPLOAD_INCOMPLETE = "UPLOAD_INCOMPLETE"
ERR_UPLOAD_ABANDONED = "UPLOAD_ABANDONED"
ERR_NO_SPEECH = "NO_SPEECH_DETECTED"
ERR_CORRUPT_AUDIO = "CORRUPT_OR_UNREADABLE_AUDIO"
ERR_PROVIDER_AUTH = "PROVIDER_AUTH"
ERR_PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
ERR_PROVIDER_ERROR = "PROVIDER_ERROR"
ERR_PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
ERR_WORKER_STUCK = "WORKER_STUCK"
ERR_SUMMARY_FAILED = "SUMMARY_FAILED"

# ---------- Upload statuses (Context.md §5) ----------
UPLOAD_STATUSES = (
    "awaiting_upload", "queued", "transcribing", "summarizing", "completed", "failed",
)

SUMMARY_STATUSES = ("pending", "done", "failed")
