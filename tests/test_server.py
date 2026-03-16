"""Tests for the FastAPI server endpoints."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from claude_ops.server import app, lcars_terminals


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
