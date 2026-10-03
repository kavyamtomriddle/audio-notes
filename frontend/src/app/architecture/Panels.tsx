// Three small presentational helpers. No "use client" needed — all static markup.

export function StatStrip({
  stats,
}: {
  stats: { value: string; label: string }[];
}) {
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-gray-200 bg-gray-200 sm:grid-cols-4">
      {stats.map((s, i) => (
        <div key={i} className="bg-white px-4 py-3">
          <div className="font-mono text-lg font-semibold text-gray-900">{s.value}</div>
          <div className="text-xs text-gray-500">{s.label}</div>
        </div>
      ))}
    </div>
  );
}

export function Callout({
  tone = "amber",
  title,
  children,
}: {
  tone?: "amber" | "slate";
  title: string;
  children: React.ReactNode;
}) {
  const styles =
    tone === "amber"
      ? "border-amber-200 bg-amber-50"
      : "border-slate-200 bg-slate-50";
  const titleColor = tone === "amber" ? "text-amber-900" : "text-slate-900";
  return (
    <div className={`rounded-xl border ${styles} p-4 sm:p-5`}>
      <div className={`mb-1.5 text-sm font-semibold ${titleColor}`}>{title}</div>
      <div className="space-y-2 text-sm leading-relaxed text-gray-700">{children}</div>
    </div>
  );
}

export type ErrorCodeRow = { code: string; retryable: boolean; meaning: string };

export function ErrorCodeTable({ rows }: { rows: ErrorCodeRow[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-gray-200">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
            <th className="px-4 py-2 font-medium">Code</th>
            <th className="px-4 py-2 font-medium">Retryable</th>
            <th className="px-4 py-2 font-medium">Meaning</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={r.code}
              className={i % 2 === 1 ? "bg-gray-50/50" : undefined}
            >
              <td className="whitespace-nowrap px-4 py-2 align-top font-mono text-[12.5px] text-indigo-700">
                {r.code}
              </td>
              <td className="whitespace-nowrap px-4 py-2 align-top text-xs">
                {r.retryable ? (
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-700">
                    yes
                  </span>
                ) : (
                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-gray-500">
                    no
                  </span>
                )}
              </td>
              <td className="px-4 py-2 align-top text-gray-700">{r.meaning}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
