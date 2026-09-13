import { useState, useCallback, useRef } from "react";
import { streamAgent } from "../api/agent";

/**
 * The brain of the frontend: owns the chat, consumes the SSE stream,
 * accumulates state across events, and streams tokens into the answer card.
 */
export default function useAgentStream() {
  const [messages, setMessages] = useState([]);
  const [isRunning, setIsRunning] = useState(false);
  const [currentNode, setCurrentNode] = useState(null);

  // WHY: Refs for values the async loop needs WITHOUT re-triggering
  // renders. A closure over useState values would go stale mid-run.
  const stepsRef = useRef([]);
  const answerRef = useRef(null);
  const streamingRef = useRef("");
  const startTimeRef = useRef(0);

  const sendTask = useCallback(async (task) => {
    if (!task.trim()) return;

    // User bubble + placeholder agent turn
    setMessages((prev) => [...prev, { role: "user", text: task }]);
    setMessages((prev) => [
      ...prev,
      { role: "agent", steps: [], answer: null, streamingAnswer: "", durationMs: 0 },
    ]);

    setIsRunning(true);
    setCurrentNode("input_guardrail");
    stepsRef.current = [];
    answerRef.current = null;
    streamingRef.current = "";
    startTimeRef.current = Date.now();

    // WHY: one helper updates the LAST agent turn immutably. Functional
    // setState = no stale closures; index check = never corrupts a user bubble.
    const updateTurn = (patch) => {
      setMessages((prev) => {
        const next = [...prev];
        const idx = next.length - 1;
        if (idx >= 0 && next[idx].role === "agent") {
          next[idx] = { ...next[idx], ...patch };
        }
        return next;
      });
    };

    // WHY: Groq emits tokens faster than React can re-render + re-parse
    // markdown. We accumulate in the ref and flush to state at most every
    // 50ms - the user sees smooth fast typing, the DOM does ~20 updates
    // instead of hundreds.
    let flushTimer = null;
    const flushStream = () => {
      if (flushTimer) return;
      flushTimer = setTimeout(() => {
        flushTimer = null;
        updateTurn({ streamingAnswer: streamingRef.current });
      }, 50);
    };
    // WHY: The ghost-timer kill switch. A pending timer that fires AFTER
    // the turn ends would patch the NEXT turn with the OLD answer's text.
    // Called on every exit path - end event, error, connection drop.
    const clearFlushTimer = () => {
      if (flushTimer) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
    };

    try {
      for await (const event of streamAgent(task)) {
        // ---- Token events: type the answer out live ----
        if (event.type === "token") {
          // WHY: keeps the phase pill fresh while tokens flow.
          // setCurrentNode with an unchanged value is a no-op re-render.
          if (event.node) setCurrentNode(event.node);
          streamingRef.current += event.content;
          flushStream();
          continue;
        }

        // ---- End event: seal the turn ----
        if (event.node === "end") {
          clearFlushTimer(); // WHY: kill the ghost before sealing
          updateTurn({
            durationMs: Date.now() - startTimeRef.current,
            streamingAnswer: "",
          });
          break;
        }

        // ---- Node updates: pill, steps, authoritative answer ----
        if (event.node) setCurrentNode(event.node);
        if (event.final_answer) answerRef.current = event.final_answer;

        stepsRef.current.push({
          ...event,
          timestamp: new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }),
        });

        // WHY: when final_answer arrives it REPLACES the streamed text -
        // the authoritative version (cleaned, complete format) always wins.
        updateTurn({
          steps: [...stepsRef.current],
          answer: answerRef.current,
        });
      }
    } catch (error) {
      stepsRef.current.push({
        node: "error",
        status: "failed",
        execution_error: error.message,
        timestamp: new Date().toLocaleTimeString(),
      });
      answerRef.current = `⚠️ **Connection error**\n\n${error.message}\n\nIs the backend running on port 8000?`;
      updateTurn({
        failed: true,
        steps: [...stepsRef.current],
        answer: answerRef.current,
        streamingAnswer: "",
      });
    } finally {
      // WHY: fires for EVERY exit path (break, catch, stream end) -
      // the ghost-timer cannot survive this line.
      clearFlushTimer();
      updateTurn({
        durationMs: Date.now() - startTimeRef.current,
        answer: answerRef.current,
        streamingAnswer: "",
        steps: [...stepsRef.current],
      });
      setIsRunning(false);
      setCurrentNode(null);
    }
  }, []);

  return { messages, isRunning, currentNode, sendTask };
}