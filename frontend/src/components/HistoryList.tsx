"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { listJobs } from "../lib/api";
import { stageText, parseServerTime } from "../lib/stage";
import { describeError } from "../lib/errors";
import type { JobListItem } from "../lib/types";

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
        <h2 className="text-lg font-semibold text-gray-900">Your History</h2>
        <button
          onClick={fetchJobs}
          disabled={loading}
          className="text-sm text-blue-600 hover:underline disabled:opacity-50"
        >
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>
      <div className="space-y-4">
        {jobs?.map((job) => {
          const ms = parseServerTime(job.created_at);
          const timeString = ms ? new Date(ms).toLocaleString() : "Unknown time";
          return (
            <Link
              key={job.id}
              href={`/jobs/${job.id}`}
              className="block rounded-lg border border-gray-200 bg-white p-4 shadow-sm hover:border-gray-300 transition-colors"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-gray-900 truncate pr-4">
                  {job.filename}
                </span>
                <span className="text-sm text-gray-500 shrink-0">
                  {timeString}
                </span>
              </div>
              <div className="flex items-center gap-2 mb-2">
                <span className="inline-flex items-center rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-800">
                  {stageText(job)}
                </span>
              </div>
              {job.summary_snippet && (
                <p className="text-sm text-gray-600 line-clamp-2">
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
