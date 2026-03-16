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


def test_match_sessions_status_returns_pid_map():
    """match_sessions_status should return a PID-to-session-slug mapping."""
    from claude_ops.parser import Session, SessionStatus
    from claude_ops.watcher import ClaudeProcess, match_sessions_status

    now = datetime.now(timezone.utc)
    sessions = [
        Session(
            id="s1", slug="my-session", project="proj", cwd="/home/user/work",
            branch="main", version="2.1", start_time=now, last_activity=now,
            status=SessionStatus.UNKNOWN, message_counts={}, token_counts={}, cost_usd=0,
        ),
    ]
    processes = [ClaudeProcess(pid=1234, cwd="/home/user/work")]

    pid_map = match_sessions_status(sessions, processes)

    assert pid_map is not None
    assert 1234 in pid_map
    assert pid_map[1234] == "my-session"
