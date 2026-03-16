"""JSONL parsing, data models, and cost calculation for Claude Code sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path


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


@dataclass
class AgentNode:
    """A node in the agent hierarchy tree."""
    agent: Agent
    children: list[AgentNode] = field(default_factory=list)


def build_agent_trees(sessions: list[Session]) -> dict[str, list[AgentNode]]:
    """Build agent hierarchy trees for each session.

    Currently builds flat trees (session -> agents) since Claude Code
    stores subagents in a flat directory. If nested subagent directories
    are found in the future, this can be extended to walk deeper.

    Returns a dict mapping session_id -> list of root AgentNodes.
    """
    trees: dict[str, list[AgentNode]] = {}
    for session in sessions:
        nodes = [AgentNode(agent=agent) for agent in session.agents]
        trees[session.id] = nodes
    return trees


def _parse_timestamp(timestamp_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except ValueError:
        return None


_session_cache: dict[str, tuple[float, int, Session]] = {}


def parse_session_file(path: Path, project: str) -> Session | None:
    """Parse a session JSONL file into a Session dataclass.

    Uses a cache keyed on (mtime, size) to avoid re-parsing unchanged files.
    """
    try:
        stat = path.stat()
    except (OSError, FileNotFoundError):
        return None

    cache_key = str(path)
    cached = _session_cache.get(cache_key)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]

    result = _parse_session_file_impl(path, project)
    if result is not None:
        _session_cache[cache_key] = (stat.st_mtime, stat.st_size, result)
    return result


def _parse_session_file_impl(path: Path, project: str) -> Session | None:
    """Parse a session JSONL file into a Session dataclass (uncached)."""
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

        ts = _parse_timestamp(record.get("timestamp", ""))

        if session_id is None:
            session_id = record.get("sessionId")
        if slug is None:
            slug = record.get("slug")

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

        msg_type = record.get("type")
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


_agent_cache: dict[str, tuple[float, int, Agent]] = {}


def parse_agent_file(path: Path, session_id: str) -> Agent | None:
    """Parse a subagent JSONL file into an Agent dataclass (cached)."""
    try:
        stat = path.stat()
    except (OSError, FileNotFoundError):
        return None

    cache_key = str(path)
    cached = _agent_cache.get(cache_key)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]

    result = _parse_agent_file_impl(path, session_id)
    if result is not None:
        _agent_cache[cache_key] = (stat.st_mtime, stat.st_size, result)
    return result


def _parse_agent_file_impl(path: Path, session_id: str) -> Agent | None:
    """Parse a subagent JSONL file into an Agent dataclass (uncached)."""
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

        ts = _parse_timestamp(record.get("timestamp", ""))

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


_events_cache: dict[str, tuple[float, int, list[ActivityEvent]]] = {}


def extract_events(
    path: Path, session_slug: str, is_agent: bool = False,
) -> list[ActivityEvent]:
    """Extract activity events from a JSONL file (cached)."""
    try:
        stat = path.stat()
    except (OSError, FileNotFoundError):
        return []

    cache_key = f"{path}:{session_slug}:{is_agent}"
    cached = _events_cache.get(cache_key)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]

    result = _extract_events_impl(path, session_slug, is_agent)
    _events_cache[cache_key] = (stat.st_mtime, stat.st_size, result)
    return result


def _extract_events_impl(
    path: Path, session_slug: str, is_agent: bool = False,
) -> list[ActivityEvent]:
    """Extract activity events from a JSONL file (uncached)."""
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

        ts = _parse_timestamp(record.get("timestamp", ""))
        if not ts:
            continue

        msg_type = record.get("type")

        # AgentSpawn: first message in an agent file
        if is_agent and first_agent_message and msg_type == "user":
            first_agent_message = False
            agent_id = record.get("agentId", "unknown")
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

            # Reset agents list to avoid accumulating duplicates on cached sessions
            session.agents = []

            # Look for subagents
            session_subagent_dir = project_dir / session.id / "subagents"
            if session_subagent_dir.is_dir():
                for agent_file in session_subagent_dir.glob("agent-*.jsonl"):
                    agent = parse_agent_file(agent_file, session_id=session.id)
                    if agent is not None:
                        session.agents.append(agent)

            sessions.append(session)

    sessions.sort(key=lambda s: s.last_activity, reverse=True)
    return sessions
