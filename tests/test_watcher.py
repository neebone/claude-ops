from claude_ops.watcher import find_claude_processes, match_sessions_status
from datetime import datetime, timezone
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
            assert procs[0].pid == 1234
            assert procs[0].cwd == "/home/user/work/app"
            assert procs[1].pid == 5678
            assert procs[1].cwd == "/home/user/work/lib"


def test_find_claude_processes_handles_ps_failure():
    with patch("claude_ops.watcher._run_ps", return_value=None):
        procs = find_claude_processes()
        assert procs is None


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
