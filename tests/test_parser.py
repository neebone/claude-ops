from datetime import datetime, timezone
from pathlib import Path
from claude_ops.parser import (
    Session, Agent, ActivityEvent, EventType, SessionStatus, AgentStatus,
    calculate_cost, MODEL_PRICING, parse_session_file, parse_agent_file,
    extract_events, AgentNode, build_agent_trees,
)

FIXTURES = Path(__file__).parent / "fixtures"


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


def test_parse_session_file():
    session = parse_session_file(FIXTURES / "session.jsonl", project="test-project")
    assert session.id == "test-session-1"
    assert session.slug == "test-slug"
    assert session.project == "test-project"
    assert session.cwd == "/home/user/work"
    assert session.branch == "feat/fix"
    assert session.version == "2.1.68"
    assert session.message_counts["user"] == 2  # isMeta excluded
    assert session.message_counts["assistant"] == 2
    assert session.token_counts["input"] == 300  # 100 + 200
    assert session.token_counts["output"] == 80  # 50 + 30
    assert session.token_counts["cache_read"] == 60  # 10 + 50
    assert session.token_counts["cache_write"] == 20  # 20 + 0
    assert session.cost_usd > 0


def test_parse_session_file_cost_uses_per_message_model():
    session = parse_session_file(FIXTURES / "session.jsonl", project="test-project")
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


def test_discover_sessions(tmp_path):
    from claude_ops.parser import discover_sessions

    project_dir = tmp_path / "projects" / "-home-user-work-app"
    project_dir.mkdir(parents=True)

    session_file = project_dir / "session-1.jsonl"
    now = datetime.now(timezone.utc).isoformat()
    session_file.write_text(
        f'{{"type":"user","sessionId":"session-1","slug":"test","cwd":"/work","gitBranch":"main","version":"2.1.68","timestamp":"{now}","message":{{"role":"user","content":"hi"}}}}\n'
    )

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


def test_build_agent_trees_flat():
    """Flat subagents should produce a one-level tree."""
    session = Session(
        id="sess-1", slug="test", project="proj", cwd="/tmp",
        branch="main", version="2.1",
        start_time=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        status=SessionStatus.UNKNOWN,
        message_counts={}, token_counts={}, cost_usd=0,
        agents=[
            Agent(
                id="a1", session_id="sess-1", model="opus", task_summary="task1",
                start_time=datetime.now(timezone.utc),
                last_activity=datetime.now(timezone.utc),
                status=AgentStatus.ACTIVE,
                token_counts={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                cost_usd=0,
            ),
            Agent(
                id="a2", session_id="sess-1", model="sonnet", task_summary="task2",
                start_time=datetime.now(timezone.utc),
                last_activity=datetime.now(timezone.utc),
                status=AgentStatus.ACTIVE,
                token_counts={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                cost_usd=0,
            ),
        ],
    )
    trees = build_agent_trees([session])
    assert "sess-1" in trees
    root = trees["sess-1"]
    assert len(root) == 2
    assert root[0].agent.id == "a1"
    assert root[0].children == []
    assert root[1].agent.id == "a2"


def test_build_agent_trees_empty():
    """Sessions with no agents should produce empty tree."""
    session = Session(
        id="sess-2", slug="test2", project="proj", cwd="/tmp",
        branch="main", version="2.1",
        start_time=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        status=SessionStatus.UNKNOWN,
        message_counts={}, token_counts={}, cost_usd=0,
    )
    trees = build_agent_trees([session])
    assert "sess-2" in trees
    assert trees["sess-2"] == []
