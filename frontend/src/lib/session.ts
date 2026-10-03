/**
 * Anonymous session ID management.
 *
 * SSR-safe: reads/writes localStorage only in the browser.
 * Key: "audio-notes-session-id". Value: UUID v4.
 * Falls back to an in-memory ID when localStorage throws (e.g. private browsing).
 */

const STORAGE_KEY = "audio-notes-session-id";

/** In-memory fallback when localStorage is unavailable. */
let memoryId: string | null = null;

/**
 * Generate a UUID v4 using crypto.randomUUID() with a
 * crypto.getRandomValues() fallback for older browsers.
 */
function generateUUID(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback: RFC 4122 version 4 UUID via getRandomValues
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  // Set version (4) and variant (10xx) bits
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20, 32),
  ].join("-");
}

/**
 * Returns the session ID, creating one if needed.
 * SSR-safe: returns a stable in-memory ID on the server.
 */
export function getSessionId(): string {
  // SSR guard: no window means no localStorage
  if (typeof window === "undefined") {
    if (!memoryId) {
      memoryId = generateUUID();
    }
    return memoryId;
  }

  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return stored;

    const id = generateUUID();
    localStorage.setItem(STORAGE_KEY, id);
    return id;
  } catch {
    // localStorage unavailable (private browsing, storage full, etc.)
    if (!memoryId) {
      memoryId = generateUUID();
    }
    return memoryId;
  }
}
