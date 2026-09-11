// WHY: One single source of truth for "what is the agent doing right now".
// The StatusPill, the collapsed step header, and future features all read
// from here — change a label once, it updates everywhere.
export const PHASES = {
  input_guardrail:  { label: "Checking request",     icon: "🛡️" },
  intent_classifier:{ label: "Understanding request", icon: "🧠" },
  direct_answer:    { label: "Composing answer",     icon: "💬" },
  planner:          { label: "Planning",             icon: "📋" },
  coder:            { label: "Writing code",         icon: "⌨️" },
  security_scanner: { label: "Security scan",        icon: "🔒" },
  tester:           { label: "Testing in sandbox",   icon: "🧪" },
  reviewer:         { label: "Fixing code",          icon: "🔧" },
  synthesizer:      { label: "Finalizing answer",    icon: "✨" },
  failure_answer:   { label: "Wrapping up",          icon: "📄" },
  end:              { label: "Completed",            icon: "✅" },
  error:            { label: "Error",                icon: "❌" },
};

// WHY: Unknown nodes must still render something sensible —
// never a blank pill if the backend adds a node later.
export function getPhase(node) {
  return PHASES[node] || { label: "Working", icon: "⚙️" };
}