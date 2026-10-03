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

// ---------------------------------------------------------------------------
// Status badge color mapping (matches HistoryList)
// ---------------------------------------------------------------------------

function statusBadgeClass(status: Job["status"], summaryStatus: Job["summary_status"]): string {
  // "Done (summary unavailable)" variant — gray pill
  if (status === "completed" && summaryStatus === "failed") {
    return "bg-gray-100 text-gray-600 ring-gray-200";
  }
  switch (status) {
    case "completed":
      return "bg-green-100 text-green-800 ring-green-200";
    case "failed":
      return "bg-red-100 text-red-800 ring-red-200";
    case "transcribing":
    case "summarizing":
      return "bg-blue-100 text-blue-800 ring-blue-200";
    case "queued":
      return "bg-amber-100 text-amber-800 ring-amber-200";
    default:
      return "bg-gray-100 text-gray-600 ring-gray-200";
  }
}

export function JobStatus({ job, estRatio, nowMs }: JobStatusProps) {
  const stage = stageText(job);
  const elapsed = elapsedSeconds(job, nowMs);
  const frac = progressFraction(job, estRatio, nowMs);
  const slow = isSlow(job, estRatio, nowMs);
  const terminal = isTerminal(job);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <h2 className="text-base font-semibold text-gray-900" aria-live="polite">
            {stage}
          </h2>
          {/* Colored status pill */}
          <span
            className={[
              "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset shrink-0",
              statusBadgeClass(job.status, job.summary_status),
            ].join(" ")}
          >
            {job.status}
          </span>
        </div>
        {elapsed !== null && (
          <span className="text-sm font-medium text-gray-500 tabular-nums shrink-0">
            {formatDuration(elapsed)}
          </span>
        )}
      </div>

      {!terminal && (
        <div className="space-y-2">
          {frac !== null ? (
            /* Determinate bar: visible track + colored fill + smooth CSS transition */
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-gray-200">
              <div
                className="h-2.5 rounded-full bg-blue-500 progress-fill"
                style={{ width: `${Math.round(frac * 100)}%` }}
              />
            </div>
          ) : (
            /* Indeterminate bar: pulsing fill */
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-gray-200">
              <div
                className="h-2.5 animate-[pulse_1.5s_ease-in-out_infinite] rounded-full bg-blue-400"
                style={{ width: "100%" }}
              />
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
