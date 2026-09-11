import { useState, useCallback, useRef, useEffect } from "react";
import { streamAgent } from "../api/agent";

/**
 * The brain of the frontend: owns the chat, consumes the SSE stream,
 * and accumulates state across events.
 */
export default function useAgentStream() {
  const [messages, setMessages] = useState([]);
  const [isRunning, setIsRunning] = useState(false);
  const [currentNode, setCurrentNode] = useState(null);

  // WHY: Refs for values the async loop needs WITHOUT re-triggering
  // renders. A closure over useState values would go stale mid-run.
  const stepsRef = useRef([]);
  const answerRef = useRef(null);
  const startTimeRef = useRef(0);
  const durationRef = useRef(0);

  const sendTask = useCallback(async (task) => {
    if (!task.trim()) return;

    // --- 1. Show the user bubble + a placeholder for the agent turn ---
    setMessages((prev) => [...prev, { role: "user", text: task }]);

    // WHY: agentRef pattern — we push an object and mutate it as events
    // arrive, because state updates are async and we'd lose the reference.
    // Final structure is replaced wholesale at the end.
    const agentTurn = { role: "agent", steps: [], answer: null, durationMs: 0, failed: false };
    setMessages((prev) => [...prev, agentTurn]);
    const turnIndexRef = { current: -1 };

    setIsRunning(true);
    setCurrentNode("input_guardrail");
    stepsRef.current = [];
    answerRef.current = null;
    startTimeRef.current = Date.now();
    durationRef.current = 0;

    try {
      // --- 2. Consume the stream ---
      for await (const event of streamAgent(task)) {
        // Track the CURRENT node for the StatusPill
        if (event.node && event.node !== "end") {
          setCurrentNode(event.node);
        }

        // --- 3. The end event: finish the run ---
        if (event.node === "end") {
          durationRef.current = Date.now() - startTimeRef.current;
          break;
        }

        // --- 4. Accumulate state across events ---
        // WHY: THE MERGE STRATEGY. LangGraph's 'updates' mode sometimes
        // bundles multiple nodes into one event (we saw direct_answer's
        // payload arrive inside intent_classifier's event). Merging every
        // non-null field into a snapshot makes us immune to that — the
        // final_answer displays no matter which event carried it.
        if (event.final_answer) answerRef.current = event.final_answer;

        if (event.plan) {
          // WHY: Store the LATEST plan — later planner events would
          // overwrite, but plans arrive once per run in our graph.
          stepsRef.current.plan = event.plan;
        }

        stepsRef.current.push({
          ...event,
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
        });

        // --- 5. Live-update the agent turn in the messages array ---
        setMessages((prev) => {
          const next = [...prev];
          const idx = next.length - 1;
          // WHY: Guard — only mutate if the last message is really our agent turn.
          if (idx >= 0 && next[idx].role === "agent") {
            next[idx] = {
              ...next[idx],
              steps: [...stepsRef.current],
              answer: answerRef.current,
              durationMs: Date.now() - startTimeRef.current,
            };
          }
          return next;
        });
      }
    } catch (error) {
      // --- 6. Error becomes an honest answer, never a blank screen ---
      stepsRef.current.push({
        node: "error",
        status: "failed",
        execution_error: error.message,
        timestamp: new Date().toLocaleTimeString(),
      });
      answerRef.current = `⚠️ **Connection error**\n\n${error.message}\n\nIs the backend running on port 8000?`;
      setMessages((prev) => {
        const next = [...prev];
        const idx = next.length - 1;
        if (idx >= 0 && next[idx].role === "agent") {
          next[idx] = { ...next[idx], failed: true, steps: [...stepsRef.current], answer: answerRef.current };
        }
        return next;
      });
    } finally {
      // --- 7. Seal the turn ---
      durationRef.current = Date.now() - startTimeRef.current;
      setMessages((prev) => {
        const next = [...prev];
        const idx = next.length - 1;
        if (idx >= 0 && next[idx].role === "agent") {
          next[idx] = { ...next[idx], durationMs: durationRef.current, answer: answerRef.current, steps: [...stepsRef.current] };
        }
        return next;
      });
      setIsRunning(false);
      setCurrentNode(null);
    }
  }, []);

  return { messages, isRunning, currentNode, sendTask };
}