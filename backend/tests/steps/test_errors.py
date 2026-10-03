from app.constants import ERR_CORRUPT_AUDIO, ERR_NO_SPEECH, ERR_PROVIDER_ERROR
from app.steps.errors import classify_file_error


def test_classify_file_error_no_speech():
    code, retryable, msg = classify_file_error("no speech detected in 19s of audio")
    assert code == ERR_NO_SPEECH
    assert retryable is False
    assert msg == "No speech was detected in this audio. Try a recording with spoken words."


def test_classify_file_error_corrupt():
    code, retryable, msg = classify_file_error("ffprobe could not read the file: the file: Invalid data found when processing input")
    assert code == ERR_CORRUPT_AUDIO
    assert retryable is False
    assert msg == "This file could not be read as audio. Check that it is a valid recording."


def test_classify_file_error_mixed_case():
    code, retryable, msg = classify_file_error("Empty TRANSCRIPT after 3 retries")
    assert code == ERR_NO_SPEECH
    assert retryable is False
    assert msg == "No speech was detected in this audio. Try a recording with spoken words."
    
    code, retryable, msg = classify_file_error("UnSupported format")
    assert code == ERR_CORRUPT_AUDIO
    assert retryable is False
    assert msg == "This file could not be read as audio. Check that it is a valid recording."


def test_classify_file_error_matches_nothing():
    code, retryable, msg = classify_file_error("internal server error")
    assert code == ERR_PROVIDER_ERROR
    assert retryable is True
    assert msg == "The speech service could not process the file. Please try again."


def test_classify_file_error_none():
    code, retryable, msg = classify_file_error(None)
    assert code == ERR_PROVIDER_ERROR
    assert retryable is True
    assert msg == "The speech service could not process the file. Please try again."
