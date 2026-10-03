/**
 * Polling helpers for the Audio Notes frontend.
 *
 * nextPollDelayMs(consecutiveErrors):
 *   0 errors → 3 000 ms (healthy polling cadence)
 *   then     → 5 000, 10 000, 20 000, capped at 30 000 ms
 */

const DELAYS_MS = [3_000, 5_000, 10_000, 20_000, 30_000] as const;

/**
 * Return the delay (ms) before the NEXT poll request should be scheduled.
 *
 * @param consecutiveErrors — how many consecutive fetch failures have occurred.
 *   0 means the last fetch was healthy.
 */
export function nextPollDelayMs(consecutiveErrors: number): number {
  const idx = Math.min(consecutiveErrors, DELAYS_MS.length - 1);
  return DELAYS_MS[idx];
}
