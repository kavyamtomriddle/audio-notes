"use client";
/**
 * /jobs/[id] — Job detail page.
 *
 * Polls the job via usePollJob, shows stage/progress (JobStatus),
 * error panel (ErrorPanel) when failed, transcript (TranscriptPanel),
 * summary (SummaryPanel), reconnecting banner, not-found state,
 * and a loading skeleton.
 *
 * Clock tick: 1 s setInterval (only while non-terminal) drives nowMs
 * for elapsed time and progress bar.
 *
 * Retry buttons are disabled while a retry request is in flight.
 */

import { useParams } from "next/navigation";
import { useEffect, useState, useCallback } from "react";
import Link from "next/link";

import { useConfig } from "@/hooks/useConfig";
import { usePollJob } from "@/hooks/usePollJob";
import { useHealth } from "@/hooks/useHealth";
import { isTerminal } from "@/lib/stage";
import * as api from "@/lib/api";
import { describeError } from "@/lib/errors";

import { HealthBanner } from "@/components/HealthBanner";
import { JobStatus } from "@/components/JobStatus";
import { ErrorPanel } from "@/components/ErrorPanel";
import { TranscriptPanel } from "@/components/TranscriptPanel";
import { SummaryPanel } from "@/components/SummaryPanel";

export default function JobPage() {
  const params = useParams();
  const id = params.id as string;

  const health = useHealth();
  const { config, error: configError } = useConfig();
  const { job, error: pollError, notFound, reconnecting, refresh } = usePollJob(id);

  // 1 s clock tick for elapsed/progress — only while non-terminal
  const [nowMs, setNowMs] = useState(Date.now());

  useEffect(() => {
    if (job && isTerminal(job)) return;

    const interval = setInterval(() => {
      setNowMs(Date.now());
    }, 1_000);

    return () => clearInterval(interval);
  }, [job]);

  // Retry job (transcription failure)
  const [retryingJob, setRetryingJob] = useState(false);
  const [retryJobError, setRetryJobError] = useState<string | null>(null);

  const handleRetryJob = useCallback(async () => {
    setRetryingJob(true);
    setRetryJobError(null);
    try {
      await api.retryJob(id);
      refresh();
    } catch (err: unknown) {
      setRetryJobError(describeError(err));
    } finally {
      setRetryingJob(false);
    }
  }, [id, refresh]);

  // Retry summary
  const [retryingSummary, setRetryingSummary] = useState(false);
  const [retrySummaryError, setRetrySummaryError] = useState<string | null>(null);

  const handleRetrySummary = useCallback(async () => {
    setRetryingSummary(true);
    setRetrySummaryError(null);
    try {
      await api.retrySummary(id);
      refresh();
    } catch (err: unknown) {
      setRetrySummaryError(describeError(err));
    } finally {
      setRetryingSummary(false);
    }
  }, [id, refresh]);

  // --- Render states ---

  // Not found
  if (notFound) {
    return (
      <main className="mx-auto max-w-2xl px-4 py-12">
        <HealthBanner health={health} configError={configError} />
        <div className="mt-6 rounded-lg border border-gray-200 bg-white p-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold text-gray-900">Job not found</h1>
          <p className="mt-2 text-sm text-gray-600">
            History is per browser session — this job may belong to a different
            browser or session.
          </p>
          <Link
            href="/"
            className="mt-4 inline-block text-sm font-medium text-blue-600 underline hover:text-blue-800"
          >
            ← Back to upload
          </Link>
        </div>
      </main>
    );
  }

  // Poll error (non-transient, gave up)
  if (pollError) {
    return (
      <main className="mx-auto max-w-2xl px-4 py-12">
        <HealthBanner health={health} configError={configError} />
        <div
          role="alert"
          className="mt-6 rounded-lg border border-red-300 bg-red-50 p-8 text-center shadow-sm"
        >
          <h1 className="text-xl font-semibold text-red-900">Error</h1>
          <p className="mt-2 text-sm text-red-800">{pollError}</p>
          <Link
            href="/"
            className="mt-4 inline-block text-sm font-medium text-red-700 underline hover:text-red-900"
          >
            ← Back to upload
          </Link>
        </div>
      </main>
    );
  }

  // Loading
  if (!job) {
    return (
      <main className="mx-auto max-w-2xl px-4 py-12">
        <HealthBanner health={health} configError={configError} />
        <div className="mt-6 flex items-center justify-center py-16">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-300 border-t-blue-500" />
          <span className="ml-3 text-sm text-gray-500">Loading…</span>
        </div>
      </main>
    );
  }

  // Job loaded — full UI
  const estRatio = config?.est_ratio ?? null;

  return (
    <main className="mx-auto max-w-2xl space-y-6 px-4 py-12">
      <HealthBanner health={health} configError={configError} />

      {/* Back link + filename */}
      <div>
        <Link
          href="/"
          className="text-sm font-medium text-blue-600 hover:text-blue-800"
        >
          ← Back
        </Link>
        <h1 className="mt-1 text-xl font-semibold text-gray-900 break-all">
          {job.filename}
        </h1>
      </div>

      {/* Reconnecting notice */}
      {reconnecting && (
        <div
          role="status"
          aria-live="polite"
          className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800"
        >
          Reconnecting… showing last known status.
        </div>
      )}

      {/* Stage / progress / elapsed */}
      <JobStatus job={job} estRatio={estRatio} nowMs={nowMs} />

      {/* Error panel when job has failed */}
      {job.status === "failed" && (
        <>
          <ErrorPanel
            job={job}
            onRetry={handleRetryJob}
            retrying={retryingJob}
          />
          {retryJobError && (
            <p role="alert" className="text-sm text-red-600">
              {retryJobError}
            </p>
          )}
        </>
      )}

      {/* Transcript */}
      {job.transcript && <TranscriptPanel transcript={job.transcript} />}

      {/* Summary */}
      <SummaryPanel
        summary={job.summary}
        summaryStatus={job.summary_status}
        jobStatus={job.status}
        onRetry={handleRetrySummary}
        retrying={retryingSummary}
      />
      {retrySummaryError && (
        <p role="alert" className="text-sm text-red-600">
          {retrySummaryError}
        </p>
      )}
    </main>
  );
}
