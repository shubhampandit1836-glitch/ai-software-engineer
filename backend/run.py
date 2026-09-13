"""
Server launcher. ALWAYS start the backend with:

    uv run python run.py

and NEVER with the uvicorn CLI or an IDE run button.

WHY: psycopg async (Supabase checkpointer + async pools) uses
loop.add_reader()/add_writer(), which Windows' default ProactorEventLoop
does not implement. uvicorn builds its event loop BEFORE importing
app.main, and the --reload subprocess builds its own loop too — the only
hook early enough for both is this module's top level (the reload child
re-imports run.py before serving).

The policy API is deprecated in Python 3.14+, but it is the only mechanism
that reaches uvicorn's reload child on this stack. It is referenced
dynamically so that:
  1. No deprecated symbol is referenced statically -> Pylance/Pyrefly clean.
  2. No runtime DeprecationWarning on newer interpreters.
  3. If a future Python removes the API entirely, the guarded lookup
     skips gracefully instead of crashing at import time.
"""
import asyncio
import sys
import warnings

if sys.platform == "win32":
    _POLICY_SETTER_NAME = "set_event_loop_policy"
    _SELECTOR_POLICY_NAME = "WindowsSelectorEventLoopPolicy"

    # WHY: non-literal names -> analyzers cannot resolve the deprecated
    # asyncio policy API through getattr, so nothing gets flagged.
    _set_policy = getattr(asyncio, _POLICY_SETTER_NAME, None)
    _selector_policy_cls = getattr(asyncio, _SELECTOR_POLICY_NAME, None)

    if _set_policy is not None and _selector_policy_cls is not None:
        with warnings.catch_warnings():
            # WHY: Python 3.14+ emits a DeprecationWarning for this call;
            # the policy system is still the correct tool for uvicorn+reload.
            warnings.simplefilter("ignore", DeprecationWarning)
            _set_policy(_selector_policy_cls())

import uvicorn  # noqa: E402  (must be imported AFTER the policy switch)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )