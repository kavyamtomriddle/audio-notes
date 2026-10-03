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
    <main className="flex min-h-screen flex-col items-center bg-gray-50 px-4 py-12">
      <div className="w-full max-w-lg space-y-6">
        <HealthBanner health={health} configError={configError} />

        {configLoading && (
          <p className="text-sm text-gray-500">Loading settings…</p>
        )}

        {config && <UploadForm config={config} onCreated={handleCreated} />}
        
        <HistoryList refreshKey={refreshKey} />
      </div>
    </main>
  );
}
