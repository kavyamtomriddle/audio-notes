// Static SVG — no JS, no deps, renders identically server- and client-side.
// Hand-tuned viewBox; if you rename a status, update the two <text> labels that match it.

const BOX_FILL = "#F8FAFC"; // slate-50
const BOX_STROKE = "#CBD5E1"; // slate-300
const FAIL_FILL = "#FEF2F2"; // red-50
const FAIL_STROKE = "#FCA5A5"; // red-300
const DONE_FILL = "#ECFDF5"; // emerald-50
const DONE_STROKE = "#6EE7B7"; // emerald-300
const ARROW = "#94A3B8"; // slate-400

function Box({
  x,
  y,
  w,
  h,
  label,
  fill = BOX_FILL,
  stroke = BOX_STROKE,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  fill?: string;
  stroke?: string;
}) {
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={6}
        fill={fill}
        stroke={stroke}
        strokeWidth={1}
      />
      <text
        x={x + w / 2}
        y={y + h / 2 + 4}
        textAnchor="middle"
        fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
        fontSize="11"
        fill="#334155"
      >
        {label}
      </text>
    </g>
  );
}

function Arrow({ x1, y1, x2, y2 }: { x1: number; y1: number; x2: number; y2: number }) {
  return (
    <line
      x1={x1}
      y1={y1}
      x2={x2}
      y2={y2}
      stroke={ARROW}
      strokeWidth={1.5}
      markerEnd="url(#arrowhead)"
    />
  );
}

export default function StateDiagram() {
  const row1Y = 20;
  const boxH = 34;
  const boxW = 112;
  const gap = 36;
  const xs = [10, 10 + boxW + gap, 10 + 2 * (boxW + gap), 10 + 3 * (boxW + gap)];
  const failY = 110;
  const failX = xs[1] + boxW / 2 - 56;

  return (
    <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white p-4 sm:p-5">
      <svg
        viewBox="0 0 620 150"
        className="h-auto w-full min-w-[560px]"
        role="img"
        aria-label="State diagram: awaiting_upload to queued to transcribing to summarizing to completed, with a failed state reachable from every stage, and summary_status failed as a sub-state of completed."
      >
        <defs>
          <marker
            id="arrowhead"
            markerWidth="8"
            markerHeight="8"
            refX="6"
            refY="3"
            orient="auto"
          >
            <path d="M0,0 L6,3 L0,6 Z" fill={ARROW} />
          </marker>
        </defs>

        {/* Main sequence */}
        <Box x={xs[0]} y={row1Y} w={boxW} h={boxH} label="awaiting_upload" />
        <Box x={xs[1]} y={row1Y} w={boxW} h={boxH} label="queued" />
        <Box x={xs[2]} y={row1Y} w={boxW} h={boxH} label="transcribing" />
        <Box x={xs[3]} y={row1Y} w={boxW} h={boxH} label="summarizing" />

        <Arrow x1={xs[0] + boxW} y1={row1Y + boxH / 2} x2={xs[1]} y2={row1Y + boxH / 2} />
        <Arrow x1={xs[1] + boxW} y1={row1Y + boxH / 2} x2={xs[2]} y2={row1Y + boxH / 2} />
        <Arrow x1={xs[2] + boxW} y1={row1Y + boxH / 2} x2={xs[3]} y2={row1Y + boxH / 2} />

        {/* completed, off to the right, down from summarizing */}
        <Box
          x={xs[3]}
          y={row1Y + 62}
          w={boxW}
          h={boxH}
          label="completed"
          fill={DONE_FILL}
          stroke={DONE_STROKE}
        />
        <Arrow
          x1={xs[3] + boxW / 2}
          y1={row1Y + boxH}
          x2={xs[3] + boxW / 2}
          y2={row1Y + 62}
        />
        <text
          x={xs[3] + boxW / 2 + 8}
          y={row1Y + boxH + 28}
          fontFamily="ui-monospace, monospace"
          fontSize="9.5"
          fill="#64748B"
        >
          summary ok
        </text>

        {/* failed state, shared, below queued/transcribing */}
        <Box x={failX} y={failY} w={boxW} h={boxH} label="failed" fill={FAIL_FILL} stroke={FAIL_STROKE} />
        <Arrow x1={xs[1] + boxW / 2} y1={row1Y + boxH} x2={failX + boxW / 2 - 18} y2={failY} />
        <Arrow x1={xs[2] + boxW / 2} y1={row1Y + boxH} x2={failX + boxW / 2 + 18} y2={failY} />

        {/* summary_status=failed note under completed */}
        <text
          x={xs[3] + boxW / 2}
          y={row1Y + 62 + boxH + 20}
          textAnchor="middle"
          fontFamily="ui-monospace, monospace"
          fontSize="9.5"
          fill="#B45309"
        >
          summary_status=failed
        </text>
        <text
          x={xs[3] + boxW / 2}
          y={row1Y + 62 + boxH + 33}
          textAnchor="middle"
          fontSize="9.5"
          fill="#94A3B8"
        >
          (still `completed` — transcript kept, retryable)
        </text>
      </svg>
      <p className="mt-3 text-xs text-gray-500">
        Any status can reach <code className="font-mono text-gray-600">failed</code> on an
        unrecoverable provider or validation error. A summary failure does not fail the job —
        it stays <code className="font-mono text-gray-600">completed</code> with the transcript
        intact, and <code className="font-mono text-gray-600">summary_status</code> set to{" "}
        <code className="font-mono text-gray-600">failed</code> so it can be retried.
      </p>
    </div>
  );
}
