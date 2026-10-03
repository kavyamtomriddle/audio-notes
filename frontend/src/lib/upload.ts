/**
 * Direct-to-Supabase file upload via XMLHttpRequest PUT.
 *
 * Headers match backend/scripts/smoke_upload.py EXACTLY:
 *   Content-Type: <file.type> (fallback "application/octet-stream")
 *
 * The file is sent as the raw body (not FormData).
 * Progress is reported only when lengthComputable.
 * The signed URL is never included in error messages.
 */

// ---------------------------------------------------------------------------
// UploadError
// ---------------------------------------------------------------------------

export type UploadErrorKind = "http" | "network" | "abort";

export class UploadError extends Error {
  readonly kind: UploadErrorKind;
  readonly status: number | undefined;

  constructor(message: string, kind: UploadErrorKind, status?: number) {
    super(message);
    this.name = "UploadError";
    this.kind = kind;
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// uploadFile
// ---------------------------------------------------------------------------

export interface UploadFileOptions {
  /** Supabase signed upload URL. */
  url: string;
  /** The file to upload. */
  file: File;
  /** Progress callback: called with (loaded, total) bytes. */
  onProgress?: (loaded: number, total: number) => void;
  /** AbortSignal — when aborted, xhr.abort() is called. */
  signal?: AbortSignal;
}

export function uploadFile(options: UploadFileOptions): Promise<void> {
  const { url, file, onProgress, signal } = options;

  return new Promise<void>((resolve, reject) => {
    // If already aborted before we start, bail immediately
    if (signal?.aborted) {
      reject(new UploadError("Upload aborted", "abort"));
      return;
    }

    const xhr = new XMLHttpRequest();

    // No XHR-level timeout — let the caller control via signal
    xhr.timeout = 0;

    // Wire up caller's AbortSignal
    const onAbort = () => {
      xhr.abort();
    };
    signal?.addEventListener("abort", onAbort, { once: true });

    // Progress (upload direction only, only when computable)
    if (onProgress) {
      xhr.upload.onprogress = (e: ProgressEvent) => {
        if (e.lengthComputable) {
          onProgress(e.loaded, e.total);
        }
      };
    }

    xhr.onload = () => {
      signal?.removeEventListener("abort", onAbort);
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(
          new UploadError(
            `Upload failed with status ${xhr.status}`,
            "http",
            xhr.status,
          ),
        );
      }
    };

    xhr.onerror = () => {
      signal?.removeEventListener("abort", onAbort);
      reject(new UploadError("Upload failed — check your connection", "network"));
    };

    xhr.onabort = () => {
      signal?.removeEventListener("abort", onAbort);
      reject(new UploadError("Upload aborted", "abort"));
    };

    xhr.open("PUT", url);

    // Content-Type matches smoke_upload.py: raw body with explicit Content-Type.
    // Fallback to application/octet-stream if file.type is empty.
    xhr.setRequestHeader(
      "Content-Type",
      file.type || "application/octet-stream",
    );

    xhr.send(file);
  });
}
