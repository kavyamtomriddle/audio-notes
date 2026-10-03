/**
 * Frontend types mirroring backend/app/schemas.py EXACTLY.
 *
 * Field names, types and nullability are copied verbatim from the
 * Pydantic models and the real API fixtures in docs/fixtures/api_*.json.
 */

// -- Status unions (from DB CHECK constraints, Context.md §5) --

export type JobStatus =
  | "awaiting_upload"
  | "queued"
  | "transcribing"
  | "summarizing"
  | "completed"
  | "failed";

export type SummaryStatus = "pending" | "done" | "failed";

// -- GET /api/config (schemas.py ConfigResponse) --

export interface LanguageOption {
  code: string;
  label: string;
}

export interface Config {
  languages: LanguageOption[];
  extensions: string[];
  max_upload_bytes: number;
  max_duration_hint_s: number;
  est_ratio: number;
}

// -- POST /api/jobs/initiate (schemas.py InitiateRequest / InitiateResponse) --

export interface InitiateRequest {
  filename: string;
  size_bytes: number;
  content_type: string | null;
  duration_hint_s: number | null;
  language_code: string;
}

export interface InitiateResponse {
  id: string; // UUID serialized as string in JSON
  upload_url: string;
  expires_in: number;
}

// -- GET /api/jobs/{id} (schemas.py JobDetail) --

export interface Job {
  id: string;
  session_id: string;
  filename: string;
  size_bytes: number;
  content_type: string | null;
  language_code: string;
  duration_hint_s: number | null;
  status: JobStatus;
  gnani_status: string | null;
  summary_status: SummaryStatus;
  transcript: string | null;
  summary: string | null;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean | null;
  summary_error: string | null;
  created_at: string; // ISO 8601 e.g. "2026-10-03T10:04:44.119461Z"
  queued_at: string | null;
  processing_started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

// -- GET /api/jobs list item (schemas.py JobListItem) --

export interface JobListItem {
  id: string;
  filename: string;
  size_bytes: number;
  language_code: string;
  duration_hint_s: number | null;
  status: JobStatus;
  gnani_status: string | null;
  summary_status: SummaryStatus;
  summary_snippet: string | null;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean | null;
  created_at: string;
  completed_at: string | null;
  updated_at: string;
}

// -- Error body (schemas.py ErrorResponse, wrapped in FastAPI "detail") --

export interface ApiErrorBody {
  error_code: string;
  message: string;
  retryable: boolean;
}
