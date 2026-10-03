/**
 * Audio duration hint via HTMLAudioElement + object URL.
 *
 * readDurationHint(file, timeoutMs?) — resolves with the duration in seconds
 * (rounded to 1 decimal), or null on error, timeout, NaN, or Infinity.
 *
 * The object URL is always revoked, even on error.
 * No printing to console — errors are silent (return null).
 */

// ---------------------------------------------------------------------------
// readDurationHint
// ---------------------------------------------------------------------------

/**
 * Attempt to read the audio duration from a File using HTMLAudioElement.
 *
 * @param file       The audio File to measure.
 * @param timeoutMs  Maximum ms to wait for metadata (default 3000).
 * @returns          Duration in seconds (number), or null on any failure.
 */
export function readDurationHint(
  file: File,
  timeoutMs = 3000,
): Promise<number | null> {
  return new Promise<number | null>((resolve) => {
    let objectUrl: string | null = null;
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined = undefined;

    function finish(result: number | null): void {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (objectUrl !== null) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
      resolve(result);
    }

    // Set timeout guard before doing any async work
    timer = setTimeout(() => finish(null), timeoutMs);

    try {
      objectUrl = URL.createObjectURL(file);
    } catch {
      finish(null);
      return;
    }

    const audio = new Audio();

    audio.onloadedmetadata = () => {
      const d = audio.duration;
      if (!isFinite(d) || isNaN(d)) {
        finish(null);
      } else {
        // Round to 1 decimal place
        finish(Math.round(d * 10) / 10);
      }
    };

    audio.onerror = () => {
      finish(null);
    };

    // Assign src after wiring listeners so we don't miss a fast event.
    audio.src = objectUrl;
    // Explicitly load to trigger metadata fetch in all browsers.
    audio.load();
  });
}
