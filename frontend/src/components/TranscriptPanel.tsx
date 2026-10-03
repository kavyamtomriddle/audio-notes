interface TranscriptPanelProps {
  transcript: string | null;
}

export function TranscriptPanel({ transcript }: TranscriptPanelProps) {
  if (!transcript) return null;

  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-200 bg-gray-50 px-6 py-4">
        <h2 className="text-lg font-semibold text-gray-900">Transcript</h2>
        <p className="text-sm text-gray-500">
          Raw speech-to-text output: no punctuation, may contain mistakes
        </p>
      </div>
      <div className="max-h-96 overflow-y-auto px-6 py-4">
        <div className="whitespace-pre-wrap text-sm text-gray-700">
          {transcript}
        </div>
      </div>
    </div>
  );
}
