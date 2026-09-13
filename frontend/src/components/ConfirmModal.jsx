// WHY a custom modal, not window.confirm: browser dialogs are blocking,
// unstyleable, and scream "prototype". A SaaS-feel product needs its own.
export default function ConfirmModal({ thread, onConfirm, onCancel }) {
  if (!thread) return null;
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-rose-300">Delete thread?</h3>
        <p className="text-sm mt-2" style={{ color: "var(--ink-dim)" }}>
          “{thread.title}” and its entire history will be permanently removed.
        </p>
        <div className="flex justify-end gap-2 mt-5">
          <button className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button className="btn-danger" onClick={onConfirm}>Delete</button>
        </div>
      </div>
    </div>
  );
}