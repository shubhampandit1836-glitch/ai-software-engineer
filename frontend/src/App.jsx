import { useEffect, useRef } from "react";
import useAgentStream from "./hooks/useAgentStream";
import StatusPill from "./components/StatusPill";
import ChatInput from "./components/ChatInput";
import ChatMessage from "./components/ChatMessage";
import AgentSteps from "./components/AgentSteps";
import FinalAnswer from "./components/FinalAnswer";

export default function App() {
  const { messages, isRunning, currentNode, sendTask } = useAgentStream();

  // WHY: Auto-scroll on every new message — but only if the user hasn't
  // scrolled up to read. scrollTop + clientHeight >= scrollHeight - 100
  // means "near the bottom".
  const scrollRef = useRef(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - 100) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-3">
        <span className="text-2xl">🤖</span>
        <div>
          <h1 className="text-lg font-semibold">AI Software Engineer</h1>
          <p className="text-xs text-gray-500">Plans · Codes · Tests · Fixes — autonomously</p>
        </div>
      </header>

      {/* Chat scroll area */}
      <main ref={scrollRef} className="flex-1 overflow-y-auto px-4 md:px-8 py-6">
        <div className="max-w-3xl mx-auto space-y-4">
          {/* WHY: Empty state — first-time users see examples instead of a blank void. */}
          {messages.length === 0 && (
            <div className="text-center py-16 space-y-4">
              <p className="text-xl text-gray-300">What should I build?</p>
              <div className="flex flex-wrap justify-center gap-2">
                {[
                  "program to swap 2 numbers",
                  "write a function that checks if a string is a palindrome",
                  "what can you do",
                ].map((example) => (
                  <button
                    key={example}
                    onClick={() => sendTask(example)}
                    disabled={isRunning}
                    className="text-sm bg-gray-900 border border-gray-800 hover:border-green-600 text-gray-300 rounded-full px-4 py-2 transition-colors disabled:opacity-50"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => {
            if (msg.role === "user") {
              return (
                <ChatMessage key={i} role="user">
                  {msg.text}
                </ChatMessage>
              );
            }

            // Agent turn
            return (
              <div key={i} className="flex flex-col gap-2 items-start">
                {/* Collapsible steps disclosure — closed when done, live when running */}
                <AgentSteps
                  steps={msg.steps}
                  isRunning={isRunning && i === messages.length - 1}
                  currentNode={currentNode}
                  durationMs={msg.durationMs}
                />

                {/* Final answer card — appears the moment final_answer arrives */}
                {msg.answer && (
                  <div className="w-full max-w-[85%]">
                    <FinalAnswer answer={msg.answer} />
                  </div>
                )}

                {/* WHY: While running with no answer yet, show a raw bubble so
                    the turn never looks like floating UI without context */}
                {isRunning && i === messages.length - 1 && !msg.answer && (
                  <ChatMessage role="agent">
                    <span className="animate-pulse text-sm">Working...</span>
                  </ChatMessage>
                )}
              </div>
            );
          })}
        </div>
      </main>

      {/* Input area */}
      <footer className="border-t border-gray-800 px-4 md:px-8 py-4">
        <div className="max-w-3xl mx-auto space-y-3">
          <StatusPill node={currentNode} isRunning={isRunning} />
          <ChatInput onSubmit={sendTask} isRunning={isRunning} />
        </div>
      </footer>
    </div>
  );
}