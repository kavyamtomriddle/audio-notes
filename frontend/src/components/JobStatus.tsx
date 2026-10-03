import type { Job } from "../lib/types";
import {
  stageText,
  elapsedSeconds,
  progressFraction,
  isSlow,
  isTerminal,
  formatDuration,
} from "../lib/stage";

interface JobStatusProps {
  job: Pick<
    Job,
    | "status"
    | "gnani_status"
    | "summary_status"
    | "processing_started_at"
    | "completed_at"
    | "duration_hint_s"
  >;
  estRatio: number | null | undefined;
  nowMs: number;
}

export function JobStatus({ job, estRatio, nowMs }: JobStatusProps) {
  const stage = stageText(job);
  const elapsed = elapsedSeconds(job, nowMs);
  const frac = progressFraction(job, estRatio, nowMs);
  const slow = isSlow(job, estRatio, nowMs);
  const terminal = isTerminal(job);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900" aria-live="polite">
          {stage}
        </h2>
        {elapsed !== null && (
          <span className="text-sm font-medium text-gray-500 tabular-nums">
            {formatDuration(elapsed)}
          </span>
        )}
      </div>

      {!terminal && (
        <div className="space-y-2">
          {frac !== null ? (
            <div className="h-2 w-full overflow-hidden rounded-full bg-gray-200">
              <div
                className="h-2 rounded-full bg-blue-500 transition-all duration-500 ease-out"
                style={{ width: `${Math.round(frac * 100)}%` }}
              />
            </div>
          ) : (
            <div className="h-2 w-full overflow-hidden rounded-full bg-gray-200">
              <div className="h-2 animate-[pulse_1.5s_ease-in-out_infinite] rounded-full bg-blue-400" style={{ width: '100%' }} />
            </div>
          )}
          {slow && (
            <p className="text-xs text-amber-600">
              Taking longer than expected — still working
            </p>
          )}
        </div>
      )}
    </div>
  );
}
