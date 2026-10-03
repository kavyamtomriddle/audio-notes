import React from "react";
import { sections } from "./content";
import FlowDiagram from "./FlowDiagram";
import StateDiagram from "./StateDiagram";
import { StatStrip, Callout, ErrorCodeTable, ErrorCodeRow } from "./Panels";

function renderInline(text: string) {
  return text.split("`").map((segment, i) =>
    i % 2 === 1 ? (
      <code
        key={i}
        className="font-mono text-sm bg-gray-100 text-indigo-700 px-1.5 py-0.5 rounded"
      >
        {segment}
      </code>
    ) : (
      <React.Fragment key={i}>{segment}</React.Fragment>
    )
  );
}

// --- Data for the two generated panels below. Edit freely; this is new
// content, not part of the fact-checked docs/architecture.md text. ---

const VERIFICATION_STATS = [
  { value: "~150", label: "mocked unit tests" },
  { value: "18", label: "integration tests (real Postgres)" },
  { value: "0.13x", label: "transcription time / audio length" },
  { value: "50 MB", label: "per-file storage limit" },
];

const ERROR_CODES: ErrorCodeRow[] = [
  { code: "FILE_TOO_LARGE", retryable: false, meaning: "File exceeds the 50 MB limit; rejected at /initiate before any upload." },
  { code: "UNSUPPORTED_FORMAT", retryable: false, meaning: "Extension not in the allow-list; rejected at /initiate." },
  { code: "RATE_LIMITED_SESSION", retryable: false, meaning: "This browser session has hit its daily upload cap." },
  { code: "DAILY_CAP_REACHED", retryable: false, meaning: "The platform-wide daily upload cap has been hit." },
  { code: "NO_SPEECH_DETECTED", retryable: false, meaning: "Gnani returned an empty transcript (silence or music only)." },
  { code: "CORRUPT_OR_UNREADABLE_AUDIO", retryable: false, meaning: "Gnani could not decode the file." },
  { code: "PROVIDER_AUTH", retryable: false, meaning: "Our Gnani or Gemini API key was rejected." },
  { code: "PROVIDER_RATE_LIMITED", retryable: true, meaning: "Gnani's /files endpoint returned 429 after 8 backoff attempts." },
  { code: "PROVIDER_ERROR", retryable: true, meaning: "An unclassified Gnani failure; safe to retry." },
  { code: "PROVIDER_TIMEOUT", retryable: true, meaning: "The job sat past TRANSCRIBE_TIMEOUT_S with no update." },
  { code: "WORKER_STUCK", retryable: true, meaning: "A job was claimed more than MAX_CLAIMS times without progress." },
];

// Which section (matched by a substring of its title, case-insensitive) gets
// which extra panel, and where ("before" or "after" the prose).
// Your content.ts is the one file I haven't seen — if a panel doesn't show
// up, the title substring below didn't match your actual section title.
const SECTION_EXTRAS: {
  titleIncludes: string;
  position: "before" | "after";
  render: () => React.ReactNode;
}[] = [
  { titleIncludes: "sync vs background", position: "after", render: () => <StateDiagram /> },
  { titleIncludes: "how it was verified", position: "before", render: () => <StatStrip stats={VERIFICATION_STATS} /> },
  { titleIncludes: "failure handling", position: "after", render: () => <ErrorCodeTable rows={ERROR_CODES} /> },
];

const CALLOUT_TITLES = ["known limitations"]; // wraps paragraphs of matching sections in an amber callout

export default function ArchitecturePage() {
  return (
    <main className="max-w-3xl mx-auto px-6 py-12 w-full">
      <h1 className="text-4xl font-bold tracking-tight text-gray-900 mb-3">Architecture</h1>
      <p className="text-lg text-gray-500 mb-8">
        An overview of the design and architecture of the Audio Notes platform.
      </p>

      <div className="inline-flex gap-6 mb-12">
        <a
          href="https://github.com/kavyamtomriddle/audio-notes"
          target="_blank"
          rel="noopener noreferrer"
          className="text-indigo-600 hover:text-indigo-800 font-medium text-sm underline-offset-4 hover:underline flex items-center"
        >
          GitHub repo
        </a>
        <a
          href="https://audio-notes-red.vercel.app"
          target="_blank"
          rel="noopener noreferrer"
          className="text-indigo-600 hover:text-indigo-800 font-medium text-sm underline-offset-4 hover:underline flex items-center"
        >
          Live app
        </a>
      </div>

      <div className="mb-12">
        <h2 className="text-2xl font-semibold text-gray-900 mb-4 pb-2 border-b border-gray-100">
          System flow
        </h2>
        <FlowDiagram />
      </div>

      <nav className="bg-gray-50 border border-gray-200 rounded-xl p-6 mb-12">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">
          Contents
        </h2>
        <ul className="space-y-0">
          {sections.map((s) => (
            <li key={s.id}>
              <a
                href={`#${s.id}`}
                className="text-indigo-600 hover:underline text-sm leading-8"
              >
                {s.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <div>
        {sections.map((s) => {
          const extra = SECTION_EXTRAS.find((e) =>
            s.title.toLowerCase().includes(e.titleIncludes)
          );
          const callout = CALLOUT_TITLES.some((t) =>
            s.title.toLowerCase().includes(t)
          );

          const paragraphBlock = callout ? (
            <Callout title={s.title}>
              {s.paragraphs.map((p, i) => (
                <p key={i}>{renderInline(p)}</p>
              ))}
            </Callout>
          ) : (
            <div className="mb-4">
              {s.paragraphs.map((p, i) => (
                <p key={i} className="text-gray-700 leading-relaxed text-base mb-4">
                  {renderInline(p)}
                </p>
              ))}
            </div>
          );

          return (
            <section key={s.id} id={s.id} className="scroll-mt-24 mb-12">
              <h2 className="text-2xl font-semibold text-gray-900 mb-4 pb-2 border-b border-gray-100">
                {s.title}
              </h2>

              {extra?.position === "before" && (
                <div className="mb-4">{extra.render()}</div>
              )}

              {paragraphBlock}

              {s.steps && s.steps.length > 0 && (
                <ol className="list-decimal list-inside space-y-2 text-gray-700 text-base leading-relaxed bg-gray-50 rounded-lg p-4">
                  {s.steps.map((step, i) => (
                    <li key={i}>{renderInline(step)}</li>
                  ))}
                </ol>
              )}

              {extra?.position === "after" && (
                <div className="mt-4">{extra.render()}</div>
              )}
            </section>
          );
        })}
      </div>
    </main>
  );
}
