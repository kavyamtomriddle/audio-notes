"use client";
/**
 * Home page — Phase F1c & F3.
 *
 * Shows a HealthBanner while the server is not ready.
 * Waits for config to load ("Loading settings…"), then renders UploadForm.
 * Shows HistoryList below the UploadForm.
 */

import { useState } from "react";
import { useHealth } from "../hooks/useHealth";
import { useConfig } from "../hooks/useConfig";
import { HealthBanner } from "../components/HealthBanner";
import { UploadForm } from "../components/UploadForm";
import { HistoryList } from "../components/HistoryList";

export default function Home() {
  const health = useHealth();
  const { config, error: configError, loading: configLoading } = useConfig();
  const [refreshKey, setRefreshKey] = useState(0);

  const handleCreated = () => {
    setRefreshKey((prev) => prev + 1);
  };

  return (
    <main className="mx-auto w-full max-w-2xl px-4 py-10 space-y-6">
      <HealthBanner health={health} configError={configError} />

      <div>
        <h1 className="text-xl font-semibold text-gray-900 mb-1">Upload Audio</h1>
        <p className="text-sm text-gray-500">
          Upload an audio file to get a transcript and AI summary.
        </p>
      </div>

      {configLoading && (
        <p className="text-sm text-gray-500">Loading settings…</p>
      )}

      {config && <UploadForm config={config} onCreated={handleCreated} />}

      <HistoryList refreshKey={refreshKey} />
    </main>
  );
}
