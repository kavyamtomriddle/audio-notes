// No "use client" — this is static markup, zero JS shipped for it.

type Actor = "browser" | "backend" | "worker" | "gnani" | "gemini";

const ACTOR_STYLE: Record<Actor, { label: string; dot: string; text: string }> = {
  browser: { label: "Browser", dot: "bg-indigo-500", text: "text-indigo-700" },
  backend: { label: "Backend", dot: "bg-slate-500", text: "text-slate-700" },
  worker: { label: "Worker", dot: "bg-violet-500", text: "text-violet-700" },
  gnani: { label: "Gnani", dot: "bg-amber-500", text: "text-amber-700" },
  gemini: { label: "Gemini", dot: "bg-emerald-500", text: "text-emerald-700" },
};

type Step = { actor: Actor; text: string };

const STEPS: Step[] = [
  { actor: "browser", text: "Validates the file and calls `/api/jobs/initiate`" },
  { actor: "backend", text: "Issues a Supabase signed upload URL (3 h expiry)" },
  { actor: "browser", text: "PUTs the audio directly to Supabase via `XMLHttpRequest`" },
  { actor: "backend", text: "Confirms the object exists and marks the job `queued`" },
  { actor: "worker", text: "Claims the job (`FOR UPDATE SKIP LOCKED`, 120 s lease)" },
  { actor: "worker", text: "Creates the Gnani Batch job and commits `gnani_job_id` immediately" },
  { actor: "gnani", text: "Transcribes the audio; worker polls every 10 s" },
  { actor: "worker", text: "Fetches the transcript, stores it, deletes the audio object" },
  { actor: "gemini", text: "Summarizes the transcript; job marked `completed`" },
];

function renderInline(text: string) {
  return text.split("`").map((segment, i) =>
    i % 2 === 1 ? (
      <code
        key={i}
        className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[0.8em] text-indigo-700"
      >
        {segment}
      </code>
    ) : (
      <span key={i}>{segment}</span>
    )
  );
}

export default function FlowDiagram() {
  const actorsInOrder = Array.from(new Set(STEPS.map((s) => s.actor)));

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 sm:p-6">
      {/* Legend */}
      <div className="mb-5 flex flex-wrap gap-x-5 gap-y-2 border-b border-gray-100 pb-4">
        {actorsInOrder.map((a) => (
          <div key={a} className="flex items-center gap-1.5 text-xs text-gray-500">
            <span className={`h-2 w-2 rounded-full ${ACTOR_STYLE[a].dot}`} />
            {ACTOR_STYLE[a].label}
          </div>
        ))}
      </div>

      {/* Pipeline */}
      <ol className="relative ml-1.5 space-y-5 border-l border-gray-200 pl-6">
        {STEPS.map((step, i) => {
          const style = ACTOR_STYLE[step.actor];
          return (
            <li key={i} className="relative">
              <span
                className={`absolute -left-[29px] top-0.5 flex h-4 w-4 items-center justify-center rounded-full border-2 border-white ${style.dot}`}
              />
              <div className={`mb-0.5 text-[11px] font-medium ${style.text}`}>
                {style.label}
              </div>
              <p className="text-sm leading-snug text-gray-700">
                {renderInline(step.text)}
              </p>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
