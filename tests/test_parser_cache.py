"""Tests for parser file-level caching."""

import json
from datetime import datetime, timezone
from pathlib import Path

from claude_ops.parser import (
    _agent_cache,
    _events_cache,
    _session_cache,
    extract_events,
    parse_agent_file,
    parse_session_file,
)


def _make_session_jsonl(path: Path, session_id: str = "sess-1") -> None:
    """Write a minimal valid session JSONL file."""
    now = datetime.now(timezone.utc).isoformat()
    line = json.dumps({
        "type": "user",
        "sessionId": session_id,
        "slug": "test-slug",
        "cwd": "/work",
        "gitBranch": "main",
        "version": "2.1.74",
        "timestamp": now,
        "message": {"role": "user", "content": "Hello world"},
    })
    path.write_text(line + "\n")


def _make_agent_jsonl(path: Path, agent_id: str = "agent-1") -> None:
    """Write a minimal valid agent JSONL file."""
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        json.dumps({
            "type": "user",
            "sessionId": "sess-1",
            "agentId": agent_id,
            "isSidechain": True,
            "timestamp": now,
            "message": {"role": "user", "content": "do stuff"},
        }),
        json.dumps({
            "type": "assistant",
            "sessionId": "sess-1",
            "agentId": agent_id,
            "isSidechain": True,
            "timestamp": now,
            "message": {
                "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        }),
    ]
    path.write_text("\n".join(lines) + "\n")


def test_session_cache_returns_same_object_on_unchanged_file(tmp_path):
    """Cached result should be returned when file hasn't changed."""
    f = tmp_path / "cached.jsonl"
    _make_session_jsonl(f)
    cache_key = str(f)
    _session_cache.pop(cache_key, None)

    result1 = parse_session_file(f, project="test")
    result2 = parse_session_file(f, project="test")

    assert result1 is result2
    assert result1.id == "sess-1"


def test_session_cache_invalidates_on_file_change(tmp_path):
    """Cache should be invalidated when the file is modified."""
    f = tmp_path / "changing.jsonl"
    _make_session_jsonl(f, session_id="first")
    cache_key = str(f)
    _session_cache.pop(cache_key, None)

    result1 = parse_session_file(f, project="test")
    assert result1.id == "first"

    _make_session_jsonl(f, session_id="second")
    result2 = parse_session_file(f, project="test")
    assert result2.id == "second"
    assert result1 is not result2


def test_agent_cache_returns_same_object_on_unchanged_file(tmp_path):
    """Agent cache should return same object for unchanged file."""
    f = tmp_path / "agent.jsonl"
    _make_agent_jsonl(f)
    cache_key = str(f)
    _agent_cache.pop(cache_key, None)

    result1 = parse_agent_file(f, session_id="sess-1")
    result2 = parse_agent_file(f, session_id="sess-1")

    assert result1 is result2
    assert result1.id == "agent-1"


def test_events_cache_returns_same_list_on_unchanged_file(tmp_path):
    """Events cache should return same list for unchanged file."""
    f = tmp_path / "events.jsonl"
    _make_session_jsonl(f)
    cache_key = f"{f}:test-slug:False"
    _events_cache.pop(cache_key, None)

    result1 = extract_events(f, session_slug="test-slug")
    result2 = extract_events(f, session_slug="test-slug")

    assert result1 is result2


def test_session_cache_nonexistent_file_returns_none(tmp_path):
    """Nonexistent files should return None and not be cached."""
    f = tmp_path / "nope.jsonl"
    result = parse_session_file(f, project="test")
    assert result is None
    assert str(f) not in _session_cache
