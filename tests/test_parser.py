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
