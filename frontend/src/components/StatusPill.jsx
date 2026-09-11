import { getPhase } from "../utils/phases";

// WHY: While the agent runs, this sits above the input showing the CURRENT
// phase with a pulse animation — the "it's alive" signal. Claude/ChatGPT do
// exactly this; users panic at a silent UI.
export default function StatusPill({ node, isRunning }) {
  if (!isRunning || !node) return null;

  const phase = getPhase(node);

  return (
    <div className="flex items-center justify-center gap-2 text-sm text-gray-400 animate-pulse">
      <span className="text-base">{phase.icon}</span>
      <span>{phase.label}...</span>
      <span className="flex gap-1">
        <span className="w-1 h-1 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
        <span className="w-1 h-1 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
        <span className="w-1 h-1 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
      </span>
    </div>
  );
}