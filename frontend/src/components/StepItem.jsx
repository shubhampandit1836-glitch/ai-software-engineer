import { useState } from "react";
import { getPhase } from "../utils/phases";

// WHY: Node name -> human description shown INSIDE the expanded trace.
// (getPhase gives the short pill label; this gives the detailed one.)
const STEP_DESCRIPTIONS = {
  input_guardrail: "Checked request safety",
  intent_classifier: "Classified the request",
  direct_answer: "Answered directly",
  planner: "Created an execution plan",
  coder: "Wrote code",
  security_scanner: "Scanned code for dangerous operations",
  tester: "Executed code in secure sandbox",
  reviewer: "Debugged and fixed the code",
  synthesizer: "Composed the final answer",
  failure_answer: "Reported the outcome",
};

// WHY: Nested disclosure INSIDE the outer "Thinking" disclosure.
// Claude does this: "Ran 14 steps ▸" expands, and the interesting
// steps (code, output) expand FURTHER. We mirror that pattern.
export default function StepItem({ step }) {
  const [open, setOpen] = useState(false);
  const phase = getPhase(step.node);
  const description = STEP_DESCRIPTIONS[step.node] || "Agent step";

  const hasDetail = Boolean(
    step.plan ||
    step.current_code ||
    step.execution_result ||
    step.execution_error ||
    step.security_violation
  );

  return (
    <div className="border-l border-gray-800 pl-3 py-1">
      <button
        onClick={() => hasDetail && setOpen(!open)}
        className={
          hasDetail
            ? "flex items-center gap-2 text-sm text-gray-400 hover:text-gray-200 w-full text-left transition-colors"
            : "flex items-center gap-2 text-sm text-gray-400 w-full text-left cursor-default"
        }
      >
        <span className="text-xs">{open ? "▾" : "▸"}</span>
        <span className="text-xs">{phase.icon}</span>
        <span>{description}</span>
        <span className="text-xs text-gray-600">{step.timestamp}</span>

        {/* WHY: Color-coded verdict chip. Users scan color first,
            text second — green pass, red fail, amber security. */}
        {step.status === "testing_passed" && (
          <span className="ml-auto text-xs text-green-500">passed</span>
        )}
        {step.status === "testing_failed" && (
          <span className="ml-auto text-xs text-red-400">failed</span>
        )}
        {step.status === "security_violation" && (
          <span className="ml-auto text-xs text-amber-400">flagged</span>
        )}
      </button>

      {/* WHY: Only render heavy content when open — keeps long traces
          fast (no 30 code blocks in the DOM at once). */}
      {open && (
        <div className="mt-2 ml-5 space-y-2">
          {step.plan && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Plan:</p>
              <ul className="text-sm text-gray-300 list-disc list-inside space-y-0.5">
                {step.plan.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {step.current_code && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Code:</p>
              <pre className="bg-black/60 border border-gray-800 rounded-md p-2 text-xs text-green-300 overflow-x-auto whitespace-pre-wrap">
                <code>{step.current_code}</code>
              </pre>
            </div>
          )}

          {step.execution_result && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Sandbox output:</p>
              <pre className="bg-black/60 border border-gray-800 rounded-md p-2 text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                {step.execution_result}
              </pre>
            </div>
          )}

          {(step.execution_error || step.security_violation) && (
            <div>
              <p className="text-xs text-red-400 mb-1">Problem found:</p>
              <pre className="bg-red-950/40 border border-red-900/50 rounded-md p-2 text-xs text-red-300 overflow-x-auto whitespace-pre-wrap">
                {step.execution_error || step.security_violation}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}