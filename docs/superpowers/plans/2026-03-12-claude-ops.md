# Claude Ops Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a TUI dashboard for monitoring all active Claude Code sessions across terminals in real-time.

**Architecture:** Python + Textual app. Three modules: `parser.py` (data models + JSONL parsing + cost calculation), `watcher.py` (file watching + process detection), `app.py` (Textual widgets + layout). Entry point via `claude-ops` CLI command.

**Tech Stack:** Python 3.10+, Textual, watchfiles, pytest

**Spec:** `docs/superpowers/specs/2026-03-12-claude-ops-design.md`

---

## Chunk 1: Project Scaffolding and Data Models

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/claude_ops/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/fixtures/.gitkeep`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "claude-ops"
version = "0.1.0"
description = "TUI dashboard for monitoring Claude Code sessions"
requires-python = ">=3.10"
dependencies = [
    "textual>=3.0",
    "watchfiles>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[project.scripts]
claude-ops = "claude_ops.app:main"
```

- [ ] **Step 2: Create package files**

`src/claude_ops/__init__.py`:
```python
"""Claude Ops - TUI dashboard for monitoring Claude Code sessions."""
```

`tests/__init__.py`: empty file

- [ ] **Step 3: Install in dev mode and verify**

Run: `cd /home/allan/claude-ops && pip install -e ".[dev]"`
Expected: installs successfully, `claude-ops` command registered (will fail to run since app.py doesn't exist yet - that's fine)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "chore: project scaffolding"
```

### Task 2: Data models and cost calculation

**Files:**
- Create: `src/claude_ops/parser.py`
- Create: `tests/test_parser.py`

- [ ] **Step 1: Write failing tests for data models and cost calculation**

`tests/test_parser.py`:
```python
from datetime import datetime, timezone
from claude_ops.parser import (
    Session, Agent, ActivityEvent, EventType, SessionStatus, AgentStatus,
    calculate_cost, MODEL_PRICING,
)


def test_session_dataclass_fields():
    session = Session(
        id="abc-123",
        slug="drifting-pixel",
        project="my-app",
        cwd="/home/user/work/app",
        branch="master",
        version="2.1.68",
        start_time=datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc),
        last_activity=datetime(2026, 3, 12, 10, 30, 0, tzinfo=timezone.utc),
        status=SessionStatus.ACTIVE,
        message_counts={"user": 10, "assistant": 8},
        token_counts={"input": 1000, "output": 500, "cache_read": 200, "cache_write": 100},
        cost_usd=0.50,
        agents=[],
    )
    assert session.id == "abc-123"
    assert session.slug == "drifting-pixel"
    assert session.status == SessionStatus.ACTIVE


def test_agent_dataclass_fields():
    agent = Agent(
        id="ade48e0bd476ec31f",
        session_id="abc-123",
        model="claude-haiku-4-5-20251001",
        task_summary="Explore the LLM-as-judge evaluator",
        start_time=datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc),
        last_activity=datetime(2026, 3, 12, 10, 5, 0, tzinfo=timezone.utc),
        status=AgentStatus.ACTIVE,
        token_counts={"input": 500, "output": 200, "cache_read": 0, "cache_write": 0},
        cost_usd=0.0,
    )
    assert agent.id == "ade48e0bd476ec31f"
    assert agent.model == "claude-haiku-4-5-20251001"


def test_activity_event_dataclass():
    event = ActivityEvent(
        timestamp=datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc),
        session_slug="drifting-pixel",
        event_type=EventType.TOOL_USE,
        summary="Read(/home/user/work/app/src/main.py)",
    )
    assert event.event_type == EventType.TOOL_USE


def test_calculate_cost_opus():
    cost = calculate_cost(
        model="claude-opus-4-6",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == 15.0 + 75.0  # $15/M input + $75/M output


def test_calculate_cost_sonnet():
    cost = calculate_cost(
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == 3.0 + 15.0


def test_calculate_cost_haiku():
    cost = calculate_cost(
        model="claude-haiku-4-5-20251001",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == 0.80 + 4.0


def test_calculate_cost_with_cache():
    cost = calculate_cost(
        model="claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert cost == 0.30 + 3.75  # 10% of input + 125% of input


def test_calculate_cost_unknown_model_returns_zero():
    cost = calculate_cost(
        model="unknown-model",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/allan/claude-ops && python -m pytest tests/test_parser.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement data models and cost calculation**

`src/claude_ops/parser.py`:
```python
"""JSONL parsing, data models, and cost calculation for Claude Code sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SessionStatus(Enum):
    ACTIVE = "active"
    IDLE = "idle"
    DONE = "done"
    UNKNOWN = "unknown"


class AgentStatus(Enum):
    ACTIVE = "active"
    IDLE = "idle"


class EventType(Enum):
    TOOL_USE = "ToolUse"
    MESSAGE = "Message"
    AGENT_SPAWN = "AgentSpawn"


MODEL_PRICING: dict[str, dict[str, float]] = {
    "opus": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "haiku": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.00},
}


def _identify_model(model_string: str) -> str | None:
    model_lower = model_string.lower()
    for key in MODEL_PRICING:
        if key in model_lower:
            return key
    return None


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> float:
    model_key = _identify_model(model)
    if model_key is None:
        return 0.0
    pricing = MODEL_PRICING[model_key]
    return (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
        + cache_read_tokens * pricing["cache_read"] / 1_000_000
        + cache_write_tokens * pricing["cache_write"] / 1_000_000
    )


@dataclass
class Agent:
    id: str
    session_id: str
    model: str
    task_summary: str
    start_time: datetime
    last_activity: datetime
    status: AgentStatus
    token_counts: dict[str, int]
    cost_usd: float


@dataclass
class Session:
    id: str
    slug: str
    project: str
    cwd: str
    branch: str
    version: str
    start_time: datetime
    last_activity: datetime
    status: SessionStatus
    message_counts: dict[str, int]
    token_counts: dict[str, int]
    cost_usd: float
    agents: list[Agent] = field(default_factory=list)


@dataclass
class ActivityEvent:
    timestamp: datetime
    session_slug: str
    event_type: EventType
    summary: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/allan/claude-ops && python -m pytest tests/test_parser.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_ops/parser.py tests/test_parser.py
git commit -m "feat: data models and cost calculation"
```

### Task 3: JSONL parsing

**Files:**
- Create: `tests/fixtures/session.jsonl`
- Create: `tests/fixtures/subagents/agent-test123.jsonl`
- Modify: `tests/test_parser.py`
- Modify: `src/claude_ops/parser.py`

- [ ] **Step 1: Create test fixtures**

`tests/fixtures/session.jsonl` - one line per JSON object, representing a minimal session:
```jsonl
{"type":"user","sessionId":"test-session-1","slug":"test-slug","cwd":"/home/user/work","gitBranch":"master","version":"2.1.68","timestamp":"2026-03-12T10:00:00.000Z","message":{"role":"user","content":"Hello world"},"uuid":"msg-1","parentUuid":null}
{"type":"assistant","sessionId":"test-session-1","slug":"test-slug","cwd":"/home/user/work","gitBranch":"master","version":"2.1.68","timestamp":"2026-03-12T10:00:05.000Z","message":{"role":"assistant","model":"claude-sonnet-4-6","content":[{"type":"text","text":"Hi there!"}],"usage":{"input_tokens":100,"output_tokens":50,"cache_creation_input_tokens":20,"cache_read_input_tokens":10}},"uuid":"msg-2","parentUuid":"msg-1"}
{"type":"assistant","sessionId":"test-session-1","slug":"test-slug","cwd":"/home/user/work","gitBranch":"master","version":"2.1.68","timestamp":"2026-03-12T10:00:10.000Z","message":{"role":"assistant","model":"claude-sonnet-4-6","content":[{"type":"tool_use","name":"Read","id":"toolu_1","input":{"file_path":"/home/user/work/src/main.py"}}],"usage":{"input_tokens":200,"output_tokens":30,"cache_creation_input_tokens":0,"cache_read_input_tokens":50}},"uuid":"msg-3","parentUuid":"msg-2"}
{"type":"user","sessionId":"test-session-1","slug":"test-slug","cwd":"/home/user/work","gitBranch":"master","version":"2.1.68","timestamp":"2026-03-12T10:00:15.000Z","message":{"role":"user","content":"Fix the bug"},"uuid":"msg-4","parentUuid":"msg-3","isMeta":true}
{"type":"user","sessionId":"test-session-1","slug":"test-slug","cwd":"/home/user/work","gitBranch":"feat/fix","version":"2.1.68","timestamp":"2026-03-12T10:00:20.000Z","message":{"role":"user","content":"Now refactor this"},"uuid":"msg-5","parentUuid":"msg-4"}
```

`tests/fixtures/subagents/agent-test123.jsonl`:
```jsonl
{"type":"user","sessionId":"test-session-1","slug":"test-slug","cwd":"/home/user/work","gitBranch":"master","version":"2.1.68","timestamp":"2026-03-12T10:00:06.000Z","agentId":"test123","isSidechain":true,"message":{"role":"user","content":"Explore the evaluator code in detail and report back"},"uuid":"agent-msg-1","parentUuid":null}
{"type":"assistant","sessionId":"test-session-1","slug":"test-slug","cwd":"/home/user/work","gitBranch":"master","version":"2.1.68","timestamp":"2026-03-12T10:00:08.000Z","agentId":"test123","isSidechain":true,"message":{"role":"assistant","model":"claude-haiku-4-5-20251001","content":[{"type":"text","text":"I'll explore that now."}],"usage":{"input_tokens":300,"output_tokens":100,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}},"uuid":"agent-msg-2","parentUuid":"agent-msg-1"}
```

- [ ] **Step 2: Write failing tests for JSONL parsing**

Add to `tests/test_parser.py`:
```python
from pathlib import Path
from claude_ops.parser import parse_session_file, parse_agent_file, extract_events

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_session_file():
    session = parse_session_file(FIXTURES / "session.jsonl", project="test-project")
    assert session.id == "test-session-1"
    assert session.slug == "test-slug"
    assert session.project == "test-project"
    assert session.cwd == "/home/user/work"  # latest non-meta message cwd
    assert session.branch == "feat/fix"  # latest message branch
    assert session.version == "2.1.68"
    assert session.message_counts["user"] == 2  # isMeta messages excluded
    assert session.message_counts["assistant"] == 2
    assert session.token_counts["input"] == 300  # 100 + 200
    assert session.token_counts["output"] == 80  # 50 + 30
    assert session.token_counts["cache_read"] == 60  # 10 + 50
    assert session.token_counts["cache_write"] == 20  # 20 + 0
    assert session.cost_usd > 0


def test_parse_session_file_cost_uses_per_message_model():
    session = parse_session_file(FIXTURES / "session.jsonl", project="test-project")
    # Both assistant messages use sonnet: (300 input + 80 output) at sonnet rates
    expected = calculate_cost("claude-sonnet-4-6", 300, 80, 60, 20)
    assert abs(session.cost_usd - expected) < 0.0001


def test_parse_agent_file():
    agent = parse_agent_file(
        FIXTURES / "subagents" / "agent-test123.jsonl",
        session_id="test-session-1",
    )
    assert agent.id == "test123"
    assert agent.session_id == "test-session-1"
    assert agent.model == "claude-haiku-4-5-20251001"
    assert agent.task_summary == "Explore the evaluator code in detail and report back"
    assert agent.token_counts["input"] == 300
    assert agent.token_counts["output"] == 100


def test_extract_events_from_session():
    events = extract_events(FIXTURES / "session.jsonl", session_slug="test-slug")
    tool_events = [e for e in events if e.event_type == EventType.TOOL_USE]
    msg_events = [e for e in events if e.event_type == EventType.MESSAGE]
    assert len(tool_events) == 1
    assert "Read" in tool_events[0].summary
    assert "/home/user/work/src/main.py" in tool_events[0].summary
    # isMeta messages excluded, so only 2 user messages: "Hello world" and "Now refactor this"
    assert len(msg_events) == 2
    assert "Hello world" in msg_events[0].summary


def test_extract_events_agent_spawn():
    events = extract_events(
        FIXTURES / "subagents" / "agent-test123.jsonl",
        session_slug="test-slug",
        is_agent=True,
    )
    spawn_events = [e for e in events if e.event_type == EventType.AGENT_SPAWN]
    assert len(spawn_events) == 1
    assert "test123" in spawn_events[0].summary
    assert "haiku" in spawn_events[0].summary


def test_parse_session_file_nonexistent():
    session = parse_session_file(Path("/nonexistent.jsonl"), project="test")
    assert session is None


def test_parse_session_file_corrupted(tmp_path):
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text("not json\n{also bad\n")
    session = parse_session_file(bad_file, project="test")
    assert session is None


def test_parse_session_file_empty(tmp_path):
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("")
    session = parse_session_file(empty_file, project="test")
    assert session is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /home/allan/claude-ops && python -m pytest tests/test_parser.py -v -k "parse_session or parse_agent or extract_events"`
Expected: FAIL with ImportError (functions don't exist)

- [ ] **Step 4: Implement JSONL parsing**

Add to `src/claude_ops/parser.py`:
```python
import json
from pathlib import Path

def parse_session_file(path: Path, project: str) -> Session | None:
    """Parse a session JSONL file into a Session dataclass."""
    try:
        lines = path.read_text().strip().split("\n")
    except (OSError, FileNotFoundError):
        return None

    if not lines or not lines[0].strip():
        return None

    session_id = None
    slug = None
    cwd = None
    branch = None
    version = None
    start_time = None
    last_activity = None
    message_counts = {"user": 0, "assistant": 0}
    token_counts = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    cost_usd = 0.0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = record.get("type")
        timestamp_str = record.get("timestamp")
        if timestamp_str:
            try:
                ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except ValueError:
                ts = None
        else:
            ts = None

        if session_id is None:
            session_id = record.get("sessionId")
        if slug is None:
            slug = record.get("slug")

        # Track latest values from non-meta messages
        is_meta = record.get("isMeta", False)
        if not is_meta and record.get("cwd"):
            cwd = record["cwd"]
        if record.get("gitBranch"):
            branch = record["gitBranch"]
        if record.get("version"):
            version = record["version"]

        if ts:
            if start_time is None:
                start_time = ts
            last_activity = ts

        if msg_type == "user" and not is_meta:
            message_counts["user"] += 1
        elif msg_type == "assistant":
            message_counts["assistant"] += 1
            msg = record.get("message", {})
            usage = msg.get("usage", {})
            model = msg.get("model", "")
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_write = usage.get("cache_creation_input_tokens", 0)
            token_counts["input"] += in_tok
            token_counts["output"] += out_tok
            token_counts["cache_read"] += cache_read
            token_counts["cache_write"] += cache_write
            cost_usd += calculate_cost(model, in_tok, out_tok, cache_read, cache_write)

    if session_id is None:
        return None

    return Session(
        id=session_id,
        slug=slug or "",
        project=project,
        cwd=cwd or "",
        branch=branch or "",
        version=version or "",
        start_time=start_time or datetime.now(timezone.utc),
        last_activity=last_activity or datetime.now(timezone.utc),
        status=SessionStatus.UNKNOWN,
        message_counts=message_counts,
        token_counts=token_counts,
        cost_usd=cost_usd,
    )


def parse_agent_file(path: Path, session_id: str) -> Agent | None:
    """Parse a subagent JSONL file into an Agent dataclass."""
    try:
        lines = path.read_text().strip().split("\n")
    except (OSError, FileNotFoundError):
        return None

    if not lines or not lines[0].strip():
        return None

    agent_id = None
    model = None
    task_summary = None
    start_time = None
    last_activity = None
    token_counts = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    cost_usd = 0.0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        timestamp_str = record.get("timestamp")
        if timestamp_str:
            try:
                ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except ValueError:
                ts = None
        else:
            ts = None

        if agent_id is None:
            agent_id = record.get("agentId")

        if ts:
            if start_time is None:
                start_time = ts
            last_activity = ts

        msg_type = record.get("type")
        if msg_type == "user" and task_summary is None:
            content = record.get("message", {}).get("content", "")
            if isinstance(content, str):
                task_summary = content[:80]

        if msg_type == "assistant":
            msg = record.get("message", {})
            if model is None:
                model = msg.get("model", "")
            usage = msg.get("usage", {})
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_write = usage.get("cache_creation_input_tokens", 0)
            token_counts["input"] += in_tok
            token_counts["output"] += out_tok
            token_counts["cache_read"] += cache_read
            token_counts["cache_write"] += cache_write
            cost_usd += calculate_cost(
                model or "", in_tok, out_tok, cache_read, cache_write,
            )

    if agent_id is None:
        return None

    return Agent(
        id=agent_id,
        session_id=session_id,
        model=model or "",
        task_summary=task_summary or "",
        start_time=start_time or datetime.now(timezone.utc),
        last_activity=last_activity or datetime.now(timezone.utc),
        status=AgentStatus.ACTIVE,
        token_counts=token_counts,
        cost_usd=cost_usd,
    )


def extract_events(
    path: Path, session_slug: str, is_agent: bool = False,
) -> list[ActivityEvent]:
    """Extract activity events from a JSONL file."""
    events: list[ActivityEvent] = []
    try:
        lines = path.read_text().strip().split("\n")
    except (OSError, FileNotFoundError):
        return events

    first_agent_message = True

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        timestamp_str = record.get("timestamp")
        if not timestamp_str:
            continue
        try:
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        msg_type = record.get("type")

        # AgentSpawn: first message in an agent file
        if is_agent and first_agent_message and msg_type == "user":
            first_agent_message = False
            agent_id = record.get("agentId", "unknown")
            # Model comes from first assistant message - we'll find it
            model_hint = ""
            for other_line in lines:
                try:
                    other = json.loads(other_line.strip())
                    if other.get("type") == "assistant":
                        m = other.get("message", {}).get("model", "")
                        model_key = _identify_model(m)
                        model_hint = model_key or m
                        break
                except (json.JSONDecodeError, ValueError):
                    continue
            events.append(ActivityEvent(
                timestamp=ts,
                session_slug=session_slug,
                event_type=EventType.AGENT_SPAWN,
                summary=f"Spawned agent-{agent_id} ({model_hint})",
            ))
            continue

        if msg_type == "user" and not record.get("isMeta", False):
            content = record.get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                events.append(ActivityEvent(
                    timestamp=ts,
                    session_slug=session_slug,
                    event_type=EventType.MESSAGE,
                    summary=f"User: \"{content[:80]}\"",
                ))

        elif msg_type == "assistant":
            msg = record.get("message", {})
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name", "Unknown")
                        tool_input = block.get("input", {})
                        first_val = ""
                        if isinstance(tool_input, dict):
                            for v in tool_input.values():
                                first_val = str(v)[:80]
                                break
                        summary = f"{tool_name}({first_val})" if first_val else tool_name
                        events.append(ActivityEvent(
                            timestamp=ts,
                            session_slug=session_slug,
                            event_type=EventType.TOOL_USE,
                            summary=summary,
                        ))

    return events
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/allan/claude-ops && python -m pytest tests/test_parser.py -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/claude_ops/parser.py tests/test_parser.py tests/fixtures/
git commit -m "feat: JSONL parsing and event extraction"
```

### Task 4: Session discovery

**Files:**
- Modify: `src/claude_ops/parser.py`
- Modify: `tests/test_parser.py`

- [ ] **Step 1: Write failing test for session discovery**

Add to `tests/test_parser.py`:
```python
def test_discover_sessions(tmp_path):
    from claude_ops.parser import discover_sessions

    # Set up fake .claude/projects structure
    project_dir = tmp_path / "projects" / "-home-user-work-app"
    project_dir.mkdir(parents=True)

    # Create a session file with recent activity
    session_file = project_dir / "session-1.jsonl"
    now = datetime.now(timezone.utc).isoformat()
    session_file.write_text(
        f'{{"type":"user","sessionId":"session-1","slug":"test","cwd":"/work","gitBranch":"main","version":"2.1.68","timestamp":"{now}","message":{{"role":"user","content":"hi"}}}}\n'
    )

    # Create a subagent
    subagent_dir = project_dir / "session-1" / "subagents"
    subagent_dir.mkdir(parents=True)
    agent_file = subagent_dir / "agent-abc123.jsonl"
    agent_file.write_text(
        f'{{"type":"user","sessionId":"session-1","slug":"test","cwd":"/work","gitBranch":"main","version":"2.1.68","timestamp":"{now}","agentId":"abc123","isSidechain":true,"message":{{"role":"user","content":"do stuff"}}}}\n'
        f'{{"type":"assistant","sessionId":"session-1","slug":"test","cwd":"/work","gitBranch":"main","version":"2.1.68","timestamp":"{now}","agentId":"abc123","isSidechain":true,"message":{{"role":"assistant","model":"claude-haiku-4-5-20251001","content":[{{"type":"text","text":"ok"}}],"usage":{{"input_tokens":10,"output_tokens":5,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}}}}\n'
    )

    sessions = discover_sessions(tmp_path / "projects")
    assert len(sessions) == 1
    assert sessions[0].id == "session-1"
    assert len(sessions[0].agents) == 1
    assert sessions[0].agents[0].id == "abc123"


def test_discover_sessions_ignores_old(tmp_path):
    from claude_ops.parser import discover_sessions

    project_dir = tmp_path / "projects" / "-home-user-old"
    project_dir.mkdir(parents=True)

    session_file = project_dir / "old-session.jsonl"
    old_time = "2025-01-01T00:00:00.000Z"
    session_file.write_text(
        f'{{"type":"user","sessionId":"old","slug":"old","cwd":"/work","gitBranch":"main","version":"2.0.0","timestamp":"{old_time}","message":{{"role":"user","content":"hi"}}}}\n'
    )

    sessions = discover_sessions(tmp_path / "projects")
    assert len(sessions) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/allan/claude-ops && python -m pytest tests/test_parser.py::test_discover_sessions -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement session discovery**

Add to `src/claude_ops/parser.py`:
```python
from datetime import timedelta, timezone

def discover_sessions(
    projects_dir: Path,
    max_age_hours: int = 24,
) -> list[Session]:
    """Discover all recent sessions from the Claude projects directory."""
    sessions: list[Session] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    if not projects_dir.exists():
        return sessions

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        project_name = project_dir.name

        for jsonl_file in project_dir.glob("*.jsonl"):
            session = parse_session_file(jsonl_file, project=project_name)
            if session is None:
                continue
            if session.last_activity < cutoff:
                continue

            # Look for subagents
            session_subagent_dir = project_dir / session.id / "subagents"
            if session_subagent_dir.is_dir():
                for agent_file in session_subagent_dir.glob("agent-*.jsonl"):
                    agent = parse_agent_file(agent_file, session_id=session.id)
                    if agent is not None:
                        session.agents.append(agent)

            sessions.append(session)

    # Sort by most recent activity first
    sessions.sort(key=lambda s: s.last_activity, reverse=True)
    return sessions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/allan/claude-ops && python -m pytest tests/test_parser.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_ops/parser.py tests/test_parser.py
git commit -m "feat: session discovery with subagent loading"
```

## Chunk 2: File Watching and Process Detection

### Task 5: Process detection

**Files:**
- Create: `src/claude_ops/watcher.py`
- Create: `tests/test_watcher.py`

- [ ] **Step 1: Write failing tests for process detection**

`tests/test_watcher.py`:
```python
from claude_ops.watcher import find_claude_processes, match_session_status
from claude_ops.parser import SessionStatus
from datetime import datetime, timezone, timedelta
from unittest.mock import patch


def test_find_claude_processes_parses_ps_output():
    mock_output = (
        "user  1234  0.0  0.0  /usr/bin/node /home/user/.claude/bin/claude\n"
        "user  5678  0.0  0.0  /usr/bin/node /home/user/.claude/bin/claude\n"
    )
    with patch("claude_ops.watcher._run_ps", return_value=mock_output):
        with patch("claude_ops.watcher._get_process_cwd", side_effect=["/home/user/work/app", "/home/user/work/lib"]):
            procs = find_claude_processes()
            assert len(procs) == 2
            assert "/home/user/work/app" in procs
            assert "/home/user/work/lib" in procs


def test_find_claude_processes_handles_ps_failure():
    with patch("claude_ops.watcher._run_ps", return_value=None):
        procs = find_claude_processes()
        assert procs is None


def test_match_session_status_active():
    now = datetime.now(timezone.utc)
    status = match_session_status(
        session_cwd="/home/user/work/app",
        last_activity=now - timedelta(seconds=10),
        claude_cwds={"/home/user/work/app"},
    )
    assert status == SessionStatus.ACTIVE


def test_match_session_status_idle():
    now = datetime.now(timezone.utc)
    status = match_session_status(
        session_cwd="/home/user/work/app",
        last_activity=now - timedelta(seconds=60),
        claude_cwds={"/home/user/work/app"},
    )
    assert status == SessionStatus.IDLE


def test_match_session_status_done():
    now = datetime.now(timezone.utc)
    status = match_session_status(
        session_cwd="/home/user/work/app",
        last_activity=now - timedelta(seconds=10),
        claude_cwds={"/home/user/work/other"},
    )
    assert status == SessionStatus.DONE


def test_match_session_status_unknown_when_ps_failed():
    now = datetime.now(timezone.utc)
    status = match_session_status(
        session_cwd="/home/user/work/app",
        last_activity=now,
        claude_cwds=None,
    )
    assert status == SessionStatus.UNKNOWN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/allan/claude-ops && python -m pytest tests/test_watcher.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement process detection**

`src/claude_ops/watcher.py`:
```python
"""File watching and process detection for Claude Code sessions."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_ops.parser import SessionStatus

IDLE_THRESHOLD = timedelta(seconds=30)


def _run_ps() -> str | None:
    """Run ps aux and return output, or None on failure."""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _get_process_cwd(pid: int) -> str | None:
    """Get the working directory of a process via /proc."""
    try:
        cwd = Path(f"/proc/{pid}/cwd").resolve()
        return str(cwd)
    except (OSError, PermissionError):
        return None


def find_claude_processes() -> set[str] | None:
    """Find working directories of all running claude processes.

    Returns a set of cwd strings, or None if ps failed.
    """
    output = _run_ps()
    if output is None:
        return None

    cwds: set[str] = set()
    for line in output.strip().split("\n"):
        if "claude" not in line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        cwd = _get_process_cwd(pid)
        if cwd:
            cwds.add(cwd)
    return cwds


def match_session_status(
    session_cwd: str,
    last_activity: datetime,
    claude_cwds: set[str] | None,
) -> SessionStatus:
    """Determine session status from process info and activity time."""
    if claude_cwds is None:
        return SessionStatus.UNKNOWN

    if session_cwd not in claude_cwds:
        return SessionStatus.DONE

    now = datetime.now(timezone.utc)
    if now - last_activity > IDLE_THRESHOLD:
        return SessionStatus.IDLE

    return SessionStatus.ACTIVE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/allan/claude-ops && python -m pytest tests/test_watcher.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/claude_ops/watcher.py tests/test_watcher.py
git commit -m "feat: process detection and session status matching"
```

## Chunk 3: Textual TUI Application

### Task 6: Textual app with header and session list

**Files:**
- Create: `src/claude_ops/app.py`

This task builds the full Textual app. Given the tight integration between widgets, layout, and data flow, this is implemented as a single unit rather than widget-by-widget.

- [ ] **Step 1: Implement the Textual app**

`src/claude_ops/app.py`:
```python
"""Claude Ops - TUI dashboard for monitoring Claude Code sessions."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Header, Static, ListView, ListItem, Label, Rule

from claude_ops.parser import (
    Session, Agent, ActivityEvent, EventType, SessionStatus, AgentStatus,
    discover_sessions, parse_session_file, parse_agent_file, extract_events,
)
from claude_ops.watcher import find_claude_processes, match_session_status

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
PROCESS_CHECK_INTERVAL = 5.0
MAX_ACTIVITY_EVENTS = 200


def format_duration(start: datetime, end: datetime | None = None) -> str:
    """Format a duration as human-readable string."""
    end = end or datetime.now(timezone.utc)
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    if minutes < 60:
        seconds = total_seconds % 60
        return f"{minutes}m {seconds}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def format_tokens(count: int) -> str:
    """Format token count with k/M suffix."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def format_cost(cost: float) -> str:
    """Format cost as dollar string."""
    return f"${cost:.2f}"


def status_dot(status: SessionStatus | AgentStatus) -> str:
    """Return a colored dot character for status."""
    if status in (SessionStatus.ACTIVE, AgentStatus.ACTIVE):
        return "[green]●[/]"
    elif status == SessionStatus.IDLE or status == AgentStatus.IDLE:
        return "[yellow]●[/]"
    elif status == SessionStatus.DONE:
        return "[dim]●[/]"
    return "[dim]?[/]"


class SessionListItem(ListItem):
    """A session entry in the session list."""

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        s = self.session
        dot = status_dot(s.status)
        idle_text = ""
        if s.status == SessionStatus.IDLE:
            idle_text = f" [yellow](idle {format_duration(s.last_activity)})[/]"

        project_display = s.project.replace("-home-allan-", "").replace("-", "/")
        yield Static(
            f"{dot} [bold]{project_display}[/]{idle_text}\n"
            f"  [dim]{s.cwd}[/]\n"
            f"  [dim]{s.branch} · {format_duration(s.start_time)} · {format_cost(s.cost_usd)}[/]",
            markup=True,
        )
        for agent in s.agents:
            agent_dot = status_dot(agent.status)
            agent_status = ""
            if agent.status == AgentStatus.IDLE:
                agent_status = f" idle {format_duration(agent.last_activity)}"
            else:
                agent_status = f" {format_duration(agent.start_time)}"
            yield Static(
                f"  ├─ {agent_dot} [dim]agent-{agent.id[:4]}[/]{agent_status}",
                markup=True,
            )


class HeaderBar(Static):
    """Top bar showing aggregate stats."""

    def render_stats(self, sessions: list[Session]) -> str:
        active = sum(1 for s in sessions if s.status == SessionStatus.ACTIVE)
        idle = sum(1 for s in sessions if s.status == SessionStatus.IDLE)
        total_agents = sum(len(s.agents) for s in sessions)
        total_cost = sum(s.cost_usd + sum(a.cost_usd for a in s.agents) for s in sessions)

        longest = ""
        if sessions:
            earliest = min(s.start_time for s in sessions)
            longest = format_duration(earliest)

        return (
            f" [green]●[/] {active} active  "
            f"[yellow]●[/] {idle} idle  "
            f"[bold]▲[/] {total_agents} agents  "
            f"[bold]⏱[/] {longest}  "
            f"[bold]$[/] {format_cost(total_cost)}"
        )


class SessionDetail(Static):
    """Right panel showing selected session details."""

    def render_session(self, session: Session | None) -> str:
        if session is None:
            return "[dim]No session selected[/]"

        s = session
        dot = status_dot(s.status)
        project_display = s.project.replace("-home-allan-", "").replace("-", "/")

        lines = [
            f"[bold]{project_display}[/]",
            "",
            f"  Status:   {dot} {s.status.value.title()}",
            f"  Branch:   {s.branch}",
            f"  CWD:      {s.cwd}",
            f"  Uptime:   {format_duration(s.start_time)}",
            f"  Messages: {s.message_counts.get('user', 0)} user · {s.message_counts.get('assistant', 0)} assistant",
            f"  Tokens:   {format_tokens(s.token_counts.get('input', 0))} in · {format_tokens(s.token_counts.get('output', 0))} out",
            f"  Cost:     {format_cost(s.cost_usd)}",
            f"  Version:  {s.version}",
        ]

        if s.agents:
            lines.append("")
            lines.append(f"  [bold]AGENTS ({len(s.agents)})[/]")
            lines.append(f"  {'─' * 35}")
            for agent in s.agents:
                agent_dot = status_dot(agent.status)
                model_short = agent.model.split("-")[1] if "-" in agent.model else agent.model
                if agent.status == AgentStatus.IDLE:
                    time_str = f"idle {format_duration(agent.last_activity)}"
                else:
                    time_str = format_duration(agent.start_time)
                lines.append(f"  {agent_dot} agent-{agent.id[:4]} · {model_short} · {time_str}")
                lines.append(f"    [dim]\"{agent.task_summary[:60]}\"[/]")

        return "\n".join(lines)


class ActivityFeed(Static):
    """Bottom panel showing unified activity feed."""

    SESSION_COLORS = ["cyan", "magenta", "green", "yellow", "blue", "red"]

    def _session_color(self, slug: str) -> str:
        idx = hash(slug) % len(self.SESSION_COLORS)
        return self.SESSION_COLORS[idx]

    def render_events(self, events: list[ActivityEvent]) -> str:
        if not events:
            return "[dim]No activity yet[/]"

        lines = []
        for event in events[-20:]:  # Show last 20 in the visible area
            ts = event.timestamp.strftime("%H:%M:%S")
            color = self._session_color(event.session_slug)
            slug = event.session_slug[:15].ljust(15)
            etype = event.event_type.value.ljust(10)
            lines.append(f"  [dim]{ts}[/]  [{color}]{slug}[/]  [bold]{etype}[/]  {event.summary[:60]}")

        return "\n".join(lines)


class ClaudeOpsApp(App):
    """Main Claude Ops TUI application."""

    TITLE = "Claude Ops"
    CSS = """
    #header-bar {
        dock: top;
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    #main {
        height: 1fr;
    }
    #session-list {
        width: 1fr;
        border-right: solid $primary;
        padding: 0 1;
    }
    #session-detail {
        width: 1fr;
        padding: 0 1;
    }
    #activity-feed {
        dock: bottom;
        height: 12;
        border-top: solid $primary;
        padding: 0 1;
    }
    #activity-title {
        text-style: bold;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    sessions: reactive[list[Session]] = reactive(list, recompose=False)
    selected_index: reactive[int] = reactive(0)
    activity_events: deque[ActivityEvent]

    def __init__(self) -> None:
        super().__init__()
        self.activity_events = deque(maxlen=MAX_ACTIVITY_EVENTS)

    def compose(self) -> ComposeResult:
        yield Static(id="header-bar")
        with Horizontal(id="main"):
            with VerticalScroll(id="session-list"):
                yield Static("[dim]Loading sessions...[/]", id="session-list-content", markup=True)
            with VerticalScroll(id="session-detail"):
                yield SessionDetail(id="detail-widget")
        with Vertical(id="activity-feed"):
            yield Static("[bold]ACTIVITY FEED[/]", id="activity-title", markup=True)
            yield ActivityFeed(id="feed-widget")

    def on_mount(self) -> None:
        self.load_sessions()
        self.set_interval(PROCESS_CHECK_INTERVAL, self.refresh_statuses)
        self.watch_files()

    @work(thread=True)
    def watch_files(self) -> None:
        """Watch for JSONL file changes using watchfiles."""
        try:
            from watchfiles import watch
            for _changes in watch(str(CLAUDE_PROJECTS_DIR), recursive=True):
                self.call_from_thread(self.load_sessions)
        except ImportError:
            # Fallback: poll every 3 seconds if watchfiles not available
            pass
        except Exception:
            pass

    def load_sessions(self) -> None:
        """Load all sessions and update the UI."""
        sessions = discover_sessions(CLAUDE_PROJECTS_DIR)
        claude_cwds = find_claude_processes()

        for session in sessions:
            session.status = match_session_status(
                session.cwd, session.last_activity, claude_cwds,
            )
            for agent in session.agents:
                from datetime import timedelta
                now = datetime.now(timezone.utc)
                if now - agent.last_activity > timedelta(seconds=30):
                    agent.status = AgentStatus.IDLE
                else:
                    agent.status = AgentStatus.ACTIVE

        # Collect new activity events
        for session in sessions:
            session_file = self._find_session_file(session)
            if session_file:
                events = extract_events(session_file, session.slug)
                for event in events:
                    if event not in self.activity_events:
                        self.activity_events.append(event)
                # Agent events
                agent_dir = session_file.parent / session.id / "subagents"
                if agent_dir.is_dir():
                    for agent_file in agent_dir.glob("agent-*.jsonl"):
                        agent_events = extract_events(agent_file, session.slug, is_agent=True)
                        for event in agent_events:
                            if event not in self.activity_events:
                                self.activity_events.append(event)

        sorted_events = sorted(self.activity_events, key=lambda e: e.timestamp)
        self.activity_events = deque(sorted_events, maxlen=MAX_ACTIVITY_EVENTS)

        self.sessions = sessions
        self.update_ui()

    def _find_session_file(self, session: Session) -> Path | None:
        """Find the JSONL file for a session."""
        for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session.id}.jsonl"
            if candidate.exists():
                return candidate
        return None

    def refresh_statuses(self) -> None:
        """Refresh process-based status detection."""
        claude_cwds = find_claude_processes()
        for session in self.sessions:
            session.status = match_session_status(
                session.cwd, session.last_activity, claude_cwds,
            )
            for agent in session.agents:
                from datetime import timedelta
                now = datetime.now(timezone.utc)
                if now - agent.last_activity > timedelta(seconds=30):
                    agent.status = AgentStatus.IDLE
                else:
                    agent.status = AgentStatus.ACTIVE
        self.update_ui()

    def update_ui(self) -> None:
        """Update all UI widgets with current data."""
        # Header
        header = self.query_one("#header-bar", Static)
        header_bar = HeaderBar()
        header.update(header_bar.render_stats(self.sessions))

        # Session list
        session_list = self.query_one("#session-list-content", Static)
        if not self.sessions:
            session_list.update("[dim]No active sessions found[/]")
        else:
            lines = []
            for i, session in enumerate(self.sessions):
                dot = status_dot(session.status)
                idle_text = ""
                if session.status == SessionStatus.IDLE:
                    idle_text = f" [yellow](idle {format_duration(session.last_activity)})[/]"

                project_display = session.project.replace("-home-allan-", "").replace("-", "/")
                selected = ">> " if i == self.selected_index else "   "
                lines.append(
                    f"{selected}{dot} [bold]{project_display}[/]{idle_text}\n"
                    f"     [dim]{session.cwd}[/]\n"
                    f"     [dim]{session.branch} · {format_duration(session.start_time)} · {format_cost(session.cost_usd)}[/]"
                )
                for agent in session.agents:
                    agent_dot = status_dot(agent.status)
                    if agent.status == AgentStatus.IDLE:
                        agent_time = f"idle {format_duration(agent.last_activity)}"
                    else:
                        agent_time = format_duration(agent.start_time)
                    lines.append(f"     ├─ {agent_dot} [dim]agent-{agent.id[:4]} {agent_time}[/]")
                lines.append("")
            session_list.update("\n".join(lines))

        # Detail
        detail = self.query_one("#detail-widget", SessionDetail)
        selected = self.sessions[self.selected_index] if self.sessions and self.selected_index < len(self.sessions) else None
        detail.update(detail.render_session(selected))

        # Activity feed
        feed = self.query_one("#feed-widget", ActivityFeed)
        feed.update(feed.render_events(list(self.activity_events)))

        # Auto-scroll activity feed to bottom
        feed_scroll = self.query_one("#activity-feed", Vertical)
        feed_scroll.scroll_end(animate=False)

    def action_refresh(self) -> None:
        self.load_sessions()

    def on_key(self, event) -> None:
        if event.key == "up" and self.selected_index > 0:
            self.selected_index -= 1
            self.update_ui()
        elif event.key == "down" and self.selected_index < len(self.sessions) - 1:
            self.selected_index += 1
            self.update_ui()


def main() -> None:
    app = ClaudeOpsApp()
    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manual smoke test**

Run: `cd /home/allan/claude-ops && pip install -e ".[dev]" && claude-ops`
Expected: TUI launches, shows current active sessions from `~/.claude/projects/`. Press `q` to quit.

- [ ] **Step 3: Commit**

```bash
git add src/claude_ops/app.py
git commit -m "feat: Textual TUI app with all panels"
```

### Task 7: Polish and final integration

**Files:**
- Modify: `src/claude_ops/app.py` (CSS refinements based on smoke test)
- Create: `README.md`

- [ ] **Step 1: Add README**

`README.md`:
```markdown
# Claude Ops

TUI dashboard for monitoring Claude Code sessions running across terminals.

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
claude-ops
```

Automatically discovers sessions from `~/.claude/projects/`.

### Controls

- **Up/Down** - navigate sessions
- **r** - refresh
- **q** - quit
```

- [ ] **Step 2: Run full test suite**

Run: `cd /home/allan/claude-ops && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Smoke test the app**

Run: `cd /home/allan/claude-ops && claude-ops`
Expected: dashboard shows real sessions, auto-refreshes

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add README"
```

- [ ] **Step 5: Push to origin**

```bash
git push -u origin master
```
