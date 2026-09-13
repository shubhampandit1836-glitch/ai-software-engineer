export default function ThreadSidebar({ open, threads, activeId, onSelect, onNewChat, onDelete, onClose }) {
  return (
    <>
      {open && <div className="fixed inset-0 bg-black/60 z-30 md:hidden" onClick={onClose} />}
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="p-4 border-b border-white/10">
          <div className="brand-title">🤖 AI Software Engineer</div>
          <div className="brand-sub">Plans · Writes · Tests · Fixes — autonomously</div>
        </div>

        <div className="p-3">
          <button onClick={onNewChat} className="btn-brand w-full">+ New chat</button>
        </div>

        <div className="px-4 pt-2 pb-1 text-[11px] uppercase tracking-widest" style={{ color: "var(--ink-dim)" }}>
          Chat history
        </div>

        {/* WHY only this section scrolls: branding and the button stay
            pinned; a long thread list never pushes the layout around. */}
        <div className="flex-1 overflow-y-auto thin-scroll px-2 pb-3 space-y-1">
          {threads.length === 0 && (
            <p className="text-xs italic px-3 py-2" style={{ color: "var(--ink-dim)" }}>No threads yet</p>
          )}
          {threads.map((t) => (
            <div key={t.thread_id} className={`thread-item ${t.thread_id === activeId ? "thread-item-active" : ""}`}>
              <button
                className="flex-1 text-left truncate"
                onClick={() => { onSelect(t.thread_id); onClose(); }}
              >
                {t.title || "New chat"}
              </button>
              <button className="thread-delete" title="Delete thread" onClick={() => onDelete(t)}>🗑</button>
            </div>
          ))}
        </div>
      </aside>
    </>
  );
}