import { renderMarkdown } from "../utils/markdown";

// WHY: The frame around the rendered answer. Kept separate from the
// renderer so the frame (border, header, padding) can evolve without
// touching parsing logic — and vice versa.
export default function FinalAnswer({ answer }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 w-full">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-lg">🤖</span>
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
          AI Software Engineer
        </span>
      </div>
      <div>{renderMarkdown(answer)}</div>
    </div>
  );
}