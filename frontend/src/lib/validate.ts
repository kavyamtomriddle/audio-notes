/**
 * Client-side file validation.
 *
 * fileExtension(name)         — lowercased extension without leading dot, or "" if none.
 * validateFile(file, config)  — returns {code, message} for the first violation, or null.
 */

import type { Config } from "./types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return the lowercased extension without the leading dot, or "" if none. */
export function fileExtension(name: string): string {
  const idx = name.lastIndexOf(".");
  if (idx < 0 || idx === name.length - 1) return "";
  return name.slice(idx + 1).toLowerCase();
}

// ---------------------------------------------------------------------------
// Validation result
// ---------------------------------------------------------------------------

export interface ValidationError {
  code: string;
  message: string;
}

// ---------------------------------------------------------------------------
// validateFile
// ---------------------------------------------------------------------------

/**
 * Validate a File against the server config.
 *
 * Checks (in order):
 *   1. Empty file (size === 0)
 *   2. File size > config.max_upload_bytes
 *   3. Missing or disallowed extension (case-insensitive, list from config.extensions)
 *
 * Returns a ValidationError on the first violation, or null when the file is valid.
 * Messages state the actual limits taken from config.
 */
export function validateFile(
  file: File,
  config: Config,
): ValidationError | null {
  // 1. Empty file
  if (file.size === 0) {
    return {
      code: "EMPTY_FILE",
      message: "The selected file is empty. Please choose a non-empty audio file.",
    };
  }

  // 2. File too large
  if (file.size > config.max_upload_bytes) {
    const limitMB = (config.max_upload_bytes / (1024 * 1024)).toFixed(0);
    const fileMB = (file.size / (1024 * 1024)).toFixed(1);
    return {
      code: "FILE_TOO_LARGE",
      message: `File is too large (${fileMB} MB). The maximum allowed size is ${limitMB} MB.`,
    };
  }

  // 3. Missing or disallowed extension
  const ext = fileExtension(file.name);
  const allowed = config.extensions.map((e) => e.toLowerCase());
  if (!ext || !allowed.includes(ext)) {
    const list = config.extensions.join(", ");
    const displayExt = ext || "(none)";
    return {
      code: "UNSUPPORTED_FORMAT",
      message: `File extension "${displayExt}" is not supported. Allowed formats: ${list}.`,
    };
  }

  return null;
}
