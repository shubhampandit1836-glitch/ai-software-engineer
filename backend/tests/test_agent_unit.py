"""Unit tests for pure agent logic: parsers, scanner, routers, state factory.

WHY: These run in under a second with ZERO network calls - the safe core
of CI. Live integration testing stays in evals/, run on demand.

NOTE: importing app modules constructs LLM clients at import time, so env
vars must exist (your local .env covers this; CI will provide dummies).
No API calls are ever made from this file.
"""

from typing import cast

from app.agent.state import AgentState, get_initial_state
from app.agent.safety import _extract_intent, security_scanner_node
from app.agent.nodes import _extract_code, _clean
from app.agent.nodes import _parse_file_operations
from app.agent.graph import (
    MAX_REVIEW_ATTEMPTS,
    route_after_guardrail,
    route_after_intent,
    route_after_security,
    route_after_testing,
)

# WHY: fence built from chars - the no-literal-backticks rule from
# nodes.py applies to test files too (copy-paste safety).
FENCE = "`" * 3


def _state(**overrides) -> AgentState:
    """Clean state + targeted overrides for one scenario."""
    return cast(AgentState, {**get_initial_state("test"), **overrides})


# ---------- state factory ----------

def test_initial_state_has_all_13_fields():
    s = get_initial_state("hello")
    assert len(s) == 14
    assert s["intent"] == "unknown"
    assert s["review_attempts"] == 0
    assert s["final_answer"] is None


# ---------- _extract_intent ----------

def test_intent_plain():
    assert _extract_intent("coding_task") == "coding_task"

def test_intent_markdown_wrapped():
    assert _extract_intent("**general_question**") == "general_question"

def test_intent_label_in_last_line_wins():
    # WHY: reasoning models conclude at the end - bottom-up scan finds
    # the verdict, not a label mentioned mid-thought
    text = "The user wants code written.\nSo the answer is: coding_task"
    assert _extract_intent(text) == "coding_task"

def test_intent_unparseable_falls_back_to_safe_path():
    # WHY: the fallback must NEVER be a path that reaches code execution
    assert _extract_intent("I cannot decide this one") == "general_question"


# ---------- _clean / _extract_code ----------

def test_clean_strips_think_blocks():
    assert _clean("<think>internal reasoning</think>print('hi')") == "print('hi')"

def test_extract_code_python_fence():
    assert _extract_code(FENCE + "python\nx = 1\n" + FENCE) == "x = 1"

def test_extract_code_bare_fence():
    assert _extract_code("Here you go:\n" + FENCE + "\ny = 2\n" + FENCE) == "y = 2"

def test_extract_code_plain_passthrough():
    assert _extract_code("z = 3") == "z = 3"


# ---------- security scanner (async, zero network) ----------

async def test_scanner_clean_code():
    r = await security_scanner_node(_state(files={"main.py": "print('hello world')"}))
    assert r["status"] == "security_clean"
    assert r["security_violation"] is None

async def test_scanner_flags_subprocess():
    code = "import subprocess\nsubprocess.run(['ls'])"
    r = await security_scanner_node(_state(files={"main.py": code}))
    assert r["status"] == "security_violation"
    assert "subprocess" in r["security_violation"]

async def test_scanner_flags_infinite_loop_without_break():
    r = await security_scanner_node(_state(files={"main.py": "while True:\n    print('spam')"}))
    assert r["status"] == "security_violation"
    assert "infinite loop" in r["security_violation"]

async def test_scanner_allows_loop_with_break():
    r = await security_scanner_node(_state(files={"main.py": "while True:\n    break"}))
    assert r["status"] == "security_clean"

async def test_scanner_violation_names_the_file():
    # WHY: v3 scans a FILE TREE - the report must say WHICH file,
    # or the reviewer is fixing blind
    r = await security_scanner_node(_state(files={"src/util.py": "import os\nos.system('ls')"}))
    assert "src/util.py" in r["security_violation"]


# ---------- routers (pure functions - the graph's decision logic) ----------

def test_route_guardrail_rejected_ends():
    assert route_after_guardrail(_state(status="rejected")) == "__end__"

def test_route_guardrail_clean_goes_to_intent():
    assert route_after_guardrail(_state(status="input_clean")) == "intent_classifier"

def test_route_intent_coding_goes_to_planner():
    s = _state(intent="coding_task", status="intent_classified")
    assert route_after_intent(s) == "planner"

def test_route_intent_general_answered_directly():
    s = _state(intent="general_question", status="intent_classified")
    assert route_after_intent(s) == "direct_answer"

def test_route_intent_unknown_defaults_to_safe_path():
    # WHY: ambiguity must never reach code execution
    s = _state(intent="unknown", status="intent_classified")
    assert route_after_intent(s) == "direct_answer"

def test_route_testing_failed_under_cap_goes_to_reviewer():
    assert route_after_testing(_state(status="testing_failed", review_attempts=1)) == "reviewer"

def test_route_testing_failed_at_cap_gives_up_gracefully():
    s = _state(status="testing_failed", review_attempts=MAX_REVIEW_ATTEMPTS)
    assert route_after_testing(s) == "failure_answer"

def test_route_testing_passed_more_steps_to_coder():
    s = _state(status="testing_passed", plan=["a", "b", "c"], current_step_index=1)
    assert route_after_testing(s) == "coder"

def test_route_testing_passed_all_steps_synthesizes():
    s = _state(status="testing_passed", plan=["a", "b", "c"], current_step_index=3)
    assert route_after_testing(s) == "synthesizer"

def test_route_testing_infra_error_exits_gracefully():
    assert route_after_testing(_state(status="infrastructure_error")) == "failure_answer"

def test_route_security_violation_goes_to_reviewer():
    assert route_after_security(_state(status="security_violation", review_attempts=0)) == "reviewer"

def test_route_security_clean_goes_to_tester():
    assert route_after_security(_state(status="security_clean")) == "tester"

def test_parse_single_file():
    raw = "FILE: main.py\nprint('hi')\nENDFILE"
    assert _parse_file_operations(raw) == {"main.py": "print('hi')\n"}

def test_parse_multiple_files():
    raw = "FILE: a.py\nx = 1\nENDFILE\nFILE: b.py\ny = 2\nENDFILE"
    assert _parse_file_operations(raw) == {"a.py": "x = 1\n", "b.py": "y = 2\n"}

def test_parse_salvages_unterminated_file():
    # WHY: max_tokens cutoff mid-file - we keep the partial, never drop it
    raw = "FILE: main.py\nx = 1\ny = 2"
    result = _parse_file_operations(raw)
    assert "main.py" in result
    assert "x = 1" in result["main.py"]

def test_parse_drops_prose_unterminated_file():
    # WHY: leaked model deliberation masquerading as an unterminated FILE
    # block must NOT be shipped as a file (the todo run's junk main.py bug)
    raw = (
        "FILE: main.py\n"
        "Let me analyze the current state. The test suite is already\n"
        "written in tests, with a fixture in conftest.py. Let me trace\n"
        "through the code for correctness before outputting anything."
    )
    result = _parse_file_operations(raw)
    assert "main.py" not in result

def test_parse_ignores_preamble():
    raw = "Here are the files:\nFILE: a.py\nz = 3\nENDFILE"
    assert _parse_file_operations(raw) == {"a.py": "z = 3\n"}

def test_parse_empty_returns_empty():
    assert _parse_file_operations("no files here at all") == {}


# ---------- _parse_file_operations: fence stripping (v3) ----------

def test_parse_strips_wrapping_python_fence():
    raw = "FILE: main.py\n" + FENCE + "python\nx = 1\n" + FENCE + "\nENDFILE"
    assert _parse_file_operations(raw) == {"main.py": "x = 1\n"}

def test_parse_strips_bare_fence_wrap():
    raw = "FILE: util.py\n" + FENCE + "\ny = 2\n" + FENCE + "\nENDFILE"
    assert _parse_file_operations(raw) == {"util.py": "y = 2\n"}

def test_parse_keeps_interior_fences():
    # WHY: a Python file that CONTAINS fence text mid-file must not be
    # mutilated - only the first+last wrap pattern is stripped
    content = 'doc = "```"'
    raw = f"FILE: gen.py\nprint({content!r})\nENDFILE"
    assert "```" in _parse_file_operations(raw)["gen.py"]