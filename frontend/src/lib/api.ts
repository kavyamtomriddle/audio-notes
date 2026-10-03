/**
 * API client for the Audio Notes backend.
 *
 * Base URL from process.env.NEXT_PUBLIC_API_URL (checked at call time, not import).
 * JSON in/out, X-Session-Id header on session-scoped routes.
 * Error body parsing handles both top-level and FastAPI "detail"-wrapped shapes.
 */

import { getSessionId } from "./session";
import type {
  Config,
  InitiateRequest,
  InitiateResponse,
  Job,
  JobListItem,
} from "./types";

// ---------------------------------------------------------------------------
// ApiError
// ---------------------------------------------------------------------------

export type ApiErrorKind = "http" | "network" | "timeout";

export class ApiError extends Error {
  readonly status: number;
  readonly errorCode: string | null;
  readonly retryable: boolean;
  readonly kind: ApiErrorKind;

  constructor(
    message: string,
    status: number,
    errorCode: string | null,
    retryable: boolean,
    kind: ApiErrorKind,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errorCode = errorCode;
    this.retryable = retryable;
    this.kind = kind;
  }
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function getBaseUrl(): string {
  const url = process.env.NEXT_PUBLIC_API_URL;
  if (!url) {
    throw new ApiError(
      "API URL is not configured (NEXT_PUBLIC_API_URL is missing)",
      0,
      null,
      false,
      "network",
    );
  }
  return url;
}

/**
 * Parse the backend error body.
 * FastAPI wraps HTTPException bodies in "detail", so we check both:
 *   {"detail": {"error_code", "message", "retryable"}}
 *   {"error_code", "message", "retryable"}
 * Falls back to a safe generic message on any parse failure.
 */
function parseErrorBody(
  json: unknown,
  status: number,
): { message: string; errorCode: string | null; retryable: boolean } {
  const fallback = {
    message: `HTTP ${status}`,
    errorCode: null as string | null,
    retryable: false,
  };

  if (typeof json !== "object" || json === null) return fallback;

  // Unwrap FastAPI "detail" wrapper if present
  const record = json as Record<string, unknown>;
  const body =
    typeof record.detail === "object" && record.detail !== null
      ? (record.detail as Record<string, unknown>)
      : record;

  const errorCode =
    typeof body.error_code === "string" ? body.error_code : null;
  const message =
    typeof body.message === "string" ? body.message : `HTTP ${status}`;
  const retryable =
    typeof body.retryable === "boolean" ? body.retryable : false;

  return { message, errorCode, retryable };
}

// ---------------------------------------------------------------------------
// Core request
// ---------------------------------------------------------------------------

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Add X-Session-Id header. Default: true. */
  session?: boolean;
  signal?: AbortSignal;
  /** Own timeout in ms. 0 = no timeout. */
  timeoutMs?: number;
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const {
    method = "GET",
    body,
    session = true,
    signal,
    timeoutMs = 0,
  } = options;

  const baseUrl = getBaseUrl(); // throws ApiError if missing

  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (session) {
    headers["X-Session-Id"] = getSessionId();
  }

  // Timeout via AbortController; compose with caller's signal
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  let combinedSignal = signal;

  const timeoutController = timeoutMs > 0 ? new AbortController() : null;
  if (timeoutController) {
    // If caller also passed a signal, we need to listen on both
    if (signal) {
      const combined = new AbortController();
      const onAbort = () => combined.abort();
      signal.addEventListener("abort", onAbort, { once: true });
      timeoutController.signal.addEventListener("abort", onAbort, {
        once: true,
      });
      combinedSignal = combined.signal;
    } else {
      combinedSignal = timeoutController.signal;
    }
    timeoutId = setTimeout(() => timeoutController.abort(), timeoutMs);
  }

  let res: Response;
  try {
    res = await fetch(`${baseUrl}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: combinedSignal,
    });
  } catch (err: unknown) {
    if (timeoutId !== undefined) clearTimeout(timeoutId);

    // Caller abort → rethrow untouched so hooks can ignore it
    if (signal?.aborted) {
      throw err;
    }
    // Our own timeout
    if (timeoutController?.signal.aborted) {
      throw new ApiError(
        "Request timed out",
        0,
        null,
        true,
        "timeout",
      );
    }
    // Network failure
    throw new ApiError(
      "Network error — check your connection",
      0,
      null,
      true,
      "network",
    );
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }

  // Non-2xx: parse the backend error body
  if (!res.ok) {
    let json: unknown = null;
    try {
      json = await res.json();
    } catch {
      // body wasn't JSON — fall through to default message
    }
    const parsed = parseErrorBody(json, res.status);
    throw new ApiError(
      parsed.message,
      res.status,
      parsed.errorCode,
      parsed.retryable,
      "http",
    );
  }

  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Typed endpoint helpers
// ---------------------------------------------------------------------------

/** GET /health — no session, long timeout for cold start. */
export function health(): Promise<{ ok: boolean }> {
  return request("/health", {
    session: false,
    timeoutMs: 70_000,
  });
}

/** GET /api/config — no session, long timeout for cold start. */
export function getConfig(): Promise<Config> {
  return request("/api/config", {
    session: false,
    timeoutMs: 70_000,
  });
}

/** POST /api/jobs/initiate */
export function initiateJob(body: InitiateRequest): Promise<InitiateResponse> {
  return request("/api/jobs/initiate", {
    method: "POST",
    body,
  });
}

/** POST /api/jobs/{id}/complete */
export function completeJob(id: string): Promise<{ id: string; status: string }> {
  return request(`/api/jobs/${id}/complete`, { method: "POST" });
}

/** GET /api/jobs/{id} */
export function getJob(id: string, signal?: AbortSignal): Promise<Job> {
  return request(`/api/jobs/${id}`, { signal });
}

/** GET /api/jobs — this session's jobs, newest first. */
export function listJobs(signal?: AbortSignal): Promise<JobListItem[]> {
  return request("/api/jobs", { signal });
}

/** POST /api/jobs/{id}/retry */
export function retryJob(id: string): Promise<Job> {
  return request(`/api/jobs/${id}/retry`, {
    method: "POST",
    timeoutMs: 20_000,
  });
}

/** POST /api/jobs/{id}/retry-summary */
export function retrySummary(id: string): Promise<Job> {
  return request(`/api/jobs/${id}/retry-summary`, {
    method: "POST",
    timeoutMs: 20_000,
  });
}
