/**
 * Tests for src/lib/errors.ts
 *
 * Test names are EXACT as specified in Context.md Phase F2a.
 * Error codes come from Context.md §9.
 */

import { describe, it, expect } from "vitest";
import { friendlyMessage, describeError } from "../errors";
import { ApiError } from "../api";

// All §9 error codes that have a defined friendly message in the FRIENDLY map.
const CONTEXT_ERROR_CODES = [
  "FILE_TOO_LARGE",
  "UNSUPPORTED_FORMAT",
  "RATE_LIMITED_SESSION",
  "DAILY_CAP_REACHED",
  "UPLOAD_INCOMPLETE",
  "UPLOAD_ABANDONED",
  "NO_SPEECH_DETECTED",
  "CORRUPT_OR_UNREADABLE_AUDIO",
  "PROVIDER_AUTH",
  "PROVIDER_RATE_LIMITED",
  "PROVIDER_ERROR",
  "PROVIDER_TIMEOUT",
  "WORKER_STUCK",
  "SUMMARY_FAILED",
] as const;

describe("errors", () => {
  it("every_context_error_code_has_friendly_message", () => {
    for (const code of CONTEXT_ERROR_CODES) {
      const msg = friendlyMessage(code);
      // Must be a non-empty string
      expect(typeof msg).toBe("string");
      expect(msg.length).toBeGreaterThan(0);
      // A known code must NOT fall through to the generic unknown-code message
      expect(msg).not.toMatch(/An unexpected error occurred \(code:/);
    }
  });

  it("unknown_code_includes_raw_code", () => {
    const code = "TOTALLY_MADE_UP_ERROR_XYZ";
    const msg = friendlyMessage(code);
    expect(msg).toContain(code);
  });

  it("network_error_message_mentions_waking_up", () => {
    // ApiError with kind="network" → describeError must mention "waking up"
    // ApiError constructor: (message, status, errorCode, retryable, kind)
    const err = new ApiError(
      "Network error — check your connection",
      0,
      null,
      true,
      "network",
    );
    const msg = describeError(err);
    expect(msg.toLowerCase()).toContain("waking up");
  });

  it("no_message_contains_url", () => {
    // No friendly message in the FRIENDLY map should contain a URL
    for (const code of CONTEXT_ERROR_CODES) {
      const msg = friendlyMessage(code);
      expect(msg).not.toMatch(/https?:\/\//);
    }
    // Unknown code generic message also must not contain a URL
    const generic = friendlyMessage("UNKNOWN_CODE_999");
    expect(generic).not.toMatch(/https?:\/\//);
  });
});
