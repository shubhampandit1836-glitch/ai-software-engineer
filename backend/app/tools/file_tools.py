"""File and command tools for the persistent sandbox (v3).

FOUR security layers now (see safety.py for layers 1-3). This file is
layer 4: the command allowlist. Every file write passes through the
security scanner (layer 3) BEFORE reaching the sandbox; every command
is regex-parsed against the allowlist - prefix matching alone would let
'pip install x && rm -rf /' through.
"""
import re
from typing import Any, Dict, Optional
from langchain_core.tools import tool
from app.tools.sandbox_manager import get_sandbox
from app.agent.safety import COMPILED_CODE_PATTERNS

# ---------- Layer 4: command allowlist ----------

# WHY allowlist not blocklist: a blocklist must predict every dangerous
# command. An allowlist only admits what we have explicitly approved.
ALLOWED_COMMANDS = {
    "pip", "python", "python3", "pytest", "ls", "cat", "find", "tree",
    "pwd", "echo",
}

# WHY compound commands are rejected wholesale: safely parsing
# 'pip install x && <anything>' is a rabbit hole of shell quoting rules.
# Reject, don't gamble.
_CHAIN_OPERATORS = re.compile(r"[;&|<>]")

# Commands that REQUIRE a subcommand from this set (pip install=ok,
# pip download executing arbitrary setup.py = not ok).
_REQUIRED_SUBCOMMANDS = {"pip": {"install", "list", "show", "freeze"}}


def validate_command(cmd: str) -> Optional[str]:
    """Returns None if allowed, or a human-readable rejection reason."""
    if _CHAIN_OPERATORS.search(cmd):
        return "Rejected: chaining/redirect operators (;, &, |, <, >) are not allowed."

    tokens = cmd.split()
    if not tokens:
        return "Rejected: empty command."

    base = tokens[0]
    if base not in ALLOWED_COMMANDS:
        return (
            f"Rejected: '{base}' is not on the allowed list "
            f"(pip install/list/show/freeze, python, python3, pytest, ls, cat, find, tree, pwd, echo)."
        )

    required_sub = _REQUIRED_SUBCOMMANDS.get(base)
    if required_sub:
        if len(tokens) < 2 or tokens[1] not in required_sub:
            return f"Rejected: '{base}' requires one of: {', '.join(sorted(required_sub))}."

    return None


def scan_code_for_violations(code: str) -> Optional[str]:
    """Runs layer-3 patterns over arbitrary file content (pre-sandbox).

    WHY this exists separately: security_scanner_node is a graph node
    operating on AgentState; tools are stateless functions. Both use the
    SAME compiled patterns - single source of truth for what's dangerous.
    """
    violations = []
    for pattern, description in COMPILED_CODE_PATTERNS:
        if pattern.search(code):
            violations.append(description)
    if re.search(r"while\s+True\s*:", code) and "break" not in code:
        violations.append("infinite loop: 'while True:' with no 'break' statement")
    if violations:
        return (
            "SECURITY VIOLATION - remove or replace these dangerous operations "
            "with safe alternatives:\n- " + "\n- ".join(violations)
        )
    return None


# ---------- Tools ----------

@tool
def write_file(path: str, content: str) -> str:
    """Writes a file into the project sandbox. Every write is security-scanned first.

    Args:
        path: Relative path, e.g. 'src/main.py' or 'tests/test_app.py'
        content: Full file contents.
    """
    # Layer 4a: path traversal - '../' escapes the project directory.
    if ".." in path or path.startswith("/"):
        return "Error: absolute paths and '..' are not allowed. Use relative paths like 'src/main.py'."

    # Layer 3: same patterns as the graph's security scanner.
    violation = scan_code_for_violations(content)
    if violation:
        return f"Write blocked.\n{violation}"

    try:
        sb = get_sandbox()
        sb.files.write(path, content)  # type: ignore[union-attr]
        return f"OK: wrote {path} ({len(content)} chars)."
    except Exception as e:
        return f"Sandbox write error for {path}: {str(e)}"


@tool
def read_file(path: str) -> str:
    """Reads a file from the project sandbox."""
    if ".." in path or path.startswith("/"):
        return "Error: absolute paths and '..' are not allowed."
    try:
        sb = get_sandbox()
        content = sb.files.read(path)  # type: ignore[union-attr]
        return content if content else "(empty file)"
    except Exception as e:
        return f"Sandbox read error for {path}: {str(e)}"


@tool
def list_files() -> str:
    """Lists all files in the sandbox project directory (the repo map)."""
    try:
        sb = get_sandbox()
        result = sb.commands.run("find . -type f -not -path './.venv/*' -not -path './.pytest_cache/*' -not -path '*/__pycache__/*' | sort")  # type: ignore[union-attr]
        if result.exit_code == 0:
            return result.stdout or "(no files found)"
        return f"List error: {result.stderr}"
    except Exception as e:
        return f"Sandbox list error: {str(e)}"


@tool
def run_command(command: str) -> str:
    """Runs an allowlisted shell command in the sandbox (pip install, python, pytest, ls, cat, find, tree, pwd, echo).

    Args:
        command: e.g. 'pip install fastapi', 'python -m pytest -v'
    """
    rejection = validate_command(command)
    if rejection:
        return rejection

    try:
        sb = get_sandbox()
        result = sb.commands.run(command)  # type: ignore[union-attr]
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        # WHY truncate: pytest -v on a big project can be 50KB of output -
        # that flows straight into LLM context and eats the token budget.
        # 8KB chars is roughly 2K tokens, plenty for diagnosing failures.
        MAX_OUTPUT = 8000
        output = (stdout + ("\n--- stderr ---\n" + stderr if stderr else "")).strip()
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n...[output truncated]"
        if result.exit_code == 0:
            return f"EXIT 0\n{output}"
        return f"EXIT {result.exit_code} (FAILED)\n{output}"
    except Exception as e:
        return f"Sandbox command error: {str(e)}"