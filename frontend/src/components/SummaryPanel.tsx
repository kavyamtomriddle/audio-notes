import type { JobStatus, SummaryStatus } from "../lib/types";

interface SummaryPanelProps {
  summary: string | null;
  summaryStatus: SummaryStatus;
  jobStatus: JobStatus;
  onRetry?: () => void;
  retrying?: boolean;
}

export function SummaryPanel({
  summary,
  summaryStatus,
  jobStatus,
  onRetry,
  retrying = false,
}: SummaryPanelProps) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-200 bg-gray-50 px-6 py-4">
        <h2 className="text-lg font-semibold text-gray-900">Summary</h2>
      </div>
      <div className="px-6 py-4">
        {summaryStatus === "done" && summary !== null && (
          <div className="whitespace-pre-wrap text-sm text-gray-700">
            {summary}
          </div>
        )}

        {summaryStatus === "pending" &&
          jobStatus !== "completed" &&
          jobStatus !== "failed" && (
            <p className="text-sm italic text-gray-500">
              Summary appears when transcription finishes
            </p>
          )}

        {summaryStatus === "pending" && jobStatus === "completed" && (
          <p className="text-sm italic text-gray-500">
            Generating summary...
          </p>
        )}

        {summaryStatus === "failed" && (
          <div className="flex items-center justify-between">
            <p className="text-sm text-red-600">
              Summary could not be generated
            </p>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                disabled={retrying}
                className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50"
              >
                {retrying ? "Retrying..." : "Retry summary"}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
