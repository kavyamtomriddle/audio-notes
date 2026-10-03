/**
 * Error utilities for the Audio Notes frontend.
 *
 * friendlyMessage(code, fallback?) — human-readable text for each §9 error code.
 * describeError(e) — top-level description for any thrown value.
 */

import { ApiError } from "./api";
import { UploadError } from "./upload";

// ---------------------------------------------------------------------------
// Friendly messages per §9 error code
// ---------------------------------------------------------------------------

const FRIENDLY: Record<string, string> = {
  FILE_TOO_LARGE:
    "Your file is too large. Please compress it (e.g. convert to MP3 at 128 kbps — roughly 1 MB per minute) and try again.",
  UNSUPPORTED_FORMAT:
    "That file format is not supported. Please use a supported audio format such as MP3, WAV, FLAC, or M4A.",
  RATE_LIMITED_SESSION:
    "You have sent too many requests recently. Please wait a moment and try again.",
  DAILY_CAP_REACHED:
    "The daily upload limit has been reached. Please try again tomorrow.",
  UPLOAD_INCOMPLETE:
    "The upload did not complete successfully. Please try uploading the file again.",
  UPLOAD_ABANDONED:
    "The upload was not finished in time and has been cancelled. Please try again.",
  NO_SPEECH_DETECTED:
    "No speech was detected in the audio. Please check that the file contains audible speech and try again.",
  CORRUPT_OR_UNREADABLE_AUDIO:
    "The audio file could not be read. It may be corrupted or in an unsupported encoding. Please try a different file.",
  PROVIDER_AUTH:
    "The transcription service is not authorised. Please contact support.",
  PROVIDER_RATE_LIMITED:
    "The transcription service is temporarily busy. Your job will be retried automatically.",
  PROVIDER_ERROR:
    "The transcription service encountered an error. Your job will be retried automatically.",
  PROVIDER_TIMEOUT:
    "The transcription service took too long to respond. Your job will be retried automatically.",
  WORKER_STUCK:
    "The background worker could not complete this job after several attempts. Please retry.",
  SUMMARY_FAILED:
    "The summary could not be generated. Your transcript is still available. You can retry the summary.",
};

/**
 * Return a plain-English message for a §9 error code.
 * Unknown codes return a generic message that includes the raw code.
 * An optional fallback overrides the generic unknown-code text.
 */
export function friendlyMessage(code: string, fallback?: string): string {
  return (
    FRIENDLY[code] ??
    fallback ??
    `An unexpected error occurred (code: ${code}). Please try again or contact support.`
  );
}

// ---------------------------------------------------------------------------
// describeError
// ---------------------------------------------------------------------------

/**
 * Produce a human-readable description of any thrown value.
 *
 * - ApiError network  → mention server may be waking up (free tier, up to 1 min)
 * - ApiError timeout  → mention the request timed out
 * - ApiError http     → friendly message by errorCode (falls back to raw message)
 * - UploadError http  → upload was rejected by storage
 * - UploadError network → upload network failure, server may be waking up
 * - UploadError abort → upload was cancelled
 * - anything else     → generic
 */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.kind === "network") {
      return (
        "Could not reach the server. It may be waking up on the free tier — " +
        "this can take up to a minute. Please wait and try again."
      );
    }
    if (e.kind === "timeout") {
      return (
        "The request timed out. The server may be starting up (free tier, " +
        "up to a minute). Please try again."
      );
    }
    // http error — use friendly message if we have an error code
    if (e.errorCode) {
      return friendlyMessage(e.errorCode, e.message);
    }
    return e.message || `HTTP ${e.status}`;
  }

  if (e instanceof UploadError) {
    if (e.kind === "abort") {
      return "The upload was cancelled.";
    }
    if (e.kind === "network") {
      return (
        "The upload failed due to a network error. The server may be waking up " +
        "on the free tier (this can take up to a minute). Please try again."
      );
    }
    // http
    return `The upload was rejected by storage (status ${e.status ?? "unknown"}). Please try again.`;
  }

  if (e instanceof Error) {
    return e.message || "An unexpected error occurred. Please try again.";
  }

  return "An unexpected error occurred. Please try again.";
}
