import { useState } from "react";

// WHY: Controlled input + Enter-to-send (Shift+Enter for future multiline).
// Disabled while running so a second submission can't race the stream.
export default function ChatInput({ onSubmit, isRunning }) {
  const [value, setValue] = useState("");

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed || isRunning) return;
    onSubmit(trimmed);
    setValue(""); // WHY: clear AFTER submit — instant feedback, no stuck text
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault(); // WHY: stop the newline BEFORE it lands
      handleSubmit();
    }
  };

  return (
    <div className="flex gap-2">
      <input
        type="text"
        value={value}
        disabled={isRunning}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask me to build a program..."
        className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 text-gray-100 placeholder-gray-500 focus:outline-none focus:border-green-500 disabled:opacity-50"
      />
      <button
        onClick={handleSubmit}
        disabled={isRunning || !value.trim()}
        className="bg-green-600 hover:bg-green-500 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold px-5 py-3 rounded-lg transition-colors"
      >
        {isRunning ? "Running..." : "Send"}
      </button>
    </div>
  );
}