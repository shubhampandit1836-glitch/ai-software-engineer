"""Persistent E2B sandbox lifecycle manager (v4a.2).

WHY per-thread registry (not a module singleton): once the server handles
concurrent requests, a single _sandbox global would let request A destroy
request B's sandbox. A dict keyed by thread_id gives each request its own
isolated slot while keeping the same lazy-create / guaranteed-destroy pattern.

WHY ContextVar (not a global, not threading.local): async tasks inherit
their parent's ContextVar values automatically. set_current_thread() is
called once at the top of stream_agent_execution; every node downstream
reads the same thread_id without being passed it explicitly.
"""
import os
import time
from contextvars import ContextVar
from typing import Dict, Optional

from e2b_code_interpreter import Sandbox
from dotenv import load_dotenv

load_dotenv()

# ContextVar holding the active thread's id for the current async task tree.
_current_thread_id: ContextVar[Optional[str]] = ContextVar("current_thread_id", default=None)

# Per-thread sandbox registry.
_sandboxes: Dict[str, Sandbox] = {}
_created_at: Dict[str, float] = {}

# WHY: dead-man's switch. E2B's own timeout reaps leaked sandboxes anyway;
# this constant documents our ceiling and lets us refuse to reuse a stale one.
MAX_SANDBOX_AGE_SECONDS = 30 * 60


# ---------- Thread routing ----------

def set_current_thread(thread_id: str) -> None:
    """Binds thread_id to the current async task tree via ContextVar."""
    _current_thread_id.set(thread_id)


def get_current_thread() -> Optional[str]:
    """Returns the thread_id bound to the current task, or None."""
    return _current_thread_id.get()


def _resolve_thread_id() -> str:
    """Returns the active thread id, falling back to 'default' for legacy callers."""
    tid = _current_thread_id.get()
    return tid if tid else "default"


# ---------- Sandbox lifecycle ----------

def get_sandbox() -> Sandbox:
    """Returns the sandbox for the current thread, creating it on first use.

    WHY lazy: general questions and guardrail rejections never touch the
    sandbox - they must not pay the ~2s creation cost or E2B credits.
    """
    tid = _resolve_thread_id()

    if tid not in _sandboxes:
        api_key = os.getenv("E2B_API_KEY")
        if not api_key:
            raise RuntimeError("E2B_API_KEY not found in environment variables.")
        # WHY timeout=900: E2B's default sandbox timeout (~5 min) would kill
        # multi-file runs mid-flight - a milestone run (scaffold + pip
        # install + pytest cycles) legitimately takes 3-8 minutes.
        _sandboxes[tid] = Sandbox(api_key=api_key, timeout=900)  # type: ignore[call-arg]
        _created_at[tid] = time.monotonic()

    return _sandboxes[tid]


def destroy_sandbox(thread_id: Optional[str] = None) -> None:
    """Idempotent destruction. Safe to call unconditionally in any finally.

    thread_id=None  -> destroy ALL slots (server shutdown / nuclear option).
    thread_id=<id>  -> destroy only that slot (per-request cleanup).

    WHY idempotent: cleanup code must never crash the cleanup. Calling
    destroy twice, or destroy-after-never-created, must be silent no-ops.
    """
    targets = list(_sandboxes.keys()) if thread_id is None else [thread_id]

    for tid in targets:
        sb = _sandboxes.pop(tid, None)
        _created_at.pop(tid, None)
        if sb is not None:
            try:
                if hasattr(sb, "kill"):
                    sb.kill()  # type: ignore[union-attr]
                elif hasattr(sb, "close"):
                    sb.close()  # type: ignore[union-attr]
            except Exception:
                # WHY: swallow - a sandbox that already died server-side must
                # not crash the graph's cleanup path.
                pass


def is_sandbox_active(thread_id: Optional[str] = None) -> bool:
    """True if a sandbox exists for the given thread and is under the age ceiling.

    thread_id=None -> checks the current ContextVar thread (legacy form).
    """
    if thread_id is None:
        tid = _resolve_thread_id()
    else:
        tid = thread_id

    if not tid or tid not in _sandboxes:
        return False
    return (time.monotonic() - _created_at.get(tid, 0.0)) < MAX_SANDBOX_AGE_SECONDS


def active_count() -> int:
    """Returns the number of currently live sandbox slots."""
    return len(_sandboxes)