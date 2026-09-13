// WHY: All thread-API communication in one file - same pattern as agent.js.
const API_BASE = "http://localhost:8000";

export async function createThread() {
  const r = await fetch(`${API_BASE}/api/threads`, { method: "POST" });
  if (!r.ok) throw new Error(`Create thread failed: ${r.status}`);
  return r.json(); // { thread_id, title }
}

export async function listThreads() {
  const r = await fetch(`${API_BASE}/api/threads`);
  if (!r.ok) throw new Error(`List threads failed: ${r.status}`);
  return r.json(); // { threads: [...] }
}

export async function startRun(threadId, task) {
  const r = await fetch(`${API_BASE}/api/threads/${threadId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `Run failed: ${r.status}`);
  }
  return r.json(); // { status: 'started' }
}

export async function fetchEvents(threadId, after) {
  const r = await fetch(`${API_BASE}/api/threads/${threadId}/events?after=${after}`);
  if (!r.ok) throw new Error(`Fetch events failed: ${r.status}`);
  return r.json(); // { events, last_seq, is_running }
}

export async function deleteThread(threadId) {
  const r = await fetch(`${API_BASE}/api/threads/${threadId}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`Delete failed: ${r.status}`);
  return r.json();
}