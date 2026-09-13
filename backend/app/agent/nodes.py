from typing import Any, Dict
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.llm import llm_smart, invoke_with_retry
from app.agent.state import AgentState
from app.tools.file_tools import write_file, read_file, list_files, run_command

# WHY: Hard caps are code, not prompts. The prompt ASKS for few steps,
# but this constant GUARANTEES the cap even if the model disobeys.
MAX_PLAN_STEPS = 5

# WHY: fence built from chars - no literal triple-backticks in prompts,
# which keeps this file copy-paste-proof (the lesson from the v2.5 bug).
FENCE = chr(96) * 3


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

def _strip_tool_calls(text: str) -> str:
    """Removes <tool_call>...</tool_call> blocks some models emit when
    trying to 'act' instead of writing files (observed corrupting main.py)."""
    while "<tool_call>" in text and "</tool_call>" in text:
        start = text.find("<tool_call>")
        end = text.find("</tool_call>") + len("</tool_call>")
        text = text[:start] + text[end:]
    return text

def _extract_code(content: str) -> str:
    """Pulls pure Python out of a (possibly markdown-wrapped) LLM response."""
    text = _clean(content)
    if FENCE + "python" in text:
        return text.split(FENCE + "python")[1].split(FENCE)[0].strip()
    if FENCE in text:
        return text.split(FENCE)[1].split(FENCE)[0].strip()
    return text


def _render_repo(files: Dict[str, str]) -> str:
    """Renders the full repository - names AND contents - for the coder.

    WHY contents, not just names: the old name-only map meant each coding
    step REGENERATED files blind, from the task description alone. The
    reviewer's fixes from earlier steps lived in state['files'] but were
    invisible to the coder - so each new step could re-break what review
    had already repaired (observed in the milestone trace: every step's
    first test failed). Seeing real contents turns each step into an
    incremental modification instead of a fresh dice roll.
    """
    if not files:
        return "(no files yet)"
    parts = []
    for path in sorted(files.keys()):
        content = files[path]
        lines = content.split("\n")
        # WHY bounded: prompt hygiene. Our 80-line file cap makes this
        # cheap, but the bound guarantees it stays cheap if caps change.
        if len(lines) > 90:
            content = "\n".join(lines[:90]) + "\n... (truncated)"
        parts.append(f"--- {path} ---\n{content}")
    return "\n\n".join(parts)

def _strip_wrapping_fences(lines: list[str]) -> list[str]:
    """Removes markdown fences the LLM wraps around file CONTENT.

    WHY: the model habitually wraps content in ```python ... ``` INSIDE the
    FILE/ENDFILE block. Those fences are not Python - written to the sandbox
    they become a SyntaxError on line 1. We only strip when BOTH first and
    last lines are fences (the wrap pattern), so a Python file that
    legitimately contains a fence mid-file is untouched.
    """
    if len(lines) >= 2:
        first = lines[0].strip()
        last = lines[-1].strip()
        if first.startswith(FENCE) and (last == FENCE or last.startswith(FENCE)):
            return lines[1:-1]
    return lines


def _parse_file_operations(raw: str) -> Dict[str, str]:
    """Parses the coder's FILE:-delimited output into {path: content}.

    WHY a state machine, not regex: the same lesson as the markdown fence
    parser - structure-in-text must be parsed line-by-line with state,
    because file CONTENT can contain anything, including lines that look
    like delimiters. Format:
        FILE: path/to/file.py
        (content lines...)
        ENDFILE
    """
    files: Dict[str, str] = {}
    current_path = None
    buffer: list[str] = []

    for line in raw.split("\n"):
        stripped = line.strip()
        if stripped.startswith("FILE:"):
            if current_path is not None:
                content_lines = _strip_wrapping_fences(buffer)
                files[current_path] = "\n".join(content_lines).strip()
            current_path = stripped[5:].strip()
            buffer = []
        elif stripped == "ENDFILE":
            if current_path is not None:
                content_lines = _strip_wrapping_fences(buffer)
                files[current_path] = "\n".join(content_lines).rstrip() + "\n"
                current_path = None
                buffer = []
        elif current_path is not None:
            buffer.append(line)

    if current_path is not None:
        content_lines = _strip_wrapping_fences(buffer)
        files[current_path] = "\n".join(content_lines).strip()

    return files


async def planner_node(state: AgentState) -> Dict[str, Any]:
    """Breaks the task into a SHORT plan (max 5 steps, minimum needed)."""
    task = state.get("task_description", "")

    system_prompt = f"""You are an expert AI Software Engineer.
Break down this coding task into a step-by-step plan.

RULES:
- Use the MINIMUM number of steps needed, {MAX_PLAN_STEPS} maximum.
- For simple tasks (for example: swap two numbers), 1 to 2 steps is correct.
- For multi-file projects: first scaffold the structure, then implement
  each component, then write tests, and the final step must run the tests.
- The final step must always verify the work (run the program or run tests).

Return ONLY a numbered list. Do not write code yet."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Task: {task}"),
    ]
    response = await invoke_with_retry(messages, tier="smart")

    content = _clean(_to_text(response.content))

    plan_steps = [
        line.strip()
        for line in content.split("\n")
        if line.strip() and line.strip()[0].isdigit()
    ]
    plan_steps = plan_steps[:MAX_PLAN_STEPS]

    if not plan_steps:
        plan_steps = [f"Write a complete Python program that: {task}"]

    return {
        "plan": plan_steps,
        "current_step_index": 0,
        # WHY resets: once the checkpointer (4a.3) persists state across
        # messages in a thread, a FAILED run would leave review_attempts=3
        # in storage - and the next task in that thread would inherit a
        # burned-out budget and fail instantly. Every new task starts with
        # a clean slate: fresh file tree, zero attempts, no stale errors.
        "files": {},
        "review_attempts": 0,
        "execution_error": None,
        "security_violation": None,
        "status": "planning_complete",
        "messages": [response],
    }


async def coder_node(state: AgentState) -> Dict[str, Any]:
    """Writes project FILES for the current step, with full repo context.

    Output contract (strict):
        FILE: path
        ...content...
        ENDFILE
    Repeated per file. Anything outside FILE/ENDFILE is treated as
    preamble and ignored (but we prefer none).
    """
    task = state.get("task_description", "")
    plan = state.get("plan") or [f"Write a complete Python program that: {task}"]
    current_idx = min(state.get("current_step_index", 0), len(plan) - 1)
    current_step = plan[current_idx]
    files = state.get("files") or {}

    system_prompt = f"""You are an expert Python developer building a project.

THE OVERALL GOAL:
{task}

THE FULL PLAN:
{chr(10).join(f"{i + 1}. {s}" for i, s in enumerate(plan))}

You are working on STEP {current_idx + 1}:
{current_step}

CURRENT REPOSITORY (real contents of files built so far):
{_render_repo(files)}

RULES (all mandatory):
1. Files listed in the repository above already exist and have passed
   tests. MODIFY them minimally based on their actual contents - do NOT
   rewrite working code from scratch, and do NOT change behavior earlier
   steps already verified.
2. Output EVERY file you create or modify in this step, COMPLETE, using
   this exact format (nothing outside it):
FILE: path/name.py
<complete file contents>
ENDFILE
3. Repeat the FILE/ENDFILE block for each file in this step.
4. NEVER output diffs or fragments - always the complete file.
5. All source files must be importable modules. For web frameworks
   (FastAPI/Flask), do NOT include uvicorn.run or any server-startup
   code - tests use TestClient, which needs no running server, and a
   blocking server start would hang the sandbox.
6. Test files go in tests/ and use pytest (functions named test_*).
   Tests MUST be independent: never rely on state from other tests - reset
   shared globals in a fixture, and capture ids from responses instead of
   hardcoding them.
7. Stay under 80 lines per file.

Output the FILE blocks now."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="Write the files for this step."),
    ]
    response = await invoke_with_retry(messages, tier="smart")

    raw = _strip_tool_calls(_clean(_to_text(response.content)))
    new_files = _parse_file_operations(raw)

    # WHY: if parsing produced nothing (model ignored the format), fall
    # back to treating the whole response as ONE file - the run degrades
    # to single-file mode instead of dying.
    if not new_files:
        code = _extract_code(raw)
        if code:
            new_files = {"main.py": code}

    # Materialize into the sandbox now (writes are scanner-gated inside
    # write_file - a violation returns an error string, NOT an exception).
    write_results = []
    for path, content in new_files.items():
        # WHY: ainvoke - sync tool in a worker thread, event loop stays free
        result = await write_file.ainvoke({"path": path, "content": content})
        write_results.append(result)

    # WHY: mirror into state AFTER attempting writes. Even blocked files
    # get mirrored - the tester/reviewer will see the block reason and
    # the reviewer fixes the file. State = attempted truth.
    merged = {**(files), **new_files}

    # If ANY write was blocked by security, surface it as the status the
    # router already knows how to handle - reviewer gets invoked.
    blocked = [r for r in write_results if "blocked" in r or "Error" in r or "error" in r]
    if blocked:
        return {
            "files": merged,
            "current_code": "",
            "execution_error": "\n".join(blocked),
            "status": "testing_failed",  # routes to reviewer with the reason
            "messages": [response],
        }

    return {
        "files": merged,
        "current_code": "",
        "status": "coding_complete",
        "messages": [response],
    }


# WHY: map imports to pip packages - install ONLY what the project uses.
# A cold bundle install (fastapi+uvicorn+pytest+requests+pydantic) can
# take 30s+ in the sandbox; one needed package takes seconds.
_DEP_IMPORTS = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pytest": "pytest",
    "requests": "requests",
    "pydantic": "pydantic",
}


async def tester_node(state: AgentState) -> Dict[str, Any]:
    """Runs the project in the persistent sandbox: deps once, then pytest.

    Verdict logic: exit code, not stdout vibes.
    """
    files = state.get("files") or {}
    if not files:
        return {"execution_error": "No files generated to test.", "status": "testing_failed"}

    has_tests = any(p.startswith("tests/") for p in files)

    # --- Detect dependencies from actual imports ---
    all_content = "\n".join(files.values())
    to_install = [
        pkg
        for mod, pkg in _DEP_IMPORTS.items()
        if f"import {mod}" in all_content or f"from {mod}" in all_content
    ]
    # WHY: plain-assert test files never 'import pytest', but running them
    # REQUIRES pytest. If tests exist, pytest is a hard dependency.
    if has_tests and "pytest" not in to_install:
        to_install.append("pytest")
    # WHY: FastAPI's TestClient needs httpx at runtime - a transitive
    # dependency that only explodes inside the test run.
    if "TestClient" in all_content and "httpx" not in to_install:
        to_install.append("httpx")

    if to_install:
        # WHY 'python -m pip' and NOT bare 'pip': bare pip can be absent
        # from PATH in the sandbox image -> instant EXIT 127 ->
        # infrastructure_error -> graceful give-up. python -m pip is tied
        # to the interpreter the debug script just proved works.
        install = await run_command.ainvoke(
            {"command": "python -m pip install " + " ".join(sorted(to_install))}
        )
        if "EXIT 0" not in install:
            return {
                "execution_error": f"Dependency install failed:\n{install}",
                "execution_result": None,
                "status": "infrastructure_error",
            }

    # --- Decide the test command ---
    if has_tests:
        command = "python -m pytest -v"
    else:
        mains = [p for p in files if p.endswith(".py") and "/" not in p]
        if not mains:
            return {
                "execution_error": "No runnable entry point found (no tests/, no top-level .py).",
                "execution_result": None,
                "status": "testing_failed",
            }
        # WHY: prefer conventional entry points - dict order is arbitrary,
        # and running 'models.py' as the smoke test when a main.py exists
        # tests the wrong thing
        entry = next(
            (p for p in ("main.py", "app.py") if p in files), mains[0]
        )
        command = "python " + entry

    # WHY: ainvoke, not invoke - a sync .invoke inside an async node BLOCKS
    # the event loop. pip install can run 20-30s; that would freeze every
    # other request AND the SSE stream. ainvoke runs the sync tool in a
    # worker thread.
    result = await run_command.ainvoke({"command": command})

    if "Sandbox command error" in result:
        return {
            "execution_error": result,
            "execution_result": None,
            "status": "infrastructure_error",
        }

    if "EXIT 0" in result:
        return {
            "execution_result": result,
            "execution_error": None,
            "status": "testing_passed",
            "current_step_index": state.get("current_step_index", 0) + 1,
            "review_attempts": 0,
        }

    return {
        "execution_result": None,
        "execution_error": result,
        "status": "testing_failed",
    }


async def reviewer_node(state: AgentState) -> Dict[str, Any]:
    """Patches FILES using the failure report + full repo context."""
    task = state.get("task_description", "")
    files = state.get("files") or {}

    problem = (
        state.get("security_violation")
        or state.get("execution_error")
        or "Unknown failure"
    )

    # WHY read from the sandbox mirror (state), not the live sandbox:
    # state is what the graph believes; if writes failed, the file is
    # NOT in state and the reviewer must REWRITE it, not patch it.
    repo_listing = _render_repo(files)

    # WHY full contents, not previews: the old 40-line/mentioned-files
    # logic starved the reviewer TWICE in the milestone failure - the
    # traceback only names the TEST file (so app.py was never shown at
    # all), and the failing test sat at line 41, one line past the
    # preview window. Generated projects are small (<=80 lines/file,
    # few files) so full context is cheap and eliminates both blind spots.
    file_contents = ""
    for path in sorted(files.keys()):
        file_contents += f"\n--- {path} ---\n{files[path]}\n"

    system_prompt = f"""You are an expert Python debugger.
The overall goal of this project is:
{task}

REPOSITORY:
{repo_listing}

The project failed checks. Fix it.

FAILURE REPORT:
{problem}
{file_contents}

RULES:
0. First state the root cause in ONE line starting with 'ROOT CAUSE:'.
   Check for these common culprits: state leaking between tests (globals
   not reset by fixtures), tests hardcoding ids instead of using returned
   values, wrong status codes, missing imports.
1. Output corrected files in this exact format, COMPLETE (no diffs):
FILE: path/name.py
<complete corrected file contents>
ENDFILE
2. Only output files that need changing.
3. If the failure is a SECURITY VIOLATION, replace the dangerous operation
   with a safe alternative or remove it.
4. Keep all tests in tests/ passing. Tests must be independent: never rely
   on state from a previous test - reset shared globals in fixtures and
   capture ids from responses instead of hardcoding them.
5. Stay under 80 lines per file.

First the ROOT CAUSE line, then the FILE blocks."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="Fix the project."),
    ]
    response = await invoke_with_retry(messages, tier="smart")

    raw = _strip_tool_calls(_clean(_to_text(response.content)))
    fixed_files = _parse_file_operations(raw)

    write_results = []
    for path, content in fixed_files.items():
        # WHY: ainvoke - sync tool in a worker thread, event loop stays free
        result = await write_file.ainvoke({"path": path, "content": content})
        write_results.append(result)

    merged = {**files, **fixed_files}

    return {
        "files": merged,
        "current_code": "",
        # WHY keep the error: the router and failure_answer still read it
        # if the NEXT test fails again. Cleared only on test pass.
        "execution_error": None,
        "security_violation": None,
        "review_attempts": state.get("review_attempts", 0) + 1,
        "status": "coding_complete",
        "messages": [response],
    }