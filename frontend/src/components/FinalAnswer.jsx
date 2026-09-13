import { renderMarkdown } from "../utils/markdown";

export default function FinalAnswer({ answer, streaming = false }) {
  return (
    <div className="glass-card w-full">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-lg">🤖</span>
        <span className="text-xs font-bold uppercase tracking-widest" style={{ color: "var(--brand-2)" }}>
          AI Software Engineer
        </span>
        {streaming && <span className="ml-auto text-xs text-emerald-300 animate-pulse">typing…</span>}
      </div>
      <div>{renderMarkdown(answer)}</div>
      {streaming && (
        <span className="inline-block w-2 h-4 rounded-sm animate-pulse" style={{ background: "var(--brand-2)" }} />
      )}
    </div>
  );
}