"use client";
/**
 * useConfig — fetch /api/config on mount.
 *
 * Retries with exponential backoff (2, 4, 8, 16, 30, 30, … seconds) until
 * the fetch succeeds or the component unmounts.
 * Cleans up the AbortController and any pending retry timer on unmount.
 * Safe under React StrictMode double-mount (the first effect is cleaned up
 * before the second runs).
 */

import { useEffect, useRef, useState } from "react";
import { getConfig } from "../lib/api";
import type { Config } from "../lib/types";

// Backoff schedule: 2,4,8,16,30,30,… seconds
const BACKOFF_S = [2, 4, 8, 16, 30];

function nextBackoffMs(attempt: number): number {
  const seconds = BACKOFF_S[Math.min(attempt, BACKOFF_S.length - 1)];
  return seconds * 1000;
}

export interface UseConfigResult {
  config: Config | null;
  error: string | null;
  loading: boolean;
}

export function useConfig(): UseConfigResult {
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Use a ref so the retry loop can check liveness without stale closure issues
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    let abortController = new AbortController();
    let retryTimerId: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;

    async function fetchConfig(): Promise<void> {
      // Create a new controller for each attempt so abort from a previous
      // attempt doesn't cancel the next one.
      abortController = new AbortController();

      try {
        // getConfig() uses its own 70 s timeout internally; we still pass the
        // unmount signal so we can abort immediately on unmount.
        const data = await getConfig();
        if (!mountedRef.current) return;
        setConfig(data);
        setError(null);
        setLoading(false);
      } catch (err: unknown) {
        if (!mountedRef.current) return;
        // If unmount aborted the request, don't update state or schedule retry.
        if (abortController.signal.aborted) return;

        const msg =
          err instanceof Error ? err.message : "Failed to load configuration.";
        setError(msg);

        // Schedule a retry
        const delayMs = nextBackoffMs(attempt);
        attempt += 1;
        retryTimerId = setTimeout(() => {
          if (mountedRef.current) {
            void fetchConfig();
          }
        }, delayMs);
      }
    }

    void fetchConfig();

    return () => {
      mountedRef.current = false;
      abortController.abort();
      if (retryTimerId !== undefined) clearTimeout(retryTimerId);
    };
  }, []);

  return { config, error, loading };
}
