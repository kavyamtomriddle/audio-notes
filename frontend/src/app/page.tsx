"use client";

import { useEffect, useState } from "react";

type HealthStatus = "loading" | "ok" | "waking";

export default function Home() {
  const [status, setStatus] = useState<HealthStatus>("loading");

  useEffect(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!apiUrl) {
      console.error("NEXT_PUBLIC_API_URL is not set");
      setStatus("waking");
      return;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => {
      // If response takes > 3 s, show "waking up" while we keep waiting
      setStatus("waking");
    }, 3000);

    fetch(`${apiUrl}/health`, { signal: controller.signal })
      .then((res) => {
        clearTimeout(timer);
        if (res.ok) setStatus("ok");
        else setStatus("waking");
      })
      .catch(() => {
        clearTimeout(timer);
        setStatus("waking");
      });

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50">
      <div className="rounded-lg bg-white p-8 shadow-md text-center">
        <h1 className="text-2xl font-bold mb-4">Audio Notes</h1>
        {status === "loading" && (
          <p className="text-gray-500">Checking backend…</p>
        )}
        {status === "ok" && (
          <p className="text-green-600 font-medium">Backend: ok</p>
        )}
        {status === "waking" && (
          <p className="text-amber-600 font-medium">
            Backend: waking up… (free tier, up to ~1 min)
          </p>
        )}
      </div>
    </main>
  );
}
