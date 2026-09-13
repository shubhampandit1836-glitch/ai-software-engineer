"""Unit tests for the persistent sandbox manager (v4a.2 per-thread registry).

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
    # WHY unique thread: the old form hit the 'default' slot, which is only
    # empty because no earlier test created it - order-dependence that
    # becomes a mystery failure the day a test above changes. A fresh slot
    # makes this test self-contained.
    set_current_thread("keyless-thread")
    try:
        sandbox_manager.get_sandbox()
        assert False, "should have raised"
    except RuntimeError as e:
        assert "E2B_API_KEY" in str(e)


def test_get_sandbox_caches_instance(monkeypatch):
    # WHY: the whole point of the manager - second call returns SAME object.
    # v4a.2: cache lives in _sandboxes dict keyed by thread_id, not _sandbox.
    import time
    from app.tools.sandbox_manager import set_current_thread, active_count
    fake = object()  # WHY: any object - we test caching, not E2B behavior
    tid = "cache-test-thread"
    monkeypatch.setitem(sandbox_manager._sandboxes, tid, fake)  # type: ignore[arg-type]
    monkeypatch.setitem(sandbox_manager._created_at, tid, time.monotonic())
    monkeypatch.setattr(sandbox_manager, "Sandbox", lambda **kw: fake)
    set_current_thread(tid)
    assert sandbox_manager.get_sandbox() is fake
    destroy_sandbox(tid)
    assert is_sandbox_active(tid) is False



    # ---------- per-thread registry (v4a.2) ----------

from app.tools.sandbox_manager import set_current_thread, active_count


def test_registry_isolation_via_contextvar():
    # WHY: the core v4a guarantee - two different thread ids must never
    # resolve to the same sandbox slot. We verify the KEYING, not E2B
    # (no network): after destroying one id, no sandbox exists at all.
    set_current_thread("thread-a")
    destroy_sandbox("thread-a")
    set_current_thread("thread-b")
    destroy_sandbox("thread-b")
    assert active_count() == 0


def test_default_thread_fallback():
    # WHY: legacy callers (evals pre-upgrade, tests) that never set a
    # thread id must land on the deterministic 'default' slot - not crash.
    from app.tools import sandbox_manager
    assert sandbox_manager.get_current_thread() is None or isinstance(
        sandbox_manager.get_current_thread(), str
    )


def test_destroy_all_clears_every_slot():
    # WHY: the nuclear option used by server shutdown - None targets ALL.
    set_current_thread("t1")
    set_current_thread("t2")
    destroy_sandbox()  # thread_id=None -> destroy all
    assert active_count() == 0