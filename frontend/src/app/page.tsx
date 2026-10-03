"use client";
/**
 * Home page — Phase F1c.
 *
 * Shows a HealthBanner while the server is not ready.
 * Waits for config to load ("Loading settings…"), then renders UploadForm.
 */

import { useHealth } from "../hooks/useHealth";
import { useConfig } from "../hooks/useConfig";
import { HealthBanner } from "../components/HealthBanner";
import { UploadForm } from "../components/UploadForm";

export default function Home() {
  const health = useHealth();
  const { config, error: configError, loading: configLoading } = useConfig();

  return (
    <main className="flex min-h-screen flex-col items-center bg-gray-50 px-4 py-12">
      <div className="w-full max-w-lg space-y-6">
        <h1 className="text-2xl font-bold text-gray-900">Audio Notes</h1>

        <HealthBanner health={health} configError={configError} />

        {configLoading && (
          <p className="text-sm text-gray-500">Loading settings…</p>
        )}

        {config && <UploadForm config={config} />}
      </div>
    </main>
  );
}
