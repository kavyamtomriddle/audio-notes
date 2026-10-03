import React from 'react';
import { sections } from './content';

function renderInline(text: string) {
  return text.split('`').map((segment, i) =>
    i % 2 === 1 ? (
      <code key={i} className="font-mono text-sm bg-gray-100 text-indigo-700 px-1.5 py-0.5 rounded">
        {segment}
      </code>
    ) : (
      <React.Fragment key={i}>{segment}</React.Fragment>
    )
  );
}

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
        <h2 className="text-2xl font-semibold text-gray-900 mb-4 pb-2 border-b border-gray-100">System Flow</h2>
        <ol className="relative border-l-2 border-indigo-200 ml-4 pl-6 space-y-6">
          {[
            "Browser validates file and calls /api/jobs/initiate",
            "Backend issues a Supabase signed upload URL (3 h expiry)",
            "Browser PUTs audio directly to Supabase via XMLHttpRequest",
            "Browser calls /api/jobs/{id}/complete — backend verifies object exists",
            "Worker claims job (FOR UPDATE SKIP LOCKED, 120 s lease)",
            "Worker creates + starts Gnani Batch STT job, commits gnani_job_id",
            "Worker polls Gnani every 10 s — on COMPLETED fetches transcript, deletes audio",
            "Worker calls Gemini to summarize — job marked completed",
          ].map((stepText, i) => (
            <li key={i} className="relative before:absolute before:-left-[25px] before:top-1 before:w-4 before:h-4 before:rounded-full before:bg-indigo-500 before:border-2 before:border-white">
              <div className="text-xs font-mono text-indigo-400 mb-0.5">Step {i + 1}</div>
              <div className="text-gray-700 text-sm leading-snug">{stepText}</div>
            </li>
          ))}
        </ol>
      </div>

      <nav className="bg-gray-50 border border-gray-200 rounded-xl p-6 mb-12">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Contents</h2>
        <ul className="space-y-0">
          {sections.map(s => (
            <li key={s.id}>
              <a href={`#${s.id}`} className="text-indigo-600 hover:underline text-sm leading-8">
                {s.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <div>
        {sections.map(s => (
          <section key={s.id} id={s.id} className="scroll-mt-24 mb-12">
            <h2 className="text-2xl font-semibold text-gray-900 mb-4 pb-2 border-b border-gray-100">{s.title}</h2>
            
            <div className="mb-4">
              {s.paragraphs.map((p, i) => (
                <p key={i} className="text-gray-700 leading-relaxed text-base mb-4">{renderInline(p)}</p>
              ))}
            </div>

            {s.steps && s.steps.length > 0 && (
              <ol className="list-decimal list-inside space-y-2 text-gray-700 text-base leading-relaxed bg-gray-50 rounded-lg p-4">
                {s.steps.map((step, i) => (
                  <li key={i}>{renderInline(step)}</li>
                ))}
              </ol>
            )}
          </section>
        ))}
      </div>
    </main>
  );
}
