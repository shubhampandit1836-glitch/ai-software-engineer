import { renderMarkdown } from "../utils/markdown";

// WHY: The frame around the rendered answer. The `streaming` prop adds a
// typing cursor - true while tokens flow, false once the authoritative
// final_answer has replaced the stream.
export default function FinalAnswer({ answer, streaming = false }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 w-full">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-lg">🤖</span>
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
          AI Software Engineer
        </span>
        {streaming && (
          <span className="ml-auto text-xs text-green-400 animate-pulse">typing...</span>
        )}
      </div>
      <div>{renderMarkdown(answer)}</div>
      {streaming && (
        <span className="inline-block w-2 h-4 bg-green-400 animate-pulse rounded-sm" />
      )}
    </div>
  );
}