/**
 * Tests for src/lib/polling.ts — nextPollDelayMs.
 */

import { describe, it, expect } from "vitest";
import { nextPollDelayMs } from "../polling";

describe("nextPollDelayMs", () => {
  it("three_seconds_when_healthy", () => {
    expect(nextPollDelayMs(0)).toBe(3_000);
  });

  it("backs_off_on_errors_and_caps_at_30s", () => {
    expect(nextPollDelayMs(1)).toBe(5_000);
    expect(nextPollDelayMs(2)).toBe(10_000);
    expect(nextPollDelayMs(3)).toBe(20_000);
    expect(nextPollDelayMs(4)).toBe(30_000);
    // Past the array — stays capped
    expect(nextPollDelayMs(5)).toBe(30_000);
    expect(nextPollDelayMs(100)).toBe(30_000);
  });
});
