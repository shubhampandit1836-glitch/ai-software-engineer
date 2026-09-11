from typing import Any, Dict
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.llm import llm_smart, invoke_with_retry
from app.agent.state import AgentState
from app.tools.executor import execute_python_code

# WHY: Hard caps are code, not prompts. The prompt ASKS for few steps,
# but this constant GUARANTEES the cap even if the model disobeys.
MAX_PLAN_STEPS = 5

# WHY: We never write a literal triple-backtick in this file. Markdown fences
# embedded inside a fenced code block can mangle copy-paste (which is exactly
# what broke your last paste). Building the fence from characters makes this
# file copy-proof.
FENCE = "`" * 3


def _to_text(content: Any) -> str:
    """Normalizes LLM content (plain string OR list of content blocks) to text."""
    if isinstance(content, str):
        return content
    return "\n".join(str(item) for item in content)


def _clean(text: str) -> str:
    """Strips reasoning-model <think> blocks so they never leak into output."""
    while "<think>" in text and "</think>" in text:
        start = text.find("<think>")
        end = text.find("</think>") + len("</think>")
        text = text[:start] + text[end:]
    return text.strip()


def _extract_code(content: str) -> str:
    """Pulls pure Python out of a (possibly markdown-wrapped) LLM response."""
    text = _clean(content)
    if FENCE + "python" in text:
        return text.split(FENCE + "python")[1].split(FENCE)[0].strip()
    if FENCE in text:
        return text.split(FENCE)[1].split(FENCE)[0].strip()
    return text


async def planner_node(state: AgentState) -> Dict[str, Any]:
    """Breaks the task into a SHORT plan (max 5 steps, minimum needed)."""
    task = state.get("task_description", "")

    system_prompt = f"""You are an expert AI Software Engineer.
Break down this coding task into a step-by-step plan.

RULES:
- Use the MINIMUM number of steps needed, {MAX_PLAN_STEPS} maximum.
- For simple tasks (for example: swap two numbers), 1 to 2 steps is correct.
- Each step must be one meaningful unit of work.
- The final step must include demonstrating the program with printed output.

Return ONLY a numbered list. Do not write code yet."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Task: {task}"),
    ]
    response = await invoke_with_retry(llm_smart, messages)

    content = _clean(_to_text(response.content))

    plan_steps = [
        line.strip()
        for line in content.split("\n")
        if line.strip() and line.strip()[0].isdigit()
    ]
    # WHY: Hard truncation - the model cannot talk its way past this line.
    plan_steps = plan_steps[:MAX_PLAN_STEPS]

    # WHY: Safe fallback - a valid 1-step plan instead of a crashed run.
    if not plan_steps:
        plan_steps = [f"Write a complete Python program that: {task}"]

    return {
        "plan": plan_steps,
        "current_step_index": 0,
        "status": "planning_complete",
        "messages": [response],
    }


async def coder_node(state: AgentState) -> Dict[str, Any]:
    """Writes COMPLETE runnable code for the current step, with global context."""
    task = state.get("task_description", "")

    # WHY: .get() with defaults + guards = immune to missing keys,
    # which permanently kills the TypedDict access error class.
    plan = state.get("plan") or []
    if not plan:
        plan = [f"Write a complete Python program that: {task}"]

    current_idx = state.get("current_step_index", 0)
    current_idx = min(current_idx, len(plan) - 1)
    current_step = plan[current_idx]
    previous_code = state.get("current_code", "")

    full_plan_str = "\n".join(
        f"{i + 1}. {step}" for i, step in enumerate(plan)
    )

    system_prompt = f"""You are an expert Python developer.

THE OVERALL GOAL:
{task}

THE FULL PLAN:
{full_plan_str}

You are working on STEP {current_idx + 1}:
{current_step}

Code written so far (may be empty):
{previous_code}

RULES (all mandatory):
1. Return the COMPLETE runnable program: all previous work PLUS the current step. Never fragments.
2. Keep the program under 80 lines.
3. The program MUST include a main demonstration block (if __name__ == '__main__':) with print() calls, so running it always produces visible output.
4. Write code that directly achieves THE OVERALL GOAL. Do not write unrelated functions.

Return ONLY the complete Python program in a single markdown code block. No explanations."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="Write the code."),
    ]
    response = await invoke_with_retry(llm_smart, messages)

    return {
        "current_code": _extract_code(_to_text(response.content)),
        "status": "coding_complete",
        "messages": [response],
    }


async def tester_node(state: AgentState) -> Dict[str, Any]:
    """Runs the code in the E2B sandbox. Separates code bugs from infra failures."""
    code = state.get("current_code", "")
    if not code:
        return {"execution_error": "No code generated to test.", "status": "testing_failed"}

    result = await execute_python_code.ainvoke({"code": code})

    # WHY: Infrastructure failure (sandbox down, bad key) - an LLM cannot fix
    # this, so we use a distinct status the router treats as fatal.
    if "Sandbox Connection Error" in result:
        return {
            "execution_error": result,
            "execution_result": None,
            "status": "infrastructure_error",
        }

    if "Execution Error" in result:
        return {
            "execution_error": result,
            "execution_result": None,
            "status": "testing_failed",
        }

    # WHY: Pass - advance the plan index HERE, atomically with the result.
    return {
        "execution_result": result,
        "execution_error": None,
        "status": "testing_passed",
        "current_step_index": state.get("current_step_index", 0) + 1,
        # WHY: Fresh budget of fix attempts for the next plan step.
        "review_attempts": 0,
    }


async def reviewer_node(state: AgentState) -> Dict[str, Any]:
    """Fixes code after runtime errors OR security violations. Counts attempts."""
    task = state.get("task_description", "")
    code = state.get("current_code", "")

    # WHY: One reviewer, two error sources. The security scanner formats
    # violations like an error report precisely so this node can consume both.
    problem = (
        state.get("security_violation")
        or state.get("execution_error")
        or "Unknown failure"
    )

    system_prompt = f"""You are an expert Python debugger.
The overall goal of this program is:
{task}

The program failed checks. Fix it.

FAILURE REPORT:
{problem}

CURRENT CODE:
{code}

RULES:
1. Return the COMPLETE corrected program, not a diff or a fragment.
2. If the failure is a SECURITY VIOLATION, replace the dangerous operation (for example: os.system, subprocess, eval) with a safe alternative or remove it.
3. Keep the main demonstration block (if __name__ == '__main__':) with print() calls.
4. Stay under 80 lines.

Return ONLY the corrected Python program in a single markdown code block."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="Fix the code."),
    ]
    response = await invoke_with_retry(llm_smart, messages)

    return {
        "current_code": _extract_code(_to_text(response.content)),
        # WHY: Clear stale errors so the next scanner/tester verdict is fresh.
        "security_violation": None,
        "execution_error": None,
        "review_attempts": state.get("review_attempts", 0) + 1,
        "status": "coding_complete",
        "messages": [response],
    }