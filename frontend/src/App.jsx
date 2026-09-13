import { useMemo, useState } from "react";
import ThreadSidebar from "./components/ThreadSidebar";
import ConfirmModal from "./components/ConfirmModal";
import StatusPill from "./components/StatusPill";
import ChatInput from "./components/ChatInput";
import FinalAnswer from "./components/FinalAnswer";
import AgentSteps from "./components/AgentSteps";
import useThreads from "./hooks/useThreads";

export default function App() {
  const {
    threads, activeId, events, isRunning, currentNode,
    switchThread, newChat, sendTask, removeThread,
  } = useThreads();

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);

  const activeThread = threads.find((t) => t.thread_id === activeId);

  // WHY turns: events are ONE ordered stream; each persisted user_message
  // starts a new turn. This makes prompts appear in place (not stacked on
  // top) and keeps every answer attached to its own question.
  const turns = useMemo(() => groupTurns(events), [events]);

  return (
    <div className="app-shell">
      <ThreadSidebar
        open={sidebarOpen}
        threads={threads}
        activeId={activeId}
        onSelect={switchThread}
        onNewChat={newChat}
        onDelete={setPendingDelete}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="flex-1 flex flex-col min-w-0">
        <header className="app-header">
          <button className="hamburger md:hidden" onClick={() => setSidebarOpen(true)}>☰</button>
          <div className="min-w-0">
            <h1 className="header-title">{activeThread?.title || "New chat"}</h1>
            <p className="header-sub">Runs continue in background while you switch threads</p>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto thin-scroll px-4 md:px-8 py-6">
          <div className="max-w-3xl mx-auto space-y-8">
            {turns.length === 0 && !isRunning && <EmptyState onPick={sendTask} />}

            {turns.map((turn, i) => {
              const isLast = i === turns.length - 1;
              const runLive = isLast && isRunning;
              const steps = turn.events.filter((e) => e.type !== "token" && e.node !== "end");
              const answer = [...turn.events].reverse().find((e) => e.final_answer)?.final_answer;

              return (
                <div key={turn.seq} className="space-y-3 fade-in">
                  <div className="flex justify-end">
                    <div className="bubble-user">{turn.userMessage}</div>
                  </div>

                  {(steps.length > 0 || runLive) && (
                    <div className="flex flex-col gap-2 items-start">
                      <AgentSteps
                        steps={steps}
                        isRunning={runLive}
                        currentNode={runLive ? currentNode : null}
                        durationMs={0}
                      />
                      {answer && (
                        <div className="w-full max-w-[85%]">
                          <FinalAnswer answer={answer} />
                        </div>
                      )}
                      {runLive && !answer && (
                        <div className="bubble-agent animate-pulse">Working…</div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </main>

        <footer className="app-footer">
          <div className="max-w-3xl mx-auto space-y-3">
            <StatusPill node={currentNode} isRunning={isRunning} />
            <ChatInput onSubmit={sendTask} isRunning={isRunning} />
          </div>
        </footer>
      </div>

      <ConfirmModal
        thread={pendingDelete}
        onCancel={() => setPendingDelete(null)}
        onConfirm={async () => {
          const id = pendingDelete.thread_id;
          setPendingDelete(null);
          await removeThread(id);
        }}
      />
    </div>
  );
}

function groupTurns(events) {
  const turns = [];
  for (const e of events) {
    if (e.node === "user") {
      turns.push({ seq: e.seq, userMessage: e.content, events: [] });
    } else if (turns.length > 0) {
      turns[turns.length - 1].events.push(e);
    }
  }
  return turns;
}

function EmptyState({ onPick }) {
  return (
    <div className="text-center py-16 fade-in">
      <div className="text-5xl mb-4">🤖</div>
      <p className="text-xl mb-1">What should I build?</p>
      <p className="text-sm mb-6" style={{ color: "var(--ink-dim)" }}>
        Multi-file projects, sandbox-verified — while you chat elsewhere.
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {["program to swap 2 numbers", "build a FastAPI todo API with tests", "what can you do"].map((ex) => (
          <button key={ex} onClick={() => onPick(ex)} className="btn-ghost rounded-full text-sm px-4 py-2">
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}