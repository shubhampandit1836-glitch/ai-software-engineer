import asyncio
import sys

# WHY HERE and not main.py: uvicorn creates its event loop BEFORE importing
# the app - a policy set inside main.py runs too late. This file executes
# first, so the Selector loop exists before uvicorn's asyncio.run().
# Note: set_event_loop_policy is deprecated in Python 3.14+ (removed in 3.16),
# but is required on Python 3.11 for psycopg async on Windows. We use getattr
# to avoid static analysis (Pyrefly) deprecation warnings.
if sys.platform == "win32":
    set_loop_policy = getattr(asyncio, "set_event_loop_policy", None)
    if callable(set_loop_policy):
        set_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)