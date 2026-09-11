import re
from typing import  Any, Dict
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.llm import llm_fast, invoke_with_retry
from app.agent.state import AgentState

# ---------- Layer 1: Deterministic Input Guardrail ----------

# WHY: A regex blacklist is instant, free, and impossible to jailbreak with
# prompt tricks. The LLM classifier is smart but can be manipulated — this
# layer cannot. Defense in depth: deterministic check FIRST, LLM judgment second.
_UNSAFE_PATTERNS = [
    r"key\s?logger",
    r"\bvirus\b", r"\bmalware\b", r"\btrojan\b", r"\bransomware\b", r"\bspyware\b",
    r"\bddos\b", r"denial[- ]of[- ]service",
    r"brute[ -]?force",
    r"phishing",
    r"\bhack(ing)?\s+(a\s+|the\s+|some\s+|my\s+)?(wifi|wi[- ]fi|password|passwd|email|account|facebook|instagram|gmail|phone|pc|computer)",
    r"crack(ing)?\s+(a\s+)?(password|wifi|wi[- ]fi|software|license)",
    r"steal\s+(passwords|credentials|credit|card|data|tokens)",
    r"delete\s+all\s+files", r"wipe\s+(the\s+)?(hard\s?drive|disk|system)",
    r"format\s+[c]:", r"rm\s+-rf\s+/",
    r"bypass\s+(the\s+)?(antivirus|firewall|security|login|authentication)",
    r"credit\s?card\s+(steal|skim|numbers)",
]
UNSAFE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _UNSAFE_PATTERNS]

MAX_INPUT_LENGTH = 5000

REJECTION_MESSAGE = (
    "I can't help with that request. I'm an AI Software Engineer built to write "
    "safe, everyday programs — algorithms, utilities, and small applications. "
    "Try something like: 'write a program to check if a string is a palindrome'."
)

async def input_guardrail_node(state: AgentState) -> Dict[str, Any]:
    """Deterministic safety gate. Runs BEFORE any LLM call (costs nothing)."""
    task = state.get("task_description", "")

    # WHY: Length guard stops prompt-injection payloads and giant inputs
    # that would blow up token limits downstream.
    if len(task) > MAX_INPUT_LENGTH:
        return {
            "intent": "unsafe_request",
            "final_answer": "Your request is too long. Please break it into a smaller, specific coding task.",
            "status": "rejected",
        }

    for pattern in UNSAFE_PATTERNS:
        if pattern.search(task):
            return {
                "intent": "unsafe_request",
                "safety_violation_reason": f"Matched blocked pattern: {pattern.pattern}",
                "final_answer": REJECTION_MESSAGE,
                "status": "rejected",
            }

    # WHY: Clean input. The router reads this status and sends us to the
    # intent classifier next.
    return {"status": "input_clean"}


# ---------- Layer 2: LLM Intent Classification ----------

def _extract_intent(content: str) -> str:
    """Robust pulls the intent label out of (possibly messy) LLM output."""
    # WHY: Free models sometimes wrap answers in markdown or thinking text.
    # Substring search on a normalized string beats json.loads() on imperfect output.
    normalized = content.lower().replace(" ", "_")
    
    # WHY: Reasoning models state their CONCLUSION last. Scanning lines
    # bottom-up finds the verdict, not a label mentioned mid-thought.
    for line in reversed(normalized.splitlines()):
        for label in ("unsafe_request", "coding_task", "general_question"):
            if label in line:
                return label
            
    # WHY: SAFE DEFAULT. If we can't parse the output, treat it as a general
    # question — the path that NEVER touches the code sandbox.
    return "general_question"

async def intent_classifier_node(state: AgentState) -> Dict[str, Any]:
    """Uses the FAST model to decide: coding task, general question, or unsafe?"""
    task = state["task_description"]

    system_prompt = """You are the intent classifier gate for an AI Software Engineer agent.

Classify the user's message into EXACTLY one of three intents:

1. "coding_task" — the user wants working code: a function, script, algorithm,
   program, or bug fix.
   Examples: "program to swap 2 numbers", "write a fibonacci function",
   "build a palindrome checker"

2. "general_question" — a question, greeting, or conversation that should be
   answered directly WITHOUT writing any code.
   Examples: "what can you do", "who are you", "what is a decorator in python"

3. "unsafe_request" — a request for malicious, destructive, or harmful code.

Respond with ONLY the intent label. No explanations, no markdown, no punctuation."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=task),
    ]

    response = await invoke_with_retry(llm_fast, messages)
    content = response.content
    if not isinstance(content, str):
        content = "\n".join(str(item) for item in content)

    # WHY: THE LINE THE BROKEN PASTE DROPPED. Classify first, then branch.
    intent = _extract_intent(content)

    # WHY: If the LLM catches what the regex missed, attach the rejection
    # message HERE. The router then ends the run with a complete answer.
    if intent == "unsafe_request":
        return {
            "intent": "unsafe_request",
            "final_answer": REJECTION_MESSAGE,
            "status": "rejected",
        }

    return {"intent": intent, "status": "intent_classified"}
        
# ---------- Layer 3: Code Security Scanner (Static Analysis) ----------

# WHY: LLM-generated code gets executed in a cloud sandbox. The sandbox is
# isolated, but we still BLOCK dangerous operations statically BEFORE running.
# A regex scan is deterministic — unlike an LLM, it cannot be jailbroken
# by cleverly-worded prompts. That's why security is code, not prompts.
_DANGEROUS_CODE_PATTERNS = [
    (r"os\.system\s*\(", "os.system() — arbitrary shell execution"),
    (r"os\.popen\s*\(", "os.popen() — shell execution"),
    (r"subprocess", "subprocess module — spawning system processes"),
    (r"shutil\.rmtree\s*\(", "shutil.rmtree() — destructive directory deletion"),
    (r"os\.remove\s*\(", "os.remove() — file deletion"),
    (r"os\.unlink\s*\(", "os.unlink() — file deletion"),
    (r"\beval\s*\(", "eval() — dynamic code execution"),
    (r"\bexec\s*\(", "exec() — dynamic code execution"),
    (r"__import__\s*\(", "__import__() — hidden dynamic imports"),
    (r"\bctypes\b", "ctypes — raw memory and system access"),
    (r"\brm\s+-rf\b", "rm -rf — destructive shell command"),
]
COMPILED_CODE_PATTERNS = [(re.compile(p, re.IGNORECASE), desc) for p, desc in _DANGEROUS_CODE_PATTERNS]


async def security_scanner_node(state: AgentState) -> Dict[str, Any]:
    """Static security analysis of LLM-generated code BEFORE sandbox execution."""
    code = state.get("current_code", "")

    violations = []
    for pattern, description in COMPILED_CODE_PATTERNS:
        if pattern.search(code):
            violations.append(description)

    # WHY: 'while True:' with no 'break' burns sandbox minutes and looks
    # like a hung app. Soft-block it so the reviewer removes it.
    if re.search(r"while\s+True\s*:", code) and "break" not in code:
        violations.append("infinite loop: 'while True:' with no 'break' statement")

    if violations:
        # WHY: We format violations like an ERROR REPORT on purpose — the
        # existing Reviewer self-healing loop then fixes security issues
        # exactly like it fixes runtime bugs. One loop, two jobs.
        violation_report = (
            "SECURITY VIOLATION — remove or replace these dangerous "
            "operations with safe alternatives:\n- " + "\n- ".join(violations)
        )
        return {
            "security_violation": violation_report,
            "status": "security_violation",
        }

    return {"security_violation": None, "status": "security_clean"}