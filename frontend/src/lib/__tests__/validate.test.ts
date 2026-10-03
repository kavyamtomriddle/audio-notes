/**
 * Tests for src/lib/validate.ts
 *
 * Test names are EXACT as specified in Context.md Phase F2a.
 * Config values come from the real api_config.json fixture.
 */

import { describe, it, expect } from "vitest";
import { validateFile } from "../validate";
import type { Config } from "../types";

// Real config from the api_config.json fixture
import configFixture from "./fixtures/api_config.json";

// Cast the fixture to Config (fields match exactly)
const config = configFixture as Config;

/** Build a minimal File-like object for testing (no real browser required). */
function makeFile(name: string, sizeBytes: number): File {
  // Create a Blob of the right size and wrap it as a File
  const blob = new Blob([new Uint8Array(sizeBytes)]);
  return new File([blob], name);
}

describe("validate", () => {
  it("rejects_oversize", () => {
    // config.max_upload_bytes = 52428800 (50 MB)
    const file = makeFile("audio.mp3", config.max_upload_bytes + 1);
    const err = validateFile(file, config);
    expect(err).not.toBeNull();
    expect(err!.code).toBe("FILE_TOO_LARGE");
  });

  it("rejects_unknown_extension", () => {
    const file = makeFile("audio.xyz", 1000);
    const err = validateFile(file, config);
    expect(err).not.toBeNull();
    expect(err!.code).toBe("UNSUPPORTED_FORMAT");
  });

  it("accepts_allowed_extension_case_insensitive", () => {
    // "MP3" should be accepted even though config stores "mp3"
    const fileUpper = makeFile("AUDIO.MP3", 1000);
    expect(validateFile(fileUpper, config)).toBeNull();

    // Lowercase also fine
    const fileLower = makeFile("audio.mp3", 1000);
    expect(validateFile(fileLower, config)).toBeNull();

    // Mixed case
    const fileMixed = makeFile("audio.Mp3", 500);
    expect(validateFile(fileMixed, config)).toBeNull();
  });

  it("rejects_empty_file", () => {
    const file = makeFile("audio.mp3", 0);
    const err = validateFile(file, config);
    expect(err).not.toBeNull();
    expect(err!.code).toBe("EMPTY_FILE");
  });

  it("rejects_no_extension", () => {
    // A file with no dot in the name → extension is ""
    const file = makeFile("audiofile", 1000);
    const err = validateFile(file, config);
    expect(err).not.toBeNull();
    expect(err!.code).toBe("UNSUPPORTED_FORMAT");
  });
});
