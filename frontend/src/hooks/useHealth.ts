"use client";
/**
 * useHealth — poll GET /health and derive a simple health state.
 *
 * States:
 *   "checking" — initial, awaiting the first response
 *   "waking"   — first call still pending after 3 s
 *   "ok"       — /health returned successfully
 *   "down"     — /health failed; will retry every 5 s while mounted
 *
 * Cleanup on unmount: cancels any in-flight request and pending timers.
 */

import { useEffect, useRef, useState } from "react";
import { health } from "../lib/api";

export type HealthState = "checking" | "ok" | "waking" | "down";

// How long to wait before upgrading "checking" → "waking"
const WAKING_THRESHOLD_MS = 3_000;
// How long to wait before retrying after a failure
const RETRY_INTERVAL_MS = 5_000;

export function useHealth(): HealthState {
  const [state, setState] = useState<HealthState>("checking");
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    let abortController = new AbortController();
    let wakingTimerId: ReturnType<typeof setTimeout> | undefined;
    let retryTimerId: ReturnType<typeof setTimeout> | undefined;

    async function checkHealth(): Promise<void> {
      abortController = new AbortController();

      // After 3 s with no response, upgrade to "waking"
      wakingTimerId = setTimeout(() => {
        if (mountedRef.current) {
          setState((prev) => (prev === "checking" ? "waking" : prev));
        }
      }, WAKING_THRESHOLD_MS);

      try {
        await health();
        if (wakingTimerId !== undefined) clearTimeout(wakingTimerId);
        if (!mountedRef.current) return;
        setState("ok");
      } catch {
        if (wakingTimerId !== undefined) clearTimeout(wakingTimerId);
        if (!mountedRef.current) return;
        if (abortController.signal.aborted) return;

        setState("down");

        // Retry every 5 s while mounted
        retryTimerId = setTimeout(() => {
          if (mountedRef.current) {
            void checkHealth();
          }
        }, RETRY_INTERVAL_MS);
      }
    }

    void checkHealth();

    return () => {
      mountedRef.current = false;
      abortController.abort();
      if (wakingTimerId !== undefined) clearTimeout(wakingTimerId);
      if (retryTimerId !== undefined) clearTimeout(retryTimerId);
    };
  }, []);

  return state;
}
