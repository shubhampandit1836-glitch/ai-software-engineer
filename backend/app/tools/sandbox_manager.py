"""Persistent E2B sandbox lifecycle manager (v3).

WHY: One sandbox per graph RUN, not per test. A multi-file project would
reinstall fastapi/pytest on every test cycle otherwise - minutes per
iteration and E2B credits torched. The manager creates lazily (only when
code execution is actually needed), hands the same instance to every
tool call, and guarantees destruction from a single finally block.
"""
import os
import time
from typing import Optional
from e2b_code_interpreter import Sandbox
from dotenv import load_dotenv

load_dotenv()

# Module-level singleton state. WHY: LangGraph nodes are plain functions
# with no constructor to hold a connection - module scope is the standard
# place for run-scoped resources in this architecture.
_sandbox: Optional[Sandbox] = None
_created_at: float = 0.0

# WHY: dead-man's switch. If a graph run somehow leaks a sandbox (crash
# outside our finally, hosting hiccup), E2B's own timeout reaps it anyway
# (default 300s idle). This constant documents our ceiling and lets us
# refuse to reuse a sandbox older than it.
MAX_SANDBOX_AGE_SECONDS = 30 * 60


def get_sandbox() -> Sandbox:
    """Returns the run's sandbox, creating it on first use (lazy init).

    WHY lazy: general questions and guardrail rejections never touch the
    sandbox - they must not pay the ~2s creation cost or E2B credits.
    """
    global _sandbox, _created_at

    if _sandbox is None:
        api_key = os.getenv("E2B_API_KEY")
        if not api_key:
            raise RuntimeError("E2B_API_KEY not found in environment variables.")
                # WHY timeout=900: E2B's default sandbox timeout (~5 min) would kill
        # multi-file runs mid-flight - a milestone run (scaffold + pip
        # install + pytest cycles) legitimately takes 3-8 minutes.
        _sandbox = Sandbox(api_key=api_key, timeout=900)  # type: ignore[call-arg]
        _created_at = time.monotonic()
    return _sandbox


def destroy_sandbox() -> None:
    """Idempotent destruction. Safe to call unconditionally in any finally.

    WHY idempotent: cleanup code must never crash the cleanup. Calling
    destroy twice, or destroy-after-never-created, must be silent no-ops.
    """
    global _sandbox, _created_at
    if _sandbox is not None:
        try:
            # WHY hasattr: e2b v1.x renamed close->kill across minors.
            # Same defensive pattern we used in v1's executor.
            if hasattr(_sandbox, "kill"):
                _sandbox.kill()  # type: ignore[union-attr]
            elif hasattr(_sandbox, "close"):
                _sandbox.close()  # type: ignore[union-attr]
        except Exception:
            # WHY: swallow - a sandbox that already died server-side must
            # not crash the graph's cleanup path.
            pass
        finally:
            _sandbox = None
            _created_at = 0.0


def is_sandbox_active() -> bool:
    """True if a sandbox exists and is under the age ceiling."""
    return _sandbox is not None and (time.monotonic() - _created_at) < MAX_SANDBOX_AGE_SECONDS