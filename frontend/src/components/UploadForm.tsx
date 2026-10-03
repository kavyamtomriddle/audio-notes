"use client";
/**
 * UploadForm — select language, pick/drop audio file, upload through the full
 * initiate → PUT → complete pipeline with progress bar.
 *
 * Props:
 *   config     — from useConfig(); never null here (caller waits).
 *   onCreated? — called with the job id after completeJob succeeds.
 *
 * Rules (§18):
 *   - signed upload_url is kept only in a local variable (never in state).
 *   - AbortController cancels XHR on "Cancel"; job is left for the sweeper.
 *   - No printing to console; no raw HTML insertion; no hardcoded lang/ext lists.
 */

import { useRef, useState } from "react";
import type { Config } from "../lib/types";
import { validateFile } from "../lib/validate";
import { readDurationHint } from "../lib/audio";
import { initiateJob, completeJob } from "../lib/api";
import { uploadFile } from "../lib/upload";
import { describeError } from "../lib/errors";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Phase =
  | "idle"
  | "reading"
  | "initiating"
  | "uploading"
  | "completing"
  | "done";

interface UploadFormProps {
  config: Config;
  onCreated?: (id: string) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function defaultLanguage(config: Config): string {
  const codes = config.languages.map((l) => l.code);
  return (codes[0] ?? "");
}

function formatPercent(loaded: number, total: number): number {
  if (total === 0) return 0;
  return Math.min(100, Math.round((loaded / total) * 100));
}

// ---------------------------------------------------------------------------
// UploadForm
// ---------------------------------------------------------------------------

export function UploadForm({ config, onCreated }: UploadFormProps) {
  const [lang, setLang] = useState<string>(() => defaultLanguage(config));
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<number>(0); // 0-100
  const [error, setError] = useState<string | null>(null);
  const [doneId, setDoneId] = useState<string | null>(null);
  const [busyMsg, setBusyMsg] = useState<string>("");
  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const busy = phase !== "idle" && phase !== "done";

  // -------------------------------------------------------------------------
  // Reset
  // -------------------------------------------------------------------------

  function reset() {
    abortRef.current?.abort();
    abortRef.current = null;
    setFile(null);
    setPhase("idle");
    setProgress(0);
    setError(null);
    setDoneId(null);
    setBusyMsg("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  // -------------------------------------------------------------------------
  // File selection (shared by input + drop)
  // -------------------------------------------------------------------------

  function pickFile(f: File) {
    const err = validateFile(f, config);
    if (err) {
      setError(err.message);
      setFile(null);
    } else {
      setError(null);
      setFile(f);
    }
  }

  // -------------------------------------------------------------------------
  // Submit
  // -------------------------------------------------------------------------

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || busy) return;

    setError(null);
    const abort = new AbortController();
    abortRef.current = abort;

    try {
      // 1. Read duration hint
      setPhase("reading");
      setBusyMsg("Reading audio duration…");
      const durationHint = await readDurationHint(file);

      if (abort.signal.aborted) return;

      if (
        durationHint !== null &&
        durationHint > config.max_duration_hint_s
      ) {
        const limitMin = Math.round(config.max_duration_hint_s / 60);
        throw new Error(
          `Audio is too long (${Math.round(durationHint / 60)} min). ` +
            `The maximum allowed duration is ${limitMin} min.`,
        );
      }

      // 2. Initiate job
      setPhase("initiating");
      setBusyMsg("Creating job…");
      const initiated = await initiateJob(
        {
          filename: file.name,
          size_bytes: file.size,
          content_type: file.type || null,
          duration_hint_s: durationHint,
          language_code: lang,
        },
      );

      if (abort.signal.aborted) return;

      // Hold upload_url only in this local variable — never in state.
      const uploadUrl = initiated.upload_url;
      const jobId = initiated.id;

      // 3. Upload
      setPhase("uploading");
      setProgress(0);
      setBusyMsg("Uploading…");
      await uploadFile({
        url: uploadUrl,
        file,
        signal: abort.signal,
        onProgress: (loaded, total) =>
          setProgress(formatPercent(loaded, total)),
      });

      if (abort.signal.aborted) return;

      // 4. Complete
      setPhase("completing");
      setBusyMsg("Finalising…");
      await completeJob(jobId);

      if (abort.signal.aborted) return;

      setDoneId(jobId);
      setPhase("done");
      onCreated?.(jobId);
    } catch (err: unknown) {
      // Ignore abort-triggered errors
      if (abort.signal.aborted) return;
      setError(describeError(err));
      setPhase("idle");
      setBusyMsg("");
    }
  }

  // -------------------------------------------------------------------------
  // Cancel
  // -------------------------------------------------------------------------

  function handleCancel() {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase("idle");
    setBusyMsg("");
    setProgress(0);
  }

  // -------------------------------------------------------------------------
  // Drag-and-drop
  // -------------------------------------------------------------------------

  function handleDrop(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    if (busy) return;
    const dropped = e.dataTransfer.files[0];
    if (dropped) pickFile(dropped);
  }

  function handleDragOver(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault();
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  if (phase === "done" && doneId) {
    return (
      <div className="rounded-lg border border-green-300 bg-green-50 p-6 text-center">
        <p className="mb-3 font-medium text-green-800">Upload complete!</p>
        <a
          href={`/jobs/${doneId}`}
          className="inline-block rounded bg-green-700 px-4 py-2 text-sm font-medium text-white hover:bg-green-800"
        >
          View job →
        </a>
        <button
          type="button"
          onClick={reset}
          className="ml-3 text-sm text-green-700 underline hover:text-green-900"
        >
          Upload another
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Language select */}
      <div>
        <label
          htmlFor="language-select"
          className="mb-1 block text-sm font-medium text-gray-700"
        >
          Language
        </label>
        <select
          id="language-select"
          value={lang}
          onChange={(e) => setLang(e.target.value)}
          disabled={busy}
          className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none disabled:opacity-50"
        >
          {config.languages.map((l) => (
            <option key={l.code} value={l.code}>
              {l.label}
            </option>
          ))}
        </select>
      </div>

      {/* Dropzone */}
      <div>
        <label
          htmlFor="file-input"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          className={[
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-10 text-sm transition-colors",
            busy
              ? "cursor-not-allowed border-gray-200 bg-gray-50 text-gray-400"
              : "border-gray-300 bg-white text-gray-500 hover:border-blue-400 hover:bg-blue-50",
          ].join(" ")}
        >
          <svg
            aria-hidden="true"
            className="h-8 w-8 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
            />
          </svg>
          <span>
            {file
              ? file.name
              : "Drop an audio file here, or click to browse"}
          </span>
          <input
            id="file-input"
            ref={fileInputRef}
            type="file"
            accept={config.extensions.join(",")}
            disabled={busy}
            className="sr-only"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) pickFile(f);
            }}
          />
        </label>
      </div>

      {/* Progress bar (uploading phase only) */}
      {phase === "uploading" && (
        <div>
          <div className="mb-1 flex justify-between text-xs text-gray-600">
            <span>Uploading…</span>
            <span>{progress}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-gray-200">
            <div
              className="h-2 rounded-full bg-blue-500 transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {/* Busy message (non-upload phases) */}
      {busy && phase !== "uploading" && (
        <p className="text-sm text-gray-500">{busyMsg}</p>
      )}

      {/* Error panel */}
      {error && (
        <div
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          <p className="mb-2">{error}</p>
          <button
            type="button"
            onClick={reset}
            className="rounded bg-red-700 px-3 py-1 text-xs font-medium text-white hover:bg-red-800"
          >
            Try again
          </button>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex gap-3">
        <button
          type="submit"
          disabled={busy || !file}
          className="flex-1 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? busyMsg || "Working…" : "Upload"}
        </button>
        {busy && (
          <button
            type="button"
            onClick={handleCancel}
            className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
