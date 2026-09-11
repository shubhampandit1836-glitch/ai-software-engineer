// WHY: All backend communication lives in ONE file. If we ever deploy the
// backend to a real URL, we change it here — not hunted across components.
const API_BASE = "http://localhost:8000";

/**
 * Streams the agent's execution as parsed JSON events.
 * WHY: An async generator (function*) — the consumer just does
 * `for await (const event of streamAgent(task))`. All the messy
 * SSE parsing (chunk boundaries, partial JSON) is encapsulated here.
 */
export async function* streamAgent(task) {
  const response = await fetch(`${API_BASE}/api/agent/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task }),
  });

  if (!response.ok) {
    throw new Error(`Backend error: ${response.status} ${response.statusText}`);
  }
  if (!response.body) {
    throw new Error("Streaming not supported in this browser");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  // WHY: Network chunks do NOT respect event boundaries. A single
  // 'data: {...}' line can arrive split across two chunks, or several
  // events can arrive in one chunk. The buffer holds the incomplete
  // tail until the rest arrives — without it, JSON.parse crashes
  // on half a message. This is THE classic SSE bug.
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // WHY: SSE events are separated by a blank line (\n\n).
    const parts = buffer.split("\n\n");
    // WHY: pop() keeps the last (possibly incomplete) part in the buffer.
    buffer = parts.pop();

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;

      try {
        // WHY: slice(5) strips 'data:'. .trim() makes it tolerant of
        // 'data: {...}' and 'data:{...}' spacing differences.
        yield JSON.parse(line.slice(5).trim());
      } catch {
        // WHY: A malformed event must never kill the whole stream —
        // skip it and keep receiving. Defensive by design.
        console.warn("Skipped malformed SSE event:", line);
      }
    }
  }
}