import Link from "next/link";
import type { Job } from "../lib/types";
import { friendlyMessage } from "../lib/errors";

interface ErrorPanelProps {
  job: Pick<Job, "error_code" | "error_message" | "retryable">;
  onRetry: () => void;
  retrying: boolean;
}

export function ErrorPanel({ job, onRetry, retrying }: ErrorPanelProps) {
  // Handle undefined/null visibly to avoid silent failures
  const rawCode = job.error_code ?? "UNKNOWN_ERROR";
  const msg = job.error_message || friendlyMessage(rawCode);
  const isRetryable = job.retryable === true;

  return (
    <div
      role="alert"
      className="rounded-lg border border-red-300 bg-red-50 p-6 shadow-sm"
    >
      <div className="mb-4">
        <h3 className="text-lg font-semibold text-red-900">Upload Failed</h3>
        <p className="mt-1 text-sm text-red-800">{msg}</p>
        <p className="mt-2 text-xs text-red-400">Error code: {rawCode}</p>
      </div>
      <div>
        {isRetryable ? (
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-red-800 disabled:opacity-50"
          >
            {retrying ? "Working..." : "Retry"}
          </button>
        ) : (
          <Link
            href="/"
            className="inline-block text-sm font-medium text-red-700 underline hover:text-red-900"
          >
            Upload a different file
          </Link>
        )}
      </div>
    </div>
  );
}
