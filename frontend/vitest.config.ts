/**
 * Vitest configuration for the Audio Notes frontend.
 *
 * Uses jsdom environment so browser globals (File, Blob, XMLHttpRequest, etc.)
 * are available in tests.
 *
 * Includes only files under src/lib/__tests__/ so Next.js component tests
 * (which would need a full React/Next setup) are excluded for now.
 */

import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/lib/__tests__/**/*.test.ts"],
    // Resolve the @/* path alias from tsconfig.json
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
