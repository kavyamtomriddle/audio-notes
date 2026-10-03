"""
Error classification for the worker.
"""

from __future__ import annotations

from app.constants import ERR_CORRUPT_AUDIO, ERR_NO_SPEECH, ERR_PROVIDER_ERROR


def classify_file_error(message: str | None) -> tuple[str, bool, str]:
    """Classify a Gnani file-level error message into our error codes.
    
    Returns (error_code, retryable, friendly_message).
    """
    if not message:
        return (
            ERR_PROVIDER_ERROR,
            True,
            "The speech service could not process the file. Please try again.",
        )
    
    lower_msg = message.lower()
    
    # Check for no speech
    if "no speech" in lower_msg or "empty transcript" in lower_msg:
        return (
            ERR_NO_SPEECH,
            False,
            "No speech was detected in this audio. Try a recording with spoken words.",
        )
        
    # Check for corrupt or unreadable audio
    corrupt_keywords = [
        "ffprobe", "invalid data", "could not read the file", "corrupt", "unsupported", "could not decode audio stream"
    ]
    if any(keyword in lower_msg for keyword in corrupt_keywords):
        return (
            ERR_CORRUPT_AUDIO,
            False,
            "This file could not be read as audio. Check that it is a valid recording.",
        )
        
    # Default to provider error
    return (
        ERR_PROVIDER_ERROR,
        True,
        "The speech service could not process the file. Please try again.",
    )
