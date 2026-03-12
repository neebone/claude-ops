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
