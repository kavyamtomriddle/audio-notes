"use client";
/**
 * usePollJob — poll GET /api/jobs/{id} with recursive setTimeout.
 *
 * Contract (Context.md §13 / §18):
 *   - Schedule the NEXT request only AFTER the previous one completes.
 *   - Delay comes from nextPollDelayMs(consecutiveErrors).
 *   - Stop when isTerminal(job).
 *   - 404 ⇒ notFound = true, stop.
 *   - Network / timeout / 5xx ⇒ consecutiveErrors++, keep last good job,
 *     reconnecting = (consecutiveErrors >= 2).
 *   - Other 4xx ⇒ error, stop after 3 consecutive 4xx.
 *   - AbortController per request; abort on unmount or id change; AbortError ignored.
 *   - Generation counter (ref) prevents StrictMode double-effects from
 *     overwriting newer state with a stale response.
 *   - refresh() cancels pending timer and fetches immediately (restart loop).
 *   - Never two requests in flight.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getJob } from "../lib/api";
import { ApiError } from "../lib/api";
import { isTerminal } from "../lib/stage";
import { nextPollDelayMs } from "../lib/polling";
import type { Job } from "../lib/types";

export interface UsePollJobResult {
  job: Job | null;
  error: string | null;
  notFound: boolean;
  reconnecting: boolean;
  refresh: () => void;
}

export function usePollJob(id: string): UsePollJobResult {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

  // Generation counter: incremented on every new effect (id change or
  // StrictMode re-mount). Any callback from a previous generation is stale.
  const generationRef = useRef(0);

  // Mutable refs shared by the poll loop and refresh()
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);
  const consecutiveErrorsRef = useRef(0);
  const inFlightRef = useRef(false);

  // refresh(): cancel pending timer and fetch immediately.
  // Stored in a ref so the stable callback identity never triggers re-renders.
  const refreshInnerRef = useRef<(() => void) | null>(null);
  const refresh = useCallback(() => {
    refreshInnerRef.current?.();
  }, []);

  useEffect(() => {
    // Bump generation — any prior stale callbacks will be no-ops.
    const gen = ++generationRef.current;

    // Reset state for this id / mount
    setJob(null);
    setError(null);
    setNotFound(false);
    setReconnecting(false);
    consecutiveErrorsRef.current = 0;
    inFlightRef.current = false;

    // Track consecutive 4xx errors (not network/timeout/5xx)
    let consecutive4xx = 0;

    function isStale(): boolean {
      return generationRef.current !== gen;
    }

    async function poll(): Promise<void> {
      if (isStale()) return;

      // Abort any previous request (defensive — should not happen)
      abortRef.current?.abort();

      const controller = new AbortController();
      abortRef.current = controller;
      inFlightRef.current = true;

      try {
        const data = await getJob(id, controller.signal);
        inFlightRef.current = false;

        if (isStale()) return;

        // Successful fetch — reset error counters
        consecutiveErrorsRef.current = 0;
        consecutive4xx = 0;
        setJob(data);
        setError(null);
        setReconnecting(false);

        // If terminal, stop polling
        if (isTerminal(data)) return;

        // Schedule next poll at healthy delay
        scheduleNext();
      } catch (err: unknown) {
        inFlightRef.current = false;

        if (isStale()) return;

        // AbortError from our own abort (unmount, id change, refresh) — ignore
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (controller.signal.aborted) return;

        if (err instanceof ApiError) {
          // 404 → not found, stop
          if (err.status === 404) {
            setNotFound(true);
            return;
          }

          const isTransient =
            err.kind === "network" ||
            err.kind === "timeout" ||
            (err.status >= 500 && err.status < 600);

          if (isTransient) {
            // Network / timeout / 5xx — keep last good job, bump errors
            consecutiveErrorsRef.current += 1;
            consecutive4xx = 0;
            setReconnecting(consecutiveErrorsRef.current >= 2);
            scheduleNext();
            return;
          }

          // Other 4xx — stop after 3 consecutive
          consecutive4xx += 1;
          if (consecutive4xx >= 3) {
            setError(err.message || `HTTP ${err.status}`);
            return;
          }
          // Retry a couple more times
          scheduleNext();
          return;
        }

        // Unknown error — treat as transient
        consecutiveErrorsRef.current += 1;
        consecutive4xx = 0;
        setReconnecting(consecutiveErrorsRef.current >= 2);
        scheduleNext();
      }
    }

    function scheduleNext(): void {
      if (isStale()) return;
      const delay = nextPollDelayMs(consecutiveErrorsRef.current);
      timerRef.current = setTimeout(() => {
        if (!isStale()) {
          void poll();
        }
      }, delay);
    }

    // Wire up refresh
    refreshInnerRef.current = () => {
      if (isStale()) return;
      // Cancel pending timer
      if (timerRef.current !== undefined) {
        clearTimeout(timerRef.current);
        timerRef.current = undefined;
      }
      // Abort any in-flight request
      abortRef.current?.abort();
      // Fetch immediately
      void poll();
    };

    // Start the first fetch immediately
    void poll();

    // Cleanup on unmount or id change
    return () => {
      // Generation is already bumped at the top of the next effect, but
      // we also abort and clear timers synchronously for safety.
      if (timerRef.current !== undefined) {
        clearTimeout(timerRef.current);
        timerRef.current = undefined;
      }
      abortRef.current?.abort();
      refreshInnerRef.current = null;
    };
  }, [id]);

  return { job, error, notFound, reconnecting, refresh };
}
