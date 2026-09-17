import asyncio
from typing import Any, Dict

from langchain_core.messages import SystemMessage, HumanMessage

from app.core.llm import invoke_with_retry
from app.agent.state import AgentState
from app.core.memory import build_memory_block, get_memories

# WHY: One shared identity block. If the direct answer and the synthesizer
# describe the agent differently, users notice and trust drops.

AGENT_IDENTITY = """You are "AI Software Engineer" — an autonomous coding agent.
Your capabilities:
- You plan, write, test, and debug Python programs (utilities, scripts, and
  small multi-file projects like FastAPI APIs with tests)
- You execute every program in a secure cloud sandbox and verify the output yourself
- You automatically fix your own code when tests fail (up to 3 attempts)
- You remember facts about the user (name, preferences) across conversations

You CANNOT (yet): work in other programming languages, access the internet,
or perform dangerous system operations."""


async def direct_answer_node(state: AgentState) -> Dict[str, Any]:
    """Answers general questions directly — no code, no sandbox. Fast and cheap."""
    task = state["task_description"]

    # WHY to_thread + try/except: memory is an ENHANCEMENT, never a
    # dependency - a DB hiccup must not take down answering.
    try:
        memories = await asyncio.to_thread(get_memories)
    except Exception:
        memories = []
    memory_section = build_memory_block(memories)
    memory_prompt = (
        f"\n{memory_section}\n"
        "Use these memories naturally when relevant - e.g. address the user "
        "by name, respect stated preferences.\n"
        if memory_section
        else ""
    )

    system_prompt = f"""{AGENT_IDENTITY}

    The user asked a general question or started a conversation — they do NOT want
    code right now. Answer directly and helpfully in 3-6 sentences.
    If they ask what you can do, describe your capabilities and suggest 2-3 example
    tasks, such as: "program to swap 2 numbers" or "write a function that checks
    if a string is a palindrome".
    Never write code in this answer.
{memory_prompt}"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=task),
    ]
    response = await invoke_with_retry(messages, tier="fast")
    content = response.content
    if not isinstance(content, str):
        content = "\n".join(str(item) for item in content)

    return {"final_answer": content.strip(), "status": "answered"}


async def synthesizer_node(state: AgentState) -> Dict[str, Any]:
    """Composes the final user-facing answer after a successful coding run."""
    task = state.get("task_description", "")
    files = state.get("files") or {}
    execution_result = state.get("execution_result") or "No output captured."

    # WHY: v3 is file-mode - the answer renders the FILE TREE. current_code
    # is legacy (v2 single-file). The fallback keeps old runs working.
    if not files and state.get("current_code"):
        files = {"main.py": state["current_code"]}

    fence = chr(96) * 3

    # WHY: deterministic assembly in Python - the LLM writes ONLY the
    # explanation. Re-transcribing code through the model corrupts it.
    code_section = ""
    for path in sorted(files.keys()):
        code_section += f"\n\n**{path}**\n{fence}python\n{files[path]}{fence}"

    repo_for_prompt = "\n".join(sorted(files.keys()))

    system_prompt = f"""You are presenting completed work to a non-technical user.
The task was: "{task}"
The project files are: {repo_for_prompt}
The program was written, executed in a secure sandbox, and passed all checks.

Write a 2-3 sentence summary in plain language: what the program does and how
it works. Do NOT include any code. Do NOT include the program output."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="Write the summary."),
    ]
    response = await invoke_with_retry(messages, tier="fast")
    explanation = response.content
    if not isinstance(explanation, str):
        explanation = "\n".join(str(item) for item in explanation)

    final_answer = (
        f"{explanation.strip()}\n"
        f"{code_section}\n\n"
        f"**✅ Verified output:**\n{fence}\n{execution_result}\n{fence}"
    )

    return {"final_answer": final_answer, "status": "synthesized"}


async def failure_answer_node(state: AgentState) -> Dict[str, Any]:
    """Graceful exit when the agent cannot fix the code after 3 review attempts.

    WHY file-mode: v3's coder always sets current_code to "" (legacy v2
    field). The old template rendered an EMPTY code block. The real
    project lives in state['files'] - render the tree plus a preview.
    """
    task = state.get("task_description", "")
    last_error = (
        state.get("execution_error")
        or state.get("security_violation")
        or "Unknown error"
    )
    attempts = state.get("review_attempts", 0)
    files = state.get("files") or {}
    fence = chr(96) * 3

    if files:
        tree = "\n".join(f"- {p}" for p in sorted(files))
        preview = "(showing up to 2 files, first 30 lines each)\n\n"
        for path in sorted(files)[:2]:
            content = "\n".join(files[path].split("\n")[:30])
            # WHY pure-bold filename alone on its line directly above the
            # fence: the renderer attaches filenames only from PURE bold
            # lines - the old '(first 30 lines)' inline suffix defeated
            # detection and every block fell back to the main.py label.
            preview += f"**{path}**\n{fence}python\n{content}\n{fence}\n\n"
    else:
        tree = "(no files were created)"
        preview = ""

    final_answer = (
        f"I attempted to build this program but could not get it passing all "
        f"checks after {attempts} debug attempts.\n\n"
        f"**Task:** {task}\n\n"
        f"**Files created:**\n{tree}\n\n"
        f"**Last failure:**\n{fence}\n{last_error}\n{fence}\n\n"
        f"{preview}"
        f"Tip: try rephrasing the task more simply, or break it into a smaller problem."
    )

    return {"final_answer": final_answer, "status": "failed_final"}