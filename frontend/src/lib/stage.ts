/**
 * Stage helpers for the Audio Notes frontend.
 *
 * All functions are pure (no side-effects, no I/O) so they are easy to test.
 *
 * Context.md §13 defines stage text and progress behaviour.
 * Context.md §13 correction (b): parseServerTime trims fractional seconds
 *   to 3 digits (Safari) and treats a missing timezone as UTC.
 */

import type { Job } from "./types";

// ---------------------------------------------------------------------------
// parseServerTime
// ---------------------------------------------------------------------------

/**
 * Parse an ISO 8601 timestamp from the server into a Unix-milliseconds number.
 *
 * Handles:
 *   - Fractional seconds with >3 digits (Safari rejects them): trim to 3.
 *   - Missing timezone designator (no Z, no ±hh:mm): treat as UTC (append Z).
 *   - NaN result from Date.parse → return null.
 *   - null / undefined input → return null.
 */
export function parseServerTime(s: string | null | undefined): number | null {
  if (s == null) return null;

  // Trim fractional seconds to at most 3 digits.
  // Matches: optional dot + digits before Z or ±offset or end-of-string.
  let normalised = s.replace(/(\.\d{3})\d+(?=[Z+\-]|$)/g, "$1");

  // If there is no timezone suffix, treat as UTC.
  // A timezone suffix is: Z, or ±HH, or ±HH:MM (e.g. +05:30, -07:00, +00).
  const hasTz = /Z$|[+\-]\d{2}(:\d{2})?$/.test(normalised);
  if (!hasTz && /\d$/.test(normalised)) {
    normalised = normalised + "Z";
  }

  const ms = Date.parse(normalised);
  return isNaN(ms) ? null : ms;
}

// ---------------------------------------------------------------------------
// isTerminal
// ---------------------------------------------------------------------------

/**
 * Return true when the job has reached a terminal state where polling should stop.
 *
 * Terminal:
 *   - status === "failed"
 *   - status === "completed" AND summary_status is "done" or "failed"
 *     (summary "pending" means we still wait for the summary result)
 *
 * Context.md §13 / polling hook spec.
 */
export function isTerminal(job: Pick<Job, "status" | "summary_status">): boolean {
  if (job.status === "failed") return true;
  if (job.status === "completed" && job.summary_status !== "pending") return true;
  return false;
}

// ---------------------------------------------------------------------------
// stageText
// ---------------------------------------------------------------------------

/**
 * Human-readable stage label for the job page.
 *
 * Mapping per Context.md §13:
 *   awaiting_upload              → "Uploading"
 *   queued                       → "Queued"
 *   transcribing
 *     gnani_status CREATED/STARTING/QUEUED/null → "Starting transcription"
 *     gnani_status IN_PROGRESS                  → "Transcribing"
 *     gnani_status COMPLETED                    → "Fetching transcript"
 *   summarizing                  → "Summarizing"
 *   completed
 *     summary_status === "failed" → "Done (summary unavailable)"
 *     otherwise                   → "Done"
 *   failed                       → "Failed"
 */
export function stageText(job: Pick<Job, "status" | "gnani_status" | "summary_status">): string {
  switch (job.status) {
    case "awaiting_upload":
      return "Uploading";
    case "queued":
      return "Queued";
    case "transcribing": {
      const g = job.gnani_status;
      if (g === "IN_PROGRESS") return "Transcribing";
      if (g === "COMPLETED") return "Fetching transcript";
      // CREATED, STARTING, QUEUED, null, or anything else
      return "Starting transcription";
    }
    case "summarizing":
      return "Summarizing";
    case "completed":
      return job.summary_status === "failed" ? "Done (summary unavailable)" : "Done";
    case "failed":
      return "Failed";
    default:
      return "Unknown";
  }
}

// ---------------------------------------------------------------------------
// elapsedSeconds
// ---------------------------------------------------------------------------

/**
 * Elapsed processing time in seconds.
 *
 * While the job is non-terminal: max(0, nowMs - processing_started_at).
 * Once terminal: frozen at (completed_at - processing_started_at), or
 *   (nowMs - processing_started_at) if completed_at is missing.
 * Returns null if processing_started_at is missing/unparseable.
 */
export function elapsedSeconds(
  job: Pick<Job, "status" | "summary_status" | "processing_started_at" | "completed_at">,
  nowMs: number,
): number | null {
  const startMs = parseServerTime(job.processing_started_at);
  if (startMs === null) return null;

  if (isTerminal(job)) {
    const endMs = parseServerTime(job.completed_at) ?? nowMs;
    return Math.max(0, (endMs - startMs) / 1000);
  }

  return Math.max(0, (nowMs - startMs) / 1000);
}

// ---------------------------------------------------------------------------
// progressFraction
// ---------------------------------------------------------------------------

/**
 * Estimated transcription progress as a fraction in [0, 0.95].
 *
 * Only meaningful while status === "transcribing".
 * Returns null if:
 *   - status is not "transcribing"
 *   - duration_hint_s is null/missing
 *   - estRatio is falsy (0, null, undefined)
 *   - processing_started_at is missing/unparseable
 *
 * Formula: min(0.95, elapsed / (estRatio * duration_hint_s))
 * where elapsed = elapsedSeconds(job, nowMs).
 */
export function progressFraction(
  job: Pick<Job, "status" | "summary_status" | "processing_started_at" | "completed_at" | "duration_hint_s">,
  estRatio: number | null | undefined,
  nowMs: number,
): number | null {
  if (job.status !== "transcribing") return null;
  if (!estRatio) return null;
  const hint = job.duration_hint_s;
  if (hint == null || hint <= 0) return null;

  const elapsed = elapsedSeconds(job, nowMs);
  if (elapsed === null) return null;

  return Math.min(0.95, elapsed / (estRatio * hint));
}

// ---------------------------------------------------------------------------
// isSlow
// ---------------------------------------------------------------------------

/**
 * Return true when elapsed processing time exceeds 2× the estimated duration.
 *
 * Estimate = estRatio * duration_hint_s.
 * Returns false (not slow) if hint or ratio is missing, or if elapsed is null.
 */
export function isSlow(
  job: Pick<Job, "status" | "summary_status" | "processing_started_at" | "completed_at" | "duration_hint_s">,
  estRatio: number | null | undefined,
  nowMs: number,
): boolean {
  if (!estRatio) return false;
  const hint = job.duration_hint_s;
  if (hint == null || hint <= 0) return false;

  const elapsed = elapsedSeconds(job, nowMs);
  if (elapsed === null) return false;

  return elapsed > 2 * estRatio * hint;
}

// ---------------------------------------------------------------------------
// formatDuration
// ---------------------------------------------------------------------------

/**
 * Format a duration in seconds as "m:ss".
 *
 * Examples: 0 → "0:00", 65 → "1:05", 3661 → "61:01".
 * Negative values are clamped to 0.
 */
export function formatDuration(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const minutes = Math.floor(s / 60);
  const seconds = s % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}
