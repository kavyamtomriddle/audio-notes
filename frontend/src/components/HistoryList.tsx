"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { listJobs } from "../lib/api";
import { stageText, parseServerTime } from "../lib/stage";
import { describeError } from "../lib/errors";
import type { JobListItem } from "../lib/types";

// ---------------------------------------------------------------------------
// Status badge color mapping
// ---------------------------------------------------------------------------

/** Returns Tailwind classes for a colored status pill based on job status. */
function statusBadgeClass(status: JobListItem["status"]): string {
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
      // awaiting_upload or any unknown state
      return "bg-gray-100 text-gray-600 ring-gray-200";
  }
}

// ---------------------------------------------------------------------------
// HistoryList
// ---------------------------------------------------------------------------

export function HistoryList({ refreshKey }: { refreshKey: number }) {
  const [jobs, setJobs] = useState<JobListItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const fetchJobs = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await listJobs();
      setJobs(data);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs, refreshKey]);

  if (loading && !jobs) {
    return <div className="mt-8 text-center text-sm text-gray-500">Loading history...</div>;
  }

  if (error) {
    return (
      <div className="mt-8 rounded-lg bg-red-50 p-4 border border-red-200">
        <p className="text-sm text-red-700">{describeError(error)}</p>
        <button
          onClick={fetchJobs}
          className="mt-2 text-sm font-medium text-red-700 hover:text-red-800"
        >
          Retry
        </button>
      </div>
    );
  }

  if (jobs?.length === 0) {
    return (
      <div className="mt-8 text-center">
        <p className="text-sm text-gray-500">No previous uploads found.</p>
        <button
          onClick={fetchJobs}
          className="mt-2 text-sm text-blue-600 hover:underline"
        >
          Refresh
        </button>
      </div>
    );
  }

  return (
    <div className="mt-12 w-full">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-gray-900">Your History</h2>
        <button
          onClick={fetchJobs}
          disabled={loading}
          className="text-sm text-blue-600 hover:underline disabled:opacity-50"
        >
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>
      {/* gap-3 between cards; shadow-sm → shadow-md on hover for elevation */}
      <div className="space-y-3">
        {jobs?.map((job) => {
          const ms = parseServerTime(job.created_at);
          const timeString = ms ? new Date(ms).toLocaleString() : "Unknown time";
          return (
            <Link
              key={job.id}
              href={`/jobs/${job.id}`}
              className="block rounded-lg border border-gray-200 bg-white p-4 shadow-sm hover:shadow-md hover:border-gray-300 transition-all"
            >
              <div className="flex items-center justify-between mb-2 gap-4">
                {/* Truncate long filenames with ellipsis */}
                <span className="font-medium text-gray-900 truncate min-w-0">
                  {job.filename}
                </span>
                <span className="text-xs text-gray-400 shrink-0">
                  {timeString}
                </span>
              </div>
              <div className="flex items-center gap-2">
                {/* Colored status pill */}
                <span
                  className={[
                    "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
                    statusBadgeClass(job.status),
                  ].join(" ")}
                >
                  {stageText(job)}
                </span>
              </div>
              {job.summary_snippet && (
                <p className="mt-2 text-sm text-gray-500 line-clamp-2">
                  {job.summary_snippet}
                </p>
              )}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
