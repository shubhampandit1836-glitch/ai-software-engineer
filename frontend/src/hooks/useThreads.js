import { useState, useEffect, useRef, useCallback } from "react";
import { createThread, listThreads, startRun, fetchEvents, deleteThread } from "../api/threads";

const POLL_MS = 2000;

export default function useThreads() {
  const [threads, setThreads] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [events, setEvents] = useState([]);
  const [isRunning, setIsRunning] = useState(false);
  const [currentNode, setCurrentNode] = useState(null);

  // WHY refs not state-in-deps: the v1 poller re-created its interval on
  // every cursor move, letting a stale in-flight poll resolve LAST and
  // regress the cursor -> duplicated events -> the old answer rendered
  // over the new one. Refs are always current; the interval never restarts.
  const cursorRef = useRef(-1);
  const activeIdRef = useRef(null);
  const freshRef = useRef(false); // first poll after a switch replaces wholesale
  const pollNowRef = useRef(null);

  const refreshThreads = useCallback(() => {
    listThreads().then((d) => setThreads(d.threads)).catch(() => {});
  }, []);

  useEffect(() => { refreshThreads(); }, [refreshThreads]);

  // Interval lifecycle only - all switching state lives in refs (see switchTo)
  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;

    const poll = async () => {
      const id = activeIdRef.current;
      if (!id || cancelled) return;
      try {
        const data = await fetchEvents(id, cursorRef.current);
        if (cancelled || activeIdRef.current !== id) return;

        if (freshRef.current) {
          setEvents(data.events); // wholesale replace: correct thread, no flash
          freshRef.current = false;
        } else if (data.events.length > 0) {
          setEvents((prev) => mergeBySeq(prev, data.events));
        }
        if (data.events.length > 0) cursorRef.current = data.last_seq;

        const meaningful = data.events.filter(
          (e) => e.type !== "token" && e.node !== "end" && e.node !== "user"
        );
        if (meaningful.length > 0) setCurrentNode(meaningful[meaningful.length - 1].node);
        setIsRunning(data.is_running);
      } catch { /* transient blip - next tick retries */ }
    };

    poll();
    pollNowRef.current = poll;
    const timer = setInterval(poll, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); pollNowRef.current = null; };
  }, [activeId]);

  // WHY one switch path: sets ref + state + cursor atomically, so an
  // immediate poll after sending can never read a stale cursor.
  const switchTo = useCallback((id) => {
    activeIdRef.current = id;
    cursorRef.current = -1;
    freshRef.current = true;
    setIsRunning(false);
    setCurrentNode(null);
    setActiveId(id);
  }, []);

  const switchThread = useCallback((id) => {
    if (id !== activeIdRef.current) switchTo(id);
  }, [switchTo]);

  const newChat = useCallback(async () => {
    // WHY the guard: an untouched thread ("New chat" title = no first
    // message) IS already a fresh chat - creating another just stacks
    // empty threads in the sidebar.
    const active = threads.find((t) => t.thread_id === activeIdRef.current);
    if (active && active.title === "New chat") return null;
    const t = await createThread();
    setThreads((prev) => [t, ...prev]);
    switchTo(t.thread_id);
    return t.thread_id;
  }, [threads, switchTo]);

  const sendTask = useCallback(async (task) => {
    let id = activeIdRef.current;
    if (!id) {
      const t = await createThread();
      setThreads((prev) => [t, ...prev]);
      switchTo(t.thread_id);
      id = t.thread_id;
    }
    await startRun(id, task);
    setIsRunning(true);
    setCurrentNode("input_guardrail");
    refreshThreads();               // title updates server-side on first message
    pollNowRef.current?.();         // instant pickup of the persisted user event
  }, [refreshThreads]);

  const removeThread = useCallback(async (id) => {
    await deleteThread(id);
    const d = await listThreads();
    setThreads(d.threads);
    if (activeIdRef.current === id) {
      const next = d.threads[0]?.thread_id ?? null;
      if (next) switchTo(next);
      else {
        activeIdRef.current = null;
        setActiveId(null);
        setEvents([]);
        setIsRunning(false);
      }
    }
  }, [switchTo]);

  return { threads, activeId, events, isRunning, currentNode,
           switchThread, newChat, sendTask, removeThread };
}

// WHY merge by seq: overlapping polls are inevitable; seq is the server's
// ordering key, so Map + sort makes every merge idempotent.
function mergeBySeq(prev, incoming) {
  const map = new Map(prev.map((e) => [e.seq, e]));
  for (const e of incoming) map.set(e.seq, e);
  return [...map.values()].sort((a, b) => a.seq - b.seq);
}