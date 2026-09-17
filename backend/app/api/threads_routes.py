"""Thread + background-run API (v4a.4 + v4b memory hookup).

Contract: thread_id is THE key everywhere - ContextVar sandbox routing,
LangGraph checkpoint thread_id, and thread_events storage. One id, four jobs.
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict

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
from app.core.memory import extract_and_sync_memories
from app.tools.sandbox_manager import destroy_sandbox, set_current_thread

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory registry of live background tasks (single-process scope line:
# a server restart loses these; events/results in Postgres survive).
_running_tasks: Dict[str, asyncio.Task[None]] = {}

# WHY a separate reference set for memory tasks: asyncio only keeps a weak
# reference to running tasks - an unreferenced task can be garbage-collected
# mid-flight. The done-callback discards the reference on completion.
_memory_tasks: set[asyncio.Task[None]] = set()


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


def _next_seq(pool, thread_id: str) -> int:
    """Next event sequence for a thread (MAX(seq)+1).

    WHY: runs used to restart at seq=0, colliding with the UNIQUE
    (thread_id, seq) constraint on the SECOND message in a thread -
    that run crashed on its first insert and persisted nothing (the
    'old answer comes back' bug). Sequences are continuous ACROSS runs.
    """
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM thread_events WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
    assert row is not None
    return row[0] + 1


def _thread_exists(thread_id: str) -> bool:
    """Sync existence check, offloaded from the async endpoint."""
    pool = get_pool()
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id FROM threads WHERE id = %s", (thread_id,)
        ).fetchone()
    return row is not None


def _record_user_message(thread_id: str, task: str) -> None:
    """Title update + user-message event: ALL sync DB work for start_run.

    WHY one function: the async endpoint offloads every blocking psycopg
    call in a single asyncio.to_thread - a blocking call run directly on
    the event loop stalls every concurrent request AND the in-flight agent
    run (even its LLM awaits: httpx response callbacks need the loop).
    """
    pool = get_pool()
    # First message becomes the title (sidebar convention).
    with pool.connection() as conn:
        conn.execute(
            "UPDATE threads SET title = %s, updated_at = now() "
            "WHERE id = %s AND title = 'New chat'",
            (task[:60], thread_id),
        )
        conn.commit()
    # WHY persist the user's message as an event: the event stream is the
    # single source of truth for the WHOLE conversation - user turns
    # included. Client-only bubbles vanished on every thread switch, and
    # only server-side events can interleave prompts with answers by seq.
    user_seq = _next_seq(pool, thread_id)
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO thread_events (thread_id, seq, event) VALUES (%s, %s, %s)",
            (
                thread_id,
                user_seq,
                json.dumps({"node": "user", "type": "user_message", "content": task}),
            ),
        )
        conn.commit()


def _delete_thread_rows(thread_id: str) -> None:
    """All sync DB deletion for delete_thread (same WHY as above).

    NOTE (v4b): memories are intentionally NOT deleted - they are
    cross-thread by design; deleting a thread never erases what the
    agent learned about the user.
    """
    pool = get_pool()
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id FROM threads WHERE id = %s", (thread_id,)
        ).fetchone()
    if row is None:
        # WHY KeyError, not HTTPException: this runs inside to_thread - a
        # plain exception crosses cleanly and the endpoint maps it to 404.
        raise KeyError(thread_id)
    # WHY separate connection: a failed statement aborts a postgres
    # transaction, so best-effort cleanup gets its own connection.
    try:
        with pool.connection() as conn:
            conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread_id,))
            conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (thread_id,))
            conn.commit()
    except Exception:
        pass  # checkpoint schema varies by version - non-fatal
    with pool.connection() as conn:
        conn.execute("DELETE FROM threads WHERE id = %s", (thread_id,))  # events cascade
        conn.commit()


async def _run_agent_background(thread_id: str, task: str) -> None:
    """The background engine: streams the graph, persists every event,
    guarantees sandbox cleanup. Runs detached from any HTTP connection.

    WHY set_current_thread FIRST: every tool call inside this task resolves
    its sandbox through the ContextVar - this line is what makes thread
    isolation real the moment the graph starts executing.
    """
    set_current_thread(thread_id)
    # WHY phase timing: "the answer is slow" must decompose into LLM time
    # vs DB time vs graph time from the terminal - not guesswork. Every
    # phase logs seconds since run start.
    run_started = time.perf_counter()

    def _mark(label: str) -> None:
        logger.info(
            "run phase: thread=%s t=%.1fs %s",
            thread_id[:8],
            time.perf_counter() - run_started,
            label,
        )

    # WHY to_thread: _next_seq is a blocking SELECT - on the loop it would
    # freeze every concurrent request mid-poll.
    seq = await asyncio.to_thread(_next_seq, get_pool(), thread_id)
    initial_state = get_initial_state(task)
    last_answer = ""
    _mark("engine ready, graph starting")

    try:
        # WHY graph_module: the stateful graph only exists after lifespan
        # startup - module attribute access reads the live value.
        # WHY stream_mode="updates" ONLY: per-token events (v4a.3) meant a
        # 344-char answer = ~80 sequential inserts with the final answer
        # queued behind them (the 15-20s 'hi'). The complete answer already
        # arrives in the node update's final_answer field.
        async for payload in graph_module.agent_graph_stateful.astream(  # type: ignore[union-attr]
            initial_state,
            stream_mode="updates",
            config={
                "recursion_limit": RECURSION_LIMIT,
                "configurable": {"thread_id": thread_id},
            },
        ):
            for node_name, node_output in payload.items():
                if not isinstance(node_output, dict):
                    continue
                final_answer = node_output.get("final_answer")
                if final_answer:
                    last_answer = final_answer
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
                    "final_answer": final_answer,
                }
                await asyncio.to_thread(_persist_event, thread_id, seq, event)
                _mark(f"persisted node={node_name} seq={seq}")
                seq += 1

        # THE ONLY completion event - one per run, always last.
        await asyncio.to_thread(
            _persist_event, thread_id, seq, {"node": "end", "status": "completed"}
        )
        _mark("completed")

        # v4b: memory extraction - detached so it adds ZERO user-perceived
        # latency (the run is complete the moment 'end' persists; this task
        # continues in the background). extract_and_sync_memories never
        # raises internally, and the reference set keeps it GC-safe.
        if last_answer:
            mem_task = asyncio.create_task(
                extract_and_sync_memories(task, last_answer, thread_id)
            )
            _memory_tasks.add(mem_task)
            mem_task.add_done_callback(_memory_tasks.discard)

    except Exception as e:
        # Even a crashed run must produce an end marker - otherwise the
        # frontend polls forever on a dead thread.
        _mark(f"failed: {type(e).__name__}")
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
        # this surgical: only THIS thread's sandbox dies. WHY pop FIRST:
        # pure in-memory work, so it always runs even if the kill below
        # fails; a stale registry entry would 409 this thread forever.
        # to_thread because the E2B kill is blocking network I/O.
        _running_tasks.pop(thread_id, None)
        try:
            await asyncio.to_thread(destroy_sandbox, thread_id)
        except Exception:
            pass  # E2B reaps orphaned sandboxes on its own timeout
        _mark("sandbox cleaned, run task exiting")


@router.post("/threads")
def create_thread():
    """Creates a new chat thread. Title defaults; first user message later
    becomes the sidebar title (4a.4 convention).

    WHY plain `def` (NOT async): this handler is 100% blocking psycopg. An
    `async def` handler runs ON the event loop - every blocking DB call
    inside it freezes ALL concurrent activity, including an in-flight agent
    run. FastAPI runs plain `def` endpoints in its threadpool - the loop
    never blocks.
    """
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
def list_threads():
    """Sidebar data: every thread, newest first. (Same WHY as create_thread.)"""
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
    """Starts a background agent run for this thread. Returns immediately.

    WHY async (unlike the endpoints above): this handler must create the
    asyncio.Task on the event loop. All its blocking DB work is offloaded
    via to_thread so the loop stays free.
    """
    # WHY 503 not silent fallback: a run that "works" but doesn't persist
    # is a mystery failure later. Loud and explicit beats quietly degraded.
    if graph_module.agent_graph_stateful is None:
        raise HTTPException(
            status_code=503,
            detail="Persistence layer unavailable - restart the server with a working DATABASE_URL.",
        )

    if thread_id in _running_tasks and not _running_tasks[thread_id].done():
        # WHY reject instead of queue: one active run per thread is a clear
        # contract. Queueing silently is how out-of-order answers happen.
        raise HTTPException(status_code=409, detail="A run is already active for this thread")

    if not await asyncio.to_thread(_thread_exists, thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")

    await asyncio.to_thread(_record_user_message, thread_id, request.task)

    task = asyncio.create_task(_run_agent_background(thread_id, request.task))
    _running_tasks[thread_id] = task
    return {"status": "started", "thread_id": thread_id}


@router.get("/threads/{thread_id}/events")
def get_events(thread_id: str, after: int = 0):
    """Replay + live polling endpoint: all events after seq N, plus run state.
    (Same WHY as create_thread - blocking psycopg belongs off the loop.)"""
    pool = get_pool()

    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT seq, event FROM thread_events "
            "WHERE thread_id = %s AND seq > %s ORDER BY seq",
            (thread_id, after),
        ).fetchall()

    # WHY safe from a threadpool thread: Task.done() only reads a flag; the
    # worst possible race is one extra poll reporting the previous state.
    is_running = (
        thread_id in _running_tasks and not _running_tasks[thread_id].done()
    )
    return {
        # WHY embed seq in each event: the frontend dedupes and orders by it
        "events": [{**row[1], "seq": row[0]} for row in rows],
        "last_seq": rows[-1][0] if rows else after,
        "is_running": is_running,
    }


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """Deletes a thread: cancels any live run, kills its sandbox, cascades
    events, and best-effort clears LangGraph checkpoints.

    WHY async: Task.cancel is not thread-safe - only the loop's own thread
    may call it. Everything blocking is offloaded via to_thread.
    """
    task = _running_tasks.pop(thread_id, None)
    if task is not None and not task.done():
        task.cancel()
    # WHY to_thread: destroy_sandbox kills via the E2B API - blocking
    # network I/O that must never sit on the event loop.
    await asyncio.to_thread(destroy_sandbox, thread_id)
    try:
        await asyncio.to_thread(_delete_thread_rows, thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"status": "deleted"}