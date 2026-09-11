from typing import Any, Dict
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.llm import llm_fast, invoke_with_retry
from app.agent.state import AgentState

# WHY: One shared identity block. If the direct answer and the synthesizer
# describe the agent differently, users notice and trust drops.

AGENT_IDENTITY = """You are "AI Software Engineer" — an autonomous coding agent.
Your capabilities:
- You plan, write, test, and debug SMALL Python programs (algorithms, utilities, scripts)
- You execute every program in a secure cloud sandbox and verify the output yourself
- You automatically fix your own code when tests fail (up to 3 attempts)

You CANNOT (yet): build multi-file applications, work in other programming
languages, access the internet, or perform dangerous system operations."""

async def direct_answer_node(state: AgentState) -> Dict[str, Any]:
    """Answers general questions directly — no code, no sandbox. Fast and cheap."""
    task = state["task_description"]

    system_prompt = f"""{AGENT_IDENTITY}

    The user asked a general question or started a conversation — they do NOT want
    code right now. Answer directly and helpfully in 3-6 sentences.
    If they ask what you can do, describe your capabilities and suggest 2-3 example
    tasks, such as: "program to swap 2 numbers" or "write a function that checks
    if a string is a palindrome".
    Never write code in this answer."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=task),
    ]
    response = await invoke_with_retry(llm_fast, messages)
    content = response.content
    if not isinstance(content, str):
        content = "\n".join(str(item) for item in content)
        
    return {"final_answer": content.strip(), "status": "answered"}

async def synthesizer_node(state: AgentState) -> Dict[str, Any]:
    """Composes the final user-facing answer after a successful coding run."""
    task = state.get("task_description"), ""
    code = state.get("current_code", "")
    execution_result = state.get("execution_result") or "No output captured"
    
    # WHY: The LLM writes ONLY the explanation. The code and output are
    # assembled deterministically in Python below — an LLM re-transcribing
    # code WILL eventually corrupt it (dropped lines, renamed variables).
    system_prompt = f"""You are presenting completed work to a non-technical user.
    The task was: "{task}"
    The program was written, executed in a secure sandbox, and passed all checks.

    Write a 2-3 sentence summary in plain language: what the program does and how
    it works. Do NOT include any code. Do NOT include the program output."""
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Program code:\n```python\n{code}\n```"),
    ]
    
    response = await invoke_with_retry(llm_fast, messages)
    explanation = response.content
    if not isinstance(explanation, str):
        explanation = "\n".join(str(item) for item in explanation)

    final_answer = (
        f"{explanation.strip()}\n\n"
        f"```python\n{code}\n```\n\n"
        f"**✅ Verified output:**\n```\n{execution_result}\n```"
    )

    return {"final_answer": final_answer, "status": "synthesized"}

async def failure_answer_node(state: AgentState) -> Dict[str, Any]:
    """Graceful exit when the agent cannot fix the code after 3 review attempts.

    WHY: Deterministic template, zero LLM calls - we are likely rate-limited
    or facing a genuinely hard problem, so burning more tokens helps no one.
    The user still gets an honest, structured answer with the last error attached.
    """
    task = state.get("task_description", "")
    last_error = (
        state.get("execution_error")
        or state.get("security_violation")
        or "Unknown error"
    )
    attempts = state.get("review_attempts", 0)
    code = state.get("current_code", "")

    # WHY: Fence built from a char code, not literal triple backticks -
    # keeps this file copy-paste-proof.
    fence = chr(96) * 3

    final_answer = (
        f"I attempted to build this program but could not get it passing all "
        f"checks after {attempts} debug attempts.\n\n"
        f"**Task:** {task}\n\n"
        f"**Last failure:**\n{fence}\n{last_error}\n{fence}\n\n"
        f"**Best attempt so far:**\n{fence}python\n{code}\n{fence}\n\n"
        f"Tip: try rephrasing the task more simply, or break it into a "
        f"smaller problem."
    )

    return {"final_answer": final_answer, "status": "failed_final"}