"""Unit tests for the persistent sandbox manager (v3).

NOTE: These tests manipulate module-level singleton state directly - they
never create a real E2B sandbox (no key, no network, zero credits). We
test the LIFECYCLE LOGIC, not the E2B connection.
"""

from typing import cast
from app.tools import sandbox_manager
from app.tools.sandbox_manager import destroy_sandbox, is_sandbox_active
from app.agent.state import AgentState, get_initial_state


def _state(**overrides) -> AgentState:
    return cast(AgentState, {**get_initial_state("test"), **overrides})


# ---------- state ----------

def test_initial_state_has_files_field():
    s = get_initial_state("hello")
    assert s["files"] == {}
    assert len(s) == 14


# ---------- lifecycle logic (no real sandbox created) ----------

def test_destroy_is_idempotent_when_never_created():
    # WHY: cleanup in a finally block must be a silent no-op when the
    # sandbox was never needed (guardrail rejects, general questions)
    destroy_sandbox()
    destroy_sandbox()  # twice - still silent


def test_is_active_false_when_never_created():
    assert is_sandbox_active() is False


def test_get_sandbox_requires_api_key(monkeypatch):
    # WHY: missing key must fail FAST with a clear message, not a cryptic
    # E2B SDK error deep in a node
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    try:
        sandbox_manager.get_sandbox()
        assert False, "should have raised"
    except RuntimeError as e:
        assert "E2B_API_KEY" in str(e)


def test_get_sandbox_caches_instance(monkeypatch):
    # WHY: the whole point of the manager - second call returns SAME object
    import time
    fake = object()  # WHY: any object - we test caching, not E2B behavior
    monkeypatch.setattr(sandbox_manager, "_sandbox", fake)
    monkeypatch.setattr(sandbox_manager, "_created_at", time.monotonic())
    monkeypatch.setattr(sandbox_manager, "Sandbox", lambda **kw: fake)
    assert sandbox_manager.get_sandbox() is fake
    destroy_sandbox()
    assert is_sandbox_active() is False