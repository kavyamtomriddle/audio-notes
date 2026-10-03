"use client";
/**
 * HealthBanner — displays a status banner when the server is not ready.
 *
 * Props:
 *   health      — from useHealth(): "checking"|"ok"|"waking"|"down"
 *   configError — truthy when useConfig() has an error (config fetch failed)
 *
 * Renders nothing when health is "ok" or "checking" AND configError is falsy.
 * Uses role="status" for accessible live-region announcement.
 */

import type { HealthState } from "../hooks/useHealth";

interface HealthBannerProps {
  health: HealthState;
  configError: string | null;
}

export function HealthBanner({ health, configError }: HealthBannerProps) {
  const shouldShow =
    health === "waking" || health === "down" || Boolean(configError);

  if (!shouldShow) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800"
    >
      {/* Spinner icon */}
      <svg
        aria-hidden="true"
        className="h-4 w-4 shrink-0 animate-spin"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
      >
        <circle
          className="opacity-25"
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="4"
        />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
        />
      </svg>
      <span>
        Server is waking up (free tier, can take up to a minute)&hellip; please
        wait.
      </span>
    </div>
  );
}
