import { useState } from "react";
import { getPhase } from "../utils/phases";
import StepItem from "./StepItem";

// WHY: THE Claude pattern. While running: "⌨️ Writing code... ▸ Thinking".
// When done: "✓ Completed in 14s · 13 steps" — closed by default, so the
// final answer owns the screen. Expandable at ANY time (even mid-run)
// to watch the agent work live.
export default function AgentSteps({ steps, isRunning, currentNode, durationMs }) {
  const [open, setOpen] = useState(false);

  const done = !isRunning;
  const seconds = (durationMs / 1000).toFixed(1);

  return (
    <div className="w-full">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
      >
        <span className="text-xs">{open ? "▾" : "▸"}</span>

        {done ? (
          <span>
            <span className="text-emerald-400">✓</span>
            {durationMs > 0 ? ` Completed in ${seconds}s · ` : " Completed · "}
            {steps.length} {steps.length === 1 ? "step" : "steps"}
          </span>
        ) : (
          <span className="animate-pulse">
            {getPhase(currentNode).icon} {getPhase(currentNode).label}... (thinking)
          </span>
        )}
      </button>

      {/* WHY: max-h + overflow-y-auto — a 30-step trace must never
          blow up the page layout. Scroll INSIDE the disclosure. */}
      {open && (
        <div className="mt-2 border border-gray-800 rounded-lg bg-gray-950 p-3 max-h-[50vh] overflow-y-auto">
          {steps.length === 0 && (
            <p className="text-xs text-gray-600 italic">Starting...</p>
          )}
          {steps.map((step, i) => (
            <StepItem key={i} step={step} />
          ))}
          {isRunning && (
            <div className="border-l border-gray-800 pl-3 py-1 flex items-center gap-2 text-sm text-gray-500 animate-pulse">
              <span className="text-xs">▸</span> working...
            </div>
          )}
        </div>
      )}
    </div>
  );
}