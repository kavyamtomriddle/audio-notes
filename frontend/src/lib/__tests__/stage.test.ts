/**
 * Tests for src/lib/stage.ts
 *
 * Test names are EXACT as specified in Context.md Phase F2a.
 * Real fixtures are imported where they provide relevant field values.
 */

import { describe, it, expect } from "vitest";
import {
  parseServerTime,
  isTerminal,
  stageText,
  elapsedSeconds,
  progressFraction,
  isSlow,
  formatDuration,
} from "../stage";
import type { Job } from "../types";

// ---------------------------------------------------------------------------
// Fixture imports (copies in src/lib/__tests__/fixtures/ to keep tsc happy)
// ---------------------------------------------------------------------------
import completedFixture from "./fixtures/api_job_completed.json";
import failedFixture from "./fixtures/api_job_failed.json";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a minimal Job stub, overriding only the specified fields. */
function job(overrides: Partial<Job>): Job {
  return {
    id: "test-id",
    session_id: "s1",
    filename: "test.mp3",
    size_bytes: 1000,
    content_type: "audio/mp3",
    language_code: "en-IN",
    duration_hint_s: null,
    status: "queued",
    gnani_status: null,
    summary_status: "pending",
    transcript: null,
    summary: null,
    error_code: null,
    error_message: null,
    retryable: null,
    summary_error: null,
    created_at: "2026-10-03T10:00:00.000000Z",
    queued_at: null,
    processing_started_at: null,
    completed_at: null,
    updated_at: "2026-10-03T10:00:00.000000Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// parseServerTime
// ---------------------------------------------------------------------------

describe("parseServerTime", () => {
  it("parses_z_suffix", () => {
    // Real fixture format: "2026-10-03T10:04:44.119461Z"
    const ms = parseServerTime(completedFixture.created_at);
    expect(ms).not.toBeNull();
    expect(ms).toBe(Date.parse("2026-10-03T10:04:44.119Z"));
  });

  it("treats_missing_timezone_as_utc", () => {
    const ms = parseServerTime("2026-01-15T12:30:00");
    const expected = Date.parse("2026-01-15T12:30:00Z");
    expect(ms).toBe(expected);
  });

  it("trims_microseconds", () => {
    // 6-digit fractional seconds must be trimmed to 3 for Safari
    const ms = parseServerTime("2026-10-03T10:04:52.118197Z");
    expect(ms).toBe(Date.parse("2026-10-03T10:04:52.118Z"));
  });

  it("handles_plus_offset", () => {
    // +05:30 timezone offset
    const ms = parseServerTime("2026-10-03T15:34:52.118197+05:30");
    // equivalent UTC: 10:04:52.118Z
    expect(ms).toBe(Date.parse("2026-10-03T10:04:52.118Z"));
  });

  it("returns_null_for_garbage", () => {
    expect(parseServerTime("not-a-date")).toBeNull();
    expect(parseServerTime(null)).toBeNull();
    expect(parseServerTime(undefined)).toBeNull();
    expect(parseServerTime("")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// isTerminal
// ---------------------------------------------------------------------------

describe("isTerminal", () => {
  it("failed_is_terminal", () => {
    // Use real failed fixture
    expect(isTerminal(failedFixture as Job)).toBe(true);
  });

  it("completed_with_summary_pending_not_terminal", () => {
    expect(isTerminal(job({ status: "completed", summary_status: "pending" }))).toBe(false);
  });

  it("completed_with_summary_done_or_failed_terminal", () => {
    expect(isTerminal(job({ status: "completed", summary_status: "done" }))).toBe(true);
    expect(isTerminal(job({ status: "completed", summary_status: "failed" }))).toBe(true);
    // Real completed fixture has summary_status: "done"
    expect(isTerminal(completedFixture as Job)).toBe(true);
  });

  it("transcribing_not_terminal", () => {
    expect(isTerminal(job({ status: "transcribing" }))).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// stageText
// ---------------------------------------------------------------------------

describe("stageText", () => {
  it("stage_text_for_each_status", () => {
    expect(stageText(job({ status: "awaiting_upload" }))).toBe("Uploading");
    expect(stageText(job({ status: "queued" }))).toBe("Queued");
    expect(stageText(job({ status: "summarizing" }))).toBe("Summarizing");
    expect(stageText(job({ status: "completed", summary_status: "done" }))).toBe("Done");
    expect(stageText(job({ status: "completed", summary_status: "failed" }))).toBe(
      "Done (summary unavailable)",
    );
    expect(stageText(job({ status: "failed" }))).toBe("Failed");
    // completed fixture: summary_status = "done"
    expect(stageText(completedFixture as Job)).toBe("Done");
    // failed fixture: status = "failed"
    expect(stageText(failedFixture as Job)).toBe("Failed");
  });

  it("transcribing_text_by_gnani_status", () => {
    const base = { status: "transcribing" } as const;
    expect(stageText(job({ ...base, gnani_status: null }))).toBe("Starting transcription");
    expect(stageText(job({ ...base, gnani_status: "CREATED" }))).toBe("Starting transcription");
    expect(stageText(job({ ...base, gnani_status: "STARTING" }))).toBe("Starting transcription");
    expect(stageText(job({ ...base, gnani_status: "QUEUED" }))).toBe("Starting transcription");
    expect(stageText(job({ ...base, gnani_status: "IN_PROGRESS" }))).toBe("Transcribing");
    expect(stageText(job({ ...base, gnani_status: "COMPLETED" }))).toBe("Fetching transcript");
  });
});

// ---------------------------------------------------------------------------
// elapsedSeconds
// ---------------------------------------------------------------------------

describe("elapsedSeconds", () => {
  it("elapsed_clamped_at_zero", () => {
    // nowMs is BEFORE processing_started_at → result clamped to 0
    const j = job({
      status: "transcribing",
      processing_started_at: "2026-10-03T10:04:52.118197Z",
    });
    const startMs = Date.parse("2026-10-03T10:04:52.118Z");
    expect(elapsedSeconds(j, startMs - 5000)).toBe(0);
  });

  it("elapsed_frozen_when_terminal", () => {
    // Use real completed fixture: processing_started_at → completed_at
    const j = completedFixture as Job;
    const startMs = parseServerTime(j.processing_started_at)!;
    const endMs = parseServerTime(j.completed_at)!;
    const expected = (endMs - startMs) / 1000;

    // Even if nowMs is far in the future, elapsed should be frozen at completed_at
    const future = endMs + 999_999;
    expect(elapsedSeconds(j, future)).toBeCloseTo(expected, 3);
  });

  it("returns_null_when_no_start_time", () => {
    const j = job({ status: "queued", processing_started_at: null });
    expect(elapsedSeconds(j, Date.now())).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// progressFraction
// ---------------------------------------------------------------------------

describe("progressFraction", () => {
  it("progress_capped_at_95_percent", () => {
    // elapsed >> estimate → fraction should be capped at 0.95
    const j = job({
      status: "transcribing",
      duration_hint_s: 60,
      processing_started_at: "2026-01-01T00:00:00Z",
    });
    const startMs = Date.parse("2026-01-01T00:00:00Z");
    // elapsed = 1000 s, estimate = 0.13 * 60 = 7.8 s → raw = 1000/7.8 >> 1
    expect(progressFraction(j, 0.13, startMs + 1_000_000)).toBe(0.95);
  });

  it("progress_null_without_hint", () => {
    const j = job({
      status: "transcribing",
      duration_hint_s: null,
      processing_started_at: "2026-01-01T00:00:00Z",
    });
    expect(progressFraction(j, 0.13, Date.now())).toBeNull();
  });

  it("progress_null_outside_transcribing", () => {
    const j = job({
      status: "summarizing",
      duration_hint_s: 60,
      processing_started_at: "2026-01-01T00:00:00Z",
    });
    expect(progressFraction(j, 0.13, Date.now())).toBeNull();
  });

  it("progress_fraction_between_0_and_0_95", () => {
    const j = job({
      status: "transcribing",
      duration_hint_s: 100,
      processing_started_at: "2026-01-01T00:00:00Z",
    });
    const startMs = Date.parse("2026-01-01T00:00:00Z");
    // elapsed = 6.5 s, estimate = 0.13 * 100 = 13 s → fraction = 6.5/13 = 0.5
    const frac = progressFraction(j, 0.13, startMs + 6500);
    expect(frac).not.toBeNull();
    expect(frac!).toBeGreaterThan(0);
    expect(frac!).toBeLessThan(0.95);
  });
});

// ---------------------------------------------------------------------------
// isSlow
// ---------------------------------------------------------------------------

describe("isSlow", () => {
  it("is_slow_after_2x_estimate", () => {
    const j = job({
      status: "transcribing",
      duration_hint_s: 60, // estimate = 0.13 * 60 = 7.8 s
      processing_started_at: "2026-01-01T00:00:00Z",
    });
    const startMs = Date.parse("2026-01-01T00:00:00Z");
    // 2× = 15.6 s; 16 s > 15.6 s → slow
    expect(isSlow(j, 0.13, startMs + 16_000)).toBe(true);
    // 10 s < 15.6 s → not slow
    expect(isSlow(j, 0.13, startMs + 10_000)).toBe(false);
  });

  it("not_slow_without_hint", () => {
    const j = job({ status: "transcribing", duration_hint_s: null });
    expect(isSlow(j, 0.13, Date.now())).toBe(false);
  });

  it("not_slow_without_ratio", () => {
    const j = job({ status: "transcribing", duration_hint_s: 60 });
    expect(isSlow(j, null, Date.now())).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// formatDuration
// ---------------------------------------------------------------------------

describe("formatDuration", () => {
  it("formats_zero", () => {
    expect(formatDuration(0)).toBe("0:00");
  });

  it("formats_under_one_minute", () => {
    expect(formatDuration(9)).toBe("0:09");
    expect(formatDuration(59)).toBe("0:59");
  });

  it("formats_over_one_minute", () => {
    expect(formatDuration(65)).toBe("1:05");
    expect(formatDuration(3661)).toBe("61:01");
  });

  it("clamps_negative_to_zero", () => {
    expect(formatDuration(-5)).toBe("0:00");
  });
});
