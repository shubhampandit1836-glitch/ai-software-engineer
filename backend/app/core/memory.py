"""Long-term user memory (v4b): facts remembered ACROSS threads.

Design (the WHY of the whole module):
- EXTRACT AFTER the run finishes, in a detached task: memory must add ZERO
  latency to answers - the hard lesson of v4a.4 (per-token events cost
  15-20s of pure latency).
- The extractor is the ONLY writer: it sees the current memory list plus
  the new exchange, then returns the UPDATED list. One LLM call handles
  add/update/dedupe/no-op naturally - no separate diffing logic to get
  wrong.
- SYNC = replace-all inside one transaction: memories are a small (<=20)
  curated set, not an append log. Simplest correct model.
- WIPE GUARD: an empty result while memories exist is treated as a parse
  failure, not a wipe command - a lazy model must never erase the user.
- All psycopg calls are SYNC and run via asyncio.to_thread - the
  event-loop-blocking lesson from v4a.4.
"""
import asyncio
import json
import logging
import re
from typing import List, Optional

from langchain_core.messages import SystemMessage, HumanMessage

from app.core.db import get_pool
from app.core.llm import invoke_with_retry

logger = logging.getLogger(__name__)

# WHY a constant user key: no auth layer yet. When auth lands (post-ship),
# this is the single switch point - every row already carries user_id.
DEFAULT_USER_ID = "default"

MAX_MEMORIES = 20

_MEMORY_BLOCK_TITLE = "WHAT YOU REMEMBER ABOUT THIS USER:"

# WHY fence stripping + array scan: free-tier models wrap JSON in markdown
# fences or add chatter around it; both must be rescued before failing.
_FENCE_RE = re.compile(r"```[a-zA-Z]*")
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def get_memories(user_id: str = DEFAULT_USER_ID) -> List[str]:
    """All memories for a user, oldest first. SYNC - call via to_thread."""
    pool = get_pool()
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT content FROM memories WHERE user_id = %s ORDER BY id",
            (user_id,),
        ).fetchall()
    return [row[0] for row in rows]


def _replace_memories(
    user_id: str, memories: List[str], source_thread_id: Optional[str]
) -> None:
    """Atomically swaps the user's memory set. SYNC - call via to_thread."""
    pool = get_pool()
    with pool.connection() as conn:
        conn.execute("DELETE FROM memories WHERE user_id = %s", (user_id,))
        # WHY an explicit cursor: psycopg3's Connection has the .execute()
        # shortcut but NO .executemany() shortcut - that method lives on
        # Cursor. conn.executemany(...) raised AttributeError (caught live
        # by the never-raise guard as 'extraction skipped' - the guard
        # design paying for itself on its first real failure).
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO memories (user_id, content, source_thread_id) "
                "VALUES (%s, %s, %s)",
                [(user_id, m, source_thread_id) for m in memories],
            )
        conn.commit()


def parse_memory_json(raw: str, current: List[str]) -> Optional[List[str]]:
    """Model output -> memory list, or None when unusable (keep current).

    None is the 'do nothing' signal: unusable output, wrong shape, runaway
    length, or a suspicious wipe (empty result while memories exist).
    """
    text = _FENCE_RE.sub("", raw).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_ARRAY_RE.search(text)
        if match is None:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, list):
        return None
    cleaned = [str(item).strip() for item in parsed if str(item).strip()]
    # WHY the cap check: a runaway list means the model misunderstood the
    # task - keeping the current set beats persisting garbage.
    if len(cleaned) > MAX_MEMORIES:
        return None
    # WIPE GUARD: users don't erase their memory by chatting - an empty
    # result with a non-empty current set is model laziness, not intent.
    if not cleaned and current:
        return None
    return cleaned


def build_memory_block(memories: List[str]) -> str:
    """Renders the injection section for system prompts ('' when empty)."""
    if not memories:
        return ""
    lines = "\n".join(f"- {m}" for m in memories)
    return f"{_MEMORY_BLOCK_TITLE}\n{lines}"


async def extract_and_sync_memories(
    user_message: str, assistant_answer: str, source_thread_id: str
) -> None:
    """Post-run memory update. NEVER raises - memory is best-effort.

    WHY detached + never raises: this runs AFTER the user already has
    their answer; a failure here must surface nowhere except the log.
    """
    try:
        current = await asyncio.to_thread(get_memories)
        current_json = json.dumps(current, ensure_ascii=False)

        system_prompt = f"""You maintain the long-term memory of a user chatting with an AI software engineer.

CURRENT MEMORY (JSON array of strings):
{current_json}

NEW EXCHANGE:
User said: {user_message[:500]}
Assistant answered: {assistant_answer[:500]}

Update the memory list:
- ADD new durable facts about the USER (name, preferences, constraints, ongoing projects).
- UPDATE memories this exchange contradicts (e.g. a corrected name).
- REMOVE memories that are clearly obsolete.
- IGNORE anything not worth remembering long-term (small talk, one-off tasks).
- If nothing should change, return the CURRENT list exactly as given.

RULES:
- Maximum {MAX_MEMORIES} items, each one short sentence, in English.
- Preserve still-valid items EXACTLY as written - do not rephrase.
- Return ONLY a JSON array of strings. No markdown fences, no commentary."""

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="Return the updated memory JSON array."),
        ]
        response = await invoke_with_retry(messages, tier="fast")
        raw = response.content
        if not isinstance(raw, str):
            raw = "\n".join(str(item) for item in raw)

        updated = parse_memory_json(raw, current)
        if updated is None:
            logger.warning("memory: unusable extractor output, keeping current set")
            return
        if updated == current:
            return  # nothing changed - skip the write entirely

        await asyncio.to_thread(
            _replace_memories, DEFAULT_USER_ID, updated, source_thread_id
        )
        logger.info(
            "memory: %d -> %d items (thread=%s)",
            len(current),
            len(updated),
            source_thread_id[:8],
        )
    except Exception as exc:
        logger.warning("memory: extraction skipped (%s)", exc)