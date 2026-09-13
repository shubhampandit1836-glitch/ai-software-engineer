import { useState } from "react";

export default function ChatInput({ onSubmit, isRunning }) {
  const [value, setValue] = useState("");

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed || isRunning) return;
    onSubmit(trimmed);
    setValue("");
  };

  return (
    <div className="flex gap-2">
      <input
        type="text"
        value={value}
        disabled={isRunning}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
        }}
        placeholder="Ask me to build a program…"
        className="input-chat"
      />
      <button onClick={handleSubmit} disabled={isRunning || !value.trim()} className="btn-brand">
        {isRunning ? "Running…" : "Send"}
      </button>
    </div>
  );
}