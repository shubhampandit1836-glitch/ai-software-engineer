"""Unit tests for memory parsing/rendering (pure functions only - no DB,
matching the rest of the suite: CI runs with a dummy DATABASE_URL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.memory import MAX_MEMORIES, build_memory_block, parse_memory_json


def test_parse_clean_json():
    assert parse_memory_json('["User\'s name is Shubh"]', []) == [
        "User's name is Shubh"
    ]


def test_parse_fenced_json():
    # WHY: free-tier models love wrapping JSON in markdown fences
    raw = "```json\n[\"a\", \"b\"]\n```"
    assert parse_memory_json(raw, []) == ["a", "b"]


def test_parse_json_with_chatter():
    raw = 'Here is the updated list: ["name is Shubh"] hope that helps'
    assert parse_memory_json(raw, []) == ["name is Shubh"]


def test_parse_garbage_returns_none():
    assert parse_memory_json("I cannot do that", ["existing"]) is None


def test_parse_non_list_returns_none():
    assert parse_memory_json('{"a": 1}', []) is None


def test_wipe_guard_blocks_empty_when_memories_exist():
    # WHY: a lazy model returning [] must never erase stored memories
    assert parse_memory_json("[]", ["name is Shubh"]) is None


def test_empty_ok_when_nothing_stored():
    assert parse_memory_json("[]", []) == []


def test_over_cap_returns_none():
    raw = json.dumps([f"memory item {i}" for i in range(MAX_MEMORIES + 5)])
    assert parse_memory_json(raw, []) is None


def test_build_memory_block_empty():
    assert build_memory_block([]) == ""


def test_build_memory_block_items():
    block = build_memory_block(["name is Shubh", "prefers FastAPI"])
    assert "WHAT YOU REMEMBER ABOUT THIS USER:" in block
    assert "- name is Shubh" in block
    assert "- prefers FastAPI" in block