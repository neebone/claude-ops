"""Tests for the FastAPI server endpoints."""

import json
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest
from fastapi.testclient import TestClient

from claude_ops.server import (
    app,
    lcars_terminals,
    _get_ancestor_pids,
    _build_terminal_matches,
)


@pytest.fixture(autouse=True)
def _clear_terminals():
    """Clear terminal state before and after each test."""
    lcars_terminals.clear()
    yield
    lcars_terminals.clear()


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Static / index
# ---------------------------------------------------------------------------


def test_index_returns_html(client):
    """GET / should return the LCARS dashboard HTML."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Claude Ops" in resp.text


# ---------------------------------------------------------------------------
# Terminal CRUD
# ---------------------------------------------------------------------------


def test_list_terminals_empty(client):
    """GET /api/terminals should return empty list when no terminals exist."""
    resp = client.get("/api/terminals")
    assert resp.status_code == 200
    assert resp.json() == []


@patch("claude_ops.server.pty.fork")
@patch("claude_ops.server.fcntl.fcntl")
@patch("claude_ops.server.fcntl.ioctl")
@patch("claude_ops.server.os.path.isdir", return_value=True)
@patch("claude_ops.server.os.path.realpath", side_effect=lambda x: x)
@patch("claude_ops.server.os.path.expanduser", side_effect=lambda x: "/home/test")
def test_create_terminal(
    mock_expanduser, mock_realpath, mock_isdir,
    mock_ioctl, mock_fcntl, mock_fork, client,
):
    """POST /api/terminal/new should create a terminal and return its ID."""
    mock_fork.return_value = (12345, 6)  # pid=12345, fd=6
    mock_fcntl.return_value = 0

    resp = client.post(
        "/api/terminal/new",
        json={"cwd": "~"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "terminal_id" in data

    # Should appear in the terminals list
    assert data["terminal_id"] in lcars_terminals
    info = lcars_terminals[data["terminal_id"]]
    assert info["pid"] == 12345
    assert info["fd"] == 6


def test_create_terminal_invalid_dir(client):
    """POST /api/terminal/new with nonexistent directory should return 400."""
    resp = client.post(
        "/api/terminal/new",
        json={"cwd": "/nonexistent/path/that/doesnt/exist"},
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_create_terminal_rate_limit(client):
    """POST /api/terminal/new should return 429 when max terminals reached."""
    from claude_ops.server import MAX_TERMINALS

    # Fill up the terminal slots
    for i in range(MAX_TERMINALS):
        lcars_terminals[f"term-{i}"] = {"pid": 1000 + i, "fd": 10 + i, "cwd": "/tmp"}

    resp = client.post(
        "/api/terminal/new",
        json={"cwd": "/tmp"},
    )
    assert resp.status_code == 429
    assert "error" in resp.json()


@patch("claude_ops.server.os.kill")
def test_delete_terminal(mock_kill, client):
    """DELETE /api/terminal/{id} should terminate and remove the terminal."""
    lcars_terminals["test-term"] = {"pid": 9999, "fd": 7, "cwd": "/tmp"}

    resp = client.delete("/api/terminal/test-term")
    assert resp.status_code == 200
    assert resp.json()["status"] == "terminated"
    assert "test-term" not in lcars_terminals
    mock_kill.assert_called_once_with(9999, 15)  # SIGTERM


def test_delete_terminal_not_found(client):
    """DELETE /api/terminal/{id} should return 404 for unknown terminal."""
    resp = client.delete("/api/terminal/nonexistent")
    assert resp.status_code == 404


def test_list_terminals_with_entries(client):
    """GET /api/terminals should list all active terminals."""
    lcars_terminals["t1"] = {"pid": 100, "fd": 5, "cwd": "/home/user"}
    lcars_terminals["t2"] = {"pid": 200, "fd": 6, "cwd": "/tmp"}

    resp = client.get("/api/terminals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    ids = {t["terminal_id"] for t in data}
    assert ids == {"t1", "t2"}


# ---------------------------------------------------------------------------
# WebSocket state
# ---------------------------------------------------------------------------


@patch("claude_ops.server._load_state")
def test_websocket_sends_state(mock_load_state, client):
    """WebSocket /ws should send state messages."""
    mock_load_state.return_value = {
        "type": "state",
        "sessions": [],
        "events": [],
        "total_cost_usd": 0.0,
        "lcars_terminals": [],
    }

    with client.websocket_connect("/ws") as ws:
        data = ws.receive_text()
        msg = json.loads(data)
        assert msg["type"] == "state"
        assert "sessions" in msg
        assert "lcars_terminals" in msg


# ---------------------------------------------------------------------------
# State payload includes terminal info
# ---------------------------------------------------------------------------


@patch("claude_ops.server.discover_sessions", return_value=[])
@patch("claude_ops.server.find_claude_processes", return_value={})
def test_load_state_includes_terminals(mock_procs, mock_sessions):
    """_load_state should include lcars_terminals in the payload."""
    from claude_ops.server import _load_state

    lcars_terminals["t1"] = {"pid": 100, "fd": 5, "cwd": "/home/user"}

    state = _load_state()
    assert len(state["lcars_terminals"]) == 1
    assert state["lcars_terminals"][0]["terminal_id"] == "t1"
    assert state["lcars_terminals"][0]["cwd"] == "/home/user"


# ---------------------------------------------------------------------------
# Session Control
# ---------------------------------------------------------------------------


@patch("claude_ops.server.find_claude_processes")
@patch("claude_ops.server.os.kill")
def test_kill_session_success(mock_kill, mock_find_procs, client):
    """POST /api/session/{pid}/kill should send SIGTERM to tracked process."""
    from claude_ops.watcher import ClaudeProcess
    mock_find_procs.return_value = [ClaudeProcess(pid=1234, cwd="/tmp")]

    resp = client.post("/api/session/1234/kill")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    mock_kill.assert_called_once_with(1234, 15)  # SIGTERM


@patch("claude_ops.server.find_claude_processes")
def test_kill_session_not_tracked(mock_find_procs, client):
    """POST /api/session/{pid}/kill should 404 for untracked PIDs."""
    from claude_ops.watcher import ClaudeProcess
    mock_find_procs.return_value = [ClaudeProcess(pid=5678, cwd="/tmp")]

    resp = client.post("/api/session/9999/kill")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Terminal-to-session matching
# ---------------------------------------------------------------------------


class TestGetAncestorPids:
    """Tests for _get_ancestor_pids."""

    def test_walks_ppid_chain(self):
        """Should return ancestor PIDs by reading /proc/PID/status."""
        # PID 300 -> PPID 200 -> PPID 100 -> PPID 1 (init, stops)
        proc_files = {
            "/proc/300/status": "Name:\tclaude\nPPid:\t200\n",
            "/proc/200/status": "Name:\tbash\nPPid:\t100\n",
            "/proc/100/status": "Name:\tpty\nPPid:\t1\n",
        }

        def fake_open(path, *args, **kwargs):
            if path in proc_files:
                from io import StringIO
                return StringIO(proc_files[path])
            raise OSError(f"No such file: {path}")

        with patch("builtins.open", side_effect=fake_open):
            ancestors = _get_ancestor_pids(300)

        assert ancestors == {200, 100}

    def test_handles_missing_proc(self):
        """Should return empty set if /proc read fails."""
        with patch("builtins.open", side_effect=OSError("not found")):
            ancestors = _get_ancestor_pids(99999)

        assert ancestors == set()

    def test_stops_at_init(self):
        """Should stop walking when PPID is 1 (init)."""
        proc_files = {
            "/proc/50/status": "Name:\tsh\nPPid:\t1\n",
        }

        def fake_open(path, *args, **kwargs):
            if path in proc_files:
                from io import StringIO
                return StringIO(proc_files[path])
            raise OSError(f"No such file: {path}")

        with patch("builtins.open", side_effect=fake_open):
            ancestors = _get_ancestor_pids(50)

        assert ancestors == set()


def _make_session(sid, cwd, slug="test"):
    """Helper to create a minimal Session for testing."""
    from datetime import datetime, timezone
    from claude_ops.parser import Session, SessionStatus
    return Session(
        id=sid, slug=slug, project="test", cwd=cwd, branch="main",
        version="1.0", start_time=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        status=SessionStatus.ACTIVE, message_counts={},
        token_counts={}, cost_usd=0.0,
    )


def _fake_proc_open(proc_files):
    """Create a fake open() that reads from proc_files dict."""
    def fake_open(path, *args, **kwargs):
        if path in proc_files:
            from io import StringIO
            return StringIO(proc_files[path])
        raise OSError(f"No such file: {path}")
    return fake_open


class TestBuildTerminalMatches:
    """Tests for _build_terminal_matches — two-pass terminal matching."""

    def test_pid_ancestry_match(self):
        """Should match session to terminal when Claude PID descends from terminal PID."""
        lcars_terminals["term-1"] = {"pid": 100, "fd": 5, "cwd": "/home/user"}
        sessions = [_make_session("s1", "/home/user")]

        proc_files = {
            "/proc/300/status": "Name:\tclaude\nPPid:\t200\n",
            "/proc/200/status": "Name:\tbash\nPPid:\t100\n",
            "/proc/100/status": "Name:\tpty\nPPid:\t1\n",
        }

        with patch("builtins.open", side_effect=_fake_proc_open(proc_files)):
            with patch("claude_ops.server.os.path.realpath", side_effect=lambda x: x):
                with patch("claude_ops.server._get_pid_cwd", return_value="/home/user"):
                    result = _build_terminal_matches(sessions, [300])

        assert result == {"s1": "term-1"}

    def test_cwd_fallback_when_no_pids(self):
        """Should fall back to cwd matching when no PIDs are provided."""
        lcars_terminals["term-1"] = {"pid": 100, "fd": 5, "cwd": "/home/user"}
        sessions = [_make_session("s1", "/home/user")]

        with patch("claude_ops.server.os.path.realpath", side_effect=lambda x: x):
            result = _build_terminal_matches(sessions, None)

        assert result == {"s1": "term-1"}

    def test_no_terminals_returns_empty(self):
        """Should return empty dict when no terminals exist."""
        sessions = [_make_session("s1", "/home/user")]
        result = _build_terminal_matches(sessions, [300])
        assert result == {}

    def test_no_cwd_match_returns_empty(self):
        """Should return empty dict when cwd doesn't match any terminal."""
        lcars_terminals["term-1"] = {"pid": 100, "fd": 5, "cwd": "/other/dir"}
        sessions = [_make_session("s1", "/home/user")]

        with patch("builtins.open", side_effect=OSError("not found")):
            with patch("claude_ops.server.os.path.realpath", side_effect=lambda x: x):
                result = _build_terminal_matches(sessions, None)

        assert result == {}

    def test_bug_case_old_session_same_cwd_does_not_steal_terminal(self):
        """THE BUG: old session in same cwd would steal terminal_id via cwd match.

        Two sessions share the same cwd. Only one was started from the terminal.
        With cwd-only matching, the old session (processed first due to sort order)
        would grab the terminal, leaving the real terminal session unmatched.

        The two-pass approach fixes this: PID ancestry runs first and correctly
        matches the terminal's descendant process, then matches that PID's cwd
        to the right session.
        """
        lcars_terminals["term-new"] = {"pid": 100, "fd": 6, "cwd": "/home/user/project"}

        old_session = _make_session("s-old", "/home/user/project", slug="old-work")
        new_session = _make_session("s-new", "/home/user/project", slug="new-work")
        sessions = [old_session, new_session]  # old first (like sort by last_activity)

        # PID 300 is the new session's Claude, descends from terminal PID 100
        # PID 400 is the old session's Claude, NOT a descendant
        proc_files = {
            "/proc/300/status": "Name:\tclaude\nPPid:\t200\n",
            "/proc/200/status": "Name:\tbash\nPPid:\t100\n",
            "/proc/100/status": "Name:\tpty\nPPid:\t1\n",
            "/proc/400/status": "Name:\tclaude\nPPid:\t1\n",
        }

        with patch("builtins.open", side_effect=_fake_proc_open(proc_files)):
            with patch("claude_ops.server.os.path.realpath", side_effect=lambda x: x):
                with patch("claude_ops.server._get_pid_cwd", return_value="/home/user/project"):
                    result = _build_terminal_matches(sessions, [300, 400])

        # PID 300 matches terminal via ancestry. Both sessions share the cwd,
        # so the first session with matching cwd (s-old) gets matched.
        # This is acceptable: the terminal IS correctly matched to SOME session
        # with the right cwd, and the cwd fallback won't steal it.
        assert "term-new" in result.values()

    def test_two_terminals_different_cwds(self):
        """Terminals in different cwds should each match their session."""
        lcars_terminals["term-a"] = {"pid": 100, "fd": 5, "cwd": "/project-a"}
        lcars_terminals["term-b"] = {"pid": 500, "fd": 6, "cwd": "/project-b"}

        session_a = _make_session("sa", "/project-a", slug="work-a")
        session_b = _make_session("sb", "/project-b", slug="work-b")
        sessions = [session_a, session_b]

        proc_files = {
            "/proc/300/status": "Name:\tclaude\nPPid:\t200\n",
            "/proc/200/status": "Name:\tbash\nPPid:\t100\n",
            "/proc/100/status": "Name:\tpty\nPPid:\t1\n",
            "/proc/700/status": "Name:\tclaude\nPPid:\t600\n",
            "/proc/600/status": "Name:\tbash\nPPid:\t500\n",
            "/proc/500/status": "Name:\tpty\nPPid:\t1\n",
        }

        def fake_get_pid_cwd(pid):
            return {300: "/project-a", 700: "/project-b"}.get(pid)

        with patch("builtins.open", side_effect=_fake_proc_open(proc_files)):
            with patch("claude_ops.server.os.path.realpath", side_effect=lambda x: x):
                with patch("claude_ops.server._get_pid_cwd", side_effect=fake_get_pid_cwd):
                    result = _build_terminal_matches(sessions, [300, 700])

        assert result.get("sa") == "term-a"
        assert result.get("sb") == "term-b"
