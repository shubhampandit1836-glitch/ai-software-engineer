"""Thread + background-run API (v4a.3).

Contract: thread_id is THE key everywhere - ContextVar sandbox routing,
LangGraph checkpoint thread_id, and thread_events storage. One id, four jobs.
"""
import asyncio
import json
import uuid
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# WHY import the MODULE, not the variable: agent_graph_stateful is None at
# import time and only assigned later (lifespan startup). 'from x import y'
# COPIES the current value - the copy would stay None forever (the 503 bug).
# Module attribute access (graph_module.agent_graph_stateful) reads live.
from app.agent import graph as graph_module
from app.agent.graph import RECURSION_LIMIT
from app.agent.state import get_initial_state
from app.core.db import get_pool
from app.tools.sandbox_manager import destroy_sandbox, set_current_thread

router = APIRouter()

# In-memory registry of live background tasks (single-process scope line:
# a server restart loses these; events/results in Postgres survive).
_running_tasks: Dict[str, asyncio.Task[None]] = {}


class RunRequest(BaseModel):
    task: str


def _persist_event(thread_id: str, seq: int, event: Dict[str, Any]) -> None:
    """Inserts one event row. Sync psycopg inside a thread via to_thread.

    WHY to_thread: psycopg's sync driver blocks; calling it on the event
    loop would stall every concurrent request during each insert.
    """
    pool = get_pool()
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO thread_events (thread_id, seq, event) VALUES (%s, %s, %s)",
            (thread_id, seq, json.dumps(event, default=str)),
        )
        conn.commit()


async def _run_agent_background(thread_id: str, task: str) -> None:
    """The background engine: streams the graph, persists every event,
    guarantees sandbox cleanup. Runs detached from any HTTP connection.

    WHY set_current_thread FIRST: every tool call inside this task resolves
    its sandbox through the ContextVar - this line is what makes thread
    isolation real the moment the graph starts executing.
    """
    set_current_thread(thread_id)
    seq = 0
    initial_state = get_initial_state(task)

    try:
        # WHY graph_module: the stateful graph only exists after lifespan
        # startup - module attribute access reads the live value.
        async for mode, payload in graph_module.agent_graph_stateful.astream(  # type: ignore[union-attr]
            initial_state,
            stream_mode=["updates", "messages"],
            config={
                "recursion_limit": RECURSION_LIMIT,
                "configurable": {"thread_id": thread_id},
            },
        ):
            if mode == "messages":
                chunk, meta = payload
                node = meta.get("langgraph_node", "")
                if node in ("direct_answer", "synthesizer"):
                    content = chunk.content
                    if isinstance(content, str) and content:
                        event = {"node": node, "type": "token", "content": content}
                        await asyncio.to_thread(_persist_event, thread_id, seq, event)
                        seq += 1
                continue

            for node_name, node_output in payload.items():
                if not isinstance(node_output, dict):
                    continue
                event = {
                    "node": node_name,
                    "status": node_output.get("status"),
                    "intent": node_output.get("intent"),
                    "plan": node_output.get("plan"),
                    "current_step_index": node_output.get("current_step_index"),
                    "current_code": node_output.get("current_code"),
                    "execution_result": node_output.get("execution_result"),
                    "execution_error": node_output.get("execution_error"),
                    "security_violation": node_output.get("security_violation"),
                    "review_attempts": node_output.get("review_attempts"),
                    "final_answer": node_output.get("final_answer"),
                }
                await asyncio.to_thread(_persist_event, thread_id, seq, event)
                seq += 1

        # THE ONLY completion event - one per run, always last.
        await asyncio.to_thread(
            _persist_event, thread_id, seq, {"node": "end", "status": "completed"}
        )

    except Exception as e:
        # Even a crashed run must produce an end marker - otherwise the
        # frontend polls forever on a dead thread.
        try:
            await asyncio.to_thread(
                _persist_event,
                thread_id,
                seq,
                {"node": "error", "status": "failed", "execution_error": str(e)},
            )
        except Exception:
            pass  # DB down too - nothing more we can do
    finally:
        # Guaranteed cleanup on EVERY exit path - the 4a.2 registry makes
        # this surgical: only THIS thread's sandbox dies.
        destroy_sandbox(thread_id)
        _running_tasks.pop(thread_id, None)


@router.post("/threads")
async def create_thread():
    """Creates a new chat thread. Title defaults; first user message later
    becomes the sidebar title (4a.4 convention)."""
    thread_id = str(uuid.uuid4())
    pool = get_pool()
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO threads (id, title) VALUES (%s, %s)",
            (thread_id, "New chat"),
        )
        conn.commit()
    return {"thread_id": thread_id, "title": "New chat"}


@router.get("/threads")
async def list_threads():
    """Sidebar data: every thread, newest first."""
    pool = get_pool()
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, title, updated_at FROM threads ORDER BY updated_at DESC"
        ).fetchall()
    return {
        "threads": [
            {
                "thread_id": str(row[0]),
                "title": row[1],
                "updated_at": row[2].isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/threads/{thread_id}/run")
async def start_run(thread_id: str, request: RunRequest):
    """Starts a background agent run for this thread. Returns immediately."""
    # WHY 503 not silent fallback: a run that "works" but doesn't persist
    # is a mystery failure later. Loud and explicit beats quietly degraded.
    if graph_module.agent_graph_stateful is None:
        raise HTTPException(
            status_code=503,
            detail="Persistence layer unavailable - restart the server with a working DATABASE_URL.",
        )
    pool = get_pool()

    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id FROM threads WHERE id = %s", (thread_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    if thread_id in _running_tasks and not _running_tasks[thread_id].done():
        # WHY reject instead of queue: one active run per thread is a clear
        # contract. Queueing silently is how out-of-order answers happen.
        raise HTTPException(status_code=409, detail="A run is already active for this thread")

    # First message becomes the title (sidebar convention).
    with pool.connection() as conn:
        conn.execute(
            "UPDATE threads SET title = %s, updated_at = now() "
            "WHERE id = %s AND title = 'New chat'",
            (request.task[:60], thread_id),
        )
        conn.commit()

    task = asyncio.create_task(_run_agent_background(thread_id, request.task))
    _running_tasks[thread_id] = task
    return {"status": "started", "thread_id": thread_id}


@router.get("/threads/{thread_id}/events")
async def get_events(thread_id: str, after: int = 0):
    """Replay + live polling endpoint: all events after seq N, plus run state."""
    pool = get_pool()

    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT seq, event FROM thread_events "
            "WHERE thread_id = %s AND seq > %s ORDER BY seq",
            (thread_id, after),
        ).fetchall()

    is_running = (
        thread_id in _running_tasks and not _running_tasks[thread_id].done()
    )
    return {
        "events": [row[1] for row in rows],
        "last_seq": rows[-1][0] if rows else after,
        "is_running": is_running,
    }