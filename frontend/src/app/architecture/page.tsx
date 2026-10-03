import React from 'react';
import { sections } from './content';

function renderInline(text: string) {
  return text.split('`').map((segment, i) =>
    i % 2 === 1 ? (
      <code key={i} className="bg-gray-100 rounded px-1.5 py-0.5 text-sm font-mono text-gray-800">
        {segment}
      </code>
    ) : (
      <React.Fragment key={i}>{segment}</React.Fragment>
    )
  );
}

export default function ArchitecturePage() {
  return (
    <main className="max-w-3xl mx-auto px-4 py-8 md:py-12 w-full">
      <h1 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">Architecture</h1>
      <p className="text-gray-600 mb-8 text-lg">
        An overview of the design and architecture of the Audio Notes platform.
      </p>

      <div className="flex flex-wrap gap-4 mb-10">
        <a 
          href="https://github.com/kavyamtomriddle/audio-notes"
          target="_blank"
          rel="noopener noreferrer" 
          className="text-blue-600 hover:text-blue-800 hover:underline font-medium flex items-center"
        >
          GitHub repo
        </a>
        <a 
          href="https://audio-notes-red.vercel.app"
          target="_blank"
          rel="noopener noreferrer" 
          className="text-blue-600 hover:text-blue-800 hover:underline font-medium flex items-center"
        >
          Live app
        </a>
      </div>

      <nav className="bg-white border border-gray-200 rounded-xl p-6 mb-12 shadow-sm">
        <h2 className="text-xl font-semibold mb-4 text-gray-900">Contents</h2>
        <ul className="space-y-2.5">
          {sections.map(s => (
            <li key={s.id}>
              <a href={`#${s.id}`} className="text-blue-600 hover:text-blue-800 hover:underline">
                {s.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <div className="space-y-12">
        {sections.map(s => (
          <section key={s.id} id={s.id} className="scroll-mt-24">
            <h2 className="text-2xl font-bold text-gray-900 mb-5">{s.title}</h2>
            
            <div className="space-y-5 text-gray-700 leading-relaxed">
              {s.paragraphs.map((p, i) => (
                <p key={i}>{renderInline(p)}</p>
              ))}
            </div>

            {s.steps && s.steps.length > 0 && (
              <ol className="list-decimal pl-5 mt-5 space-y-2.5 text-gray-700 leading-relaxed">
                {s.steps.map((step, i) => (
                  <li key={i} className="pl-1">{renderInline(step)}</li>
                ))}
              </ol>
            )}
          </section>
        ))}
      </div>
    </main>
  );
}
