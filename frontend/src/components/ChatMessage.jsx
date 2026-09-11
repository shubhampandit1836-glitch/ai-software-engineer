// WHY: Two visual roles in ONE component via the `role` prop —
// user messages right-aligned green, assistant left-aligned gray.
// A single component beats duplicating layout in two places.
export default function ChatMessage({ role, children }) {
  const isUser = role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={
          isUser
            ? "bg-green-900/40 border border-green-700/50 text-gray-100 rounded-2xl rounded-tr-sm px-4 py-2 max-w-[80%]"
            : "bg-gray-900 border border-gray-800 text-gray-100 rounded-2xl rounded-tl-sm px-4 py-3 max-w-[80%]"
        }
      >
        {children}
      </div>
    </div>
  );
}