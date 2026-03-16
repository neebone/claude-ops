"""FastAPI web server for the LCARS browser dashboard."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import signal
import struct
import termios
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from claude_ops.parser import (
    AgentStatus,
    Session,
    SessionStatus,
    discover_sessions,
    extract_events,
    build_agent_trees,
)
from claude_ops.resources import get_process_resources
from claude_ops.watcher import find_claude_processes, match_sessions_status

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATIC_DIR = Path(__file__).parent / "static"
MAX_ACTIVITY_EVENTS = 200
WATCH_INTERVAL = 2.0

# ---------------------------------------------------------------------------
# LCARS Terminal Management
# ---------------------------------------------------------------------------

lcars_terminals: dict[str, dict[str, Any]] = {}
MAX_TERMINALS = 10


class TerminalRequest(BaseModel):
    """Request body for creating a new terminal."""

    cwd: str


def _get_ancestor_pids(pid: int, max_depth: int = 10) -> set[int]:
    """Walk up the PPID chain from /proc and return all ancestor PIDs."""
    ancestors: set[int] = set()
    current = pid
    for _ in range(max_depth):
        try:
            with open(f"/proc/{current}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        if ppid <= 1:
                            return ancestors
                        ancestors.add(ppid)
                        current = ppid
                        break
                else:
                    return ancestors
        except (OSError, ValueError):
            return ancestors
    return ancestors


def _get_pid_cwd(pid: int) -> str | None:
    """Get the working directory of a process via /proc symlink."""
    try:
        return str(Path(f"/proc/{pid}/cwd").resolve())
    except OSError:
        return None


def _build_terminal_matches(
    sessions: list[Session],
    all_pids: list[int] | None = None,
) -> dict[str, str]:
    """Build a map of session_id -> terminal_id using two-pass matching.

    Pass 1 (PID ancestry — definitive): For each terminal, find which Claude
    process PID is a descendant of the terminal PID. Then match that PID to
    the session that owns it via cwd. This is always correct because it uses
    the actual process tree, not just directory matching.

    Pass 2 (CWD fallback): For unmatched terminals, fall back to cwd matching.
    This handles the case where the Claude process hasn't been detected yet.

    Two-pass ensures that PID ancestry always wins, preventing older sessions
    in the same cwd from stealing a terminal match.
    """
    if not lcars_terminals:
        return {}

    session_terminal_map: dict[str, str] = {}  # session.id -> terminal_id
    matched_terminal_ids: set[str] = set()
    matched_session_ids: set[str] = set()

    # Pass 1: PID ancestry (definitive)
    # For each Claude PID, walk its ancestor chain and check against terminal PIDs
    terminal_pids = {
        tinfo["pid"]: tid
        for tid, tinfo in lcars_terminals.items()
        if tinfo.get("pid")
    }

    if all_pids and terminal_pids:
        for claude_pid in all_pids:
            ancestors = _get_ancestor_pids(claude_pid)
            for term_pid, tid in terminal_pids.items():
                if tid in matched_terminal_ids:
                    continue
                if term_pid in ancestors:
                    # Found the terminal. Now find which session this PID belongs to
                    # by matching the PID's cwd to a session cwd.
                    try:
                        pid_cwd = _get_pid_cwd(claude_pid)
                    except OSError:
                        continue
                    if not pid_cwd:
                        continue
                    for s in sessions:
                        if s.id in matched_session_ids:
                            continue
                        if s.cwd and os.path.realpath(s.cwd) == pid_cwd:
                            session_terminal_map[s.id] = tid
                            matched_terminal_ids.add(tid)
                            matched_session_ids.add(s.id)
                            break
                    break  # This PID is matched, move to next

    # Pass 2: CWD fallback for remaining unmatched terminals
    for s in sessions:
        if s.id in matched_session_ids:
            continue
        if not s.cwd:
            continue
        resolved_cwd = os.path.realpath(s.cwd)
        for tid, tinfo in lcars_terminals.items():
            if tid in matched_terminal_ids:
                continue
            if tinfo.get("pid") and os.path.realpath(tinfo["cwd"]) == resolved_cwd:
                session_terminal_map[s.id] = tid
                matched_terminal_ids.add(tid)
                matched_session_ids.add(s.id)
                break

    return session_terminal_map


def _session_to_dict(
    session: Session,
    terminal_id: str | None = None,
    session_pid: int | None = None,
) -> dict[str, Any]:
    """Convert a Session dataclass to a JSON-serializable dict."""

    result = {
        "id": session.id,
        "slug": session.slug,
        "project": session.project,
        "cwd": session.cwd,
        "branch": session.branch,
        "version": session.version,
        "start_time": session.start_time.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "status": session.status.value,
        "message_counts": session.message_counts,
        "token_counts": session.token_counts,
        "cost_usd": session.cost_usd,
        "agents": [
            {
                "id": agent.id,
                "model": agent.model,
                "task_summary": agent.task_summary,
                "start_time": agent.start_time.isoformat(),
                "last_activity": agent.last_activity.isoformat(),
                "status": agent.status.value,
                "token_counts": agent.token_counts,
                "cost_usd": agent.cost_usd,
            }
            for agent in session.agents
        ],
    }
    if terminal_id:
        result["terminal_id"] = terminal_id
    if session_pid:
        result["pid"] = session_pid
    return result


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Convert an ActivityEvent to a JSON-serializable dict."""
    return {
        "timestamp": event.timestamp.isoformat(),
        "session_slug": event.session_slug,
        "event_type": event.event_type.value,
        "summary": event.summary,
    }


def _agent_node_to_dict(node) -> dict[str, Any]:
    """Convert an AgentNode to a JSON-serializable dict."""
    return {
        "agent": {
            "id": node.agent.id,
            "model": node.agent.model,
            "task_summary": node.agent.task_summary,
            "start_time": node.agent.start_time.isoformat(),
            "last_activity": node.agent.last_activity.isoformat(),
            "status": node.agent.status.value,
            "token_counts": node.agent.token_counts,
            "cost_usd": node.agent.cost_usd,
        },
        "children": [_agent_node_to_dict(c) for c in node.children],
    }


def _find_session_file(session: Session) -> Path | None:
    """Find the JSONL file for a session."""
    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{session.id}.jsonl"
        if candidate.exists():
            return candidate
    return None


def _load_state() -> dict[str, Any]:
    """Load all sessions and activity events, return as JSON-serializable dict."""
    sessions = discover_sessions(CLAUDE_PROJECTS_DIR)
    processes = find_claude_processes()
    now = datetime.now(timezone.utc)

    pid_map = match_sessions_status(sessions, processes)

    for s in sessions:
        for agent in s.agents:
            if now - agent.last_activity > timedelta(seconds=30):
                agent.status = AgentStatus.IDLE
            else:
                agent.status = AgentStatus.ACTIVE

    # Resource monitoring
    resources_data = {}
    if processes:
        pids = [p.pid for p in processes]
        resources = get_process_resources(pids)
        for pid, stats in resources.items():
            label = pid_map.get(pid, str(pid)) if pid_map else str(pid)
            resources_data[str(pid)] = {
                "cpu_pct": round(stats.cpu_pct, 1),
                "rss_mb": round(stats.rss_mb, 1),
                "label": label,
            }

    # Build cwd-to-PID lookup for session kill buttons
    pid_by_cwd: dict[str, int] = {}
    if processes:
        for proc in processes:
            resolved = os.path.realpath(proc.cwd)
            pid_by_cwd[resolved] = proc.pid

    seen_keys: set[tuple] = set()
    all_events = []

    for s in sessions:
        session_file = _find_session_file(s)
        if session_file:
            for event in extract_events(session_file, s.slug):
                key = (event.timestamp, event.session_slug, event.event_type, event.summary)
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_events.append(event)
            agent_dir = session_file.parent / s.id / "subagents"
            if agent_dir.is_dir():
                for agent_file in agent_dir.glob("agent-*.jsonl"):
                    for event in extract_events(agent_file, s.slug, is_agent=True):
                        key = (
                            event.timestamp, event.session_slug,
                            event.event_type, event.summary,
                        )
                        if key not in seen_keys:
                            seen_keys.add(key)
                            all_events.append(event)

    all_events.sort(key=lambda e: e.timestamp)
    all_events = all_events[-MAX_ACTIVITY_EVENTS:]

    # Agent trees
    agent_trees = build_agent_trees(sessions)
    agent_trees_data = {}
    for sid, nodes in agent_trees.items():
        agent_trees_data[sid] = [_agent_node_to_dict(n) for n in nodes]

    # Per-session events (last 20 per session)
    session_events: dict[str, list] = {}
    for s in sessions:
        slug_events = [e for e in all_events if e.session_slug == s.slug]
        session_events[s.id] = [_event_to_dict(e) for e in slug_events[-20:]]

    total_cost = sum(
        s.cost_usd + sum(a.cost_usd for a in s.agents) for s in sessions
    )

    # Build LCARS terminal info for the state payload
    terminal_info = [
        {"terminal_id": tid, "cwd": tinfo["cwd"], "pid": tinfo["pid"]}
        for tid, tinfo in lcars_terminals.items()
    ]

    # Match terminals to sessions (PID ancestry first, then cwd fallback)
    all_pids = [p.pid for p in processes] if processes else None
    terminal_matches = _build_terminal_matches(sessions, all_pids)

    session_dicts = []
    for s in sessions:
        s_pid = None
        if s.cwd:
            resolved = os.path.realpath(s.cwd)
            s_pid = pid_by_cwd.get(resolved)
        d = _session_to_dict(s, terminal_matches.get(s.id), s_pid)
        session_dicts.append(d)

    return {
        "type": "state",
        "sessions": session_dicts,
        "events": [_event_to_dict(e) for e in all_events],
        "total_cost_usd": total_cost,
        "lcars_terminals": terminal_info,
        "resources": resources_data,
        "agent_trees": agent_trees_data,
        "session_events": session_events,
    }


# --- FastAPI app (module-level to avoid closure issues with WebSocket) ---

app = FastAPI(title="Claude Ops LCARS")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint that pushes state updates to the client."""
    await websocket.accept()
    try:
        while True:
            try:
                state = await asyncio.get_event_loop().run_in_executor(
                    None, _load_state,
                )
            except Exception:
                await asyncio.sleep(WATCH_INTERVAL)
                continue
            await websocket.send_text(json.dumps(state))
            await asyncio.sleep(WATCH_INTERVAL)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Terminal REST endpoints
# ---------------------------------------------------------------------------


@app.post("/api/terminal/new")
async def create_terminal(req: TerminalRequest):
    """Spawn a PTY running `claude` in the given directory."""
    if len(lcars_terminals) >= MAX_TERMINALS:
        return JSONResponse({"error": "Maximum terminal limit reached"}, status_code=429)

    cwd = os.path.realpath(os.path.expanduser(req.cwd or "~"))
    if not os.path.isdir(cwd):
        return JSONResponse({"error": f"Directory does not exist: {cwd}"}, status_code=400)

    pid, fd = pty.fork()

    if pid == 0:
        # Child process — exec claude
        os.chdir(cwd)
        # Remove CLAUDECODE env var so nested sessions are allowed
        os.environ.pop("CLAUDECODE", None)
        os.execvp("claude", ["claude"])
    else:
        # Parent — set fd to non-blocking for async reads
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        terminal_id = str(uuid.uuid4())
        lcars_terminals[terminal_id] = {
            "pid": pid,
            "fd": fd,
            "cwd": cwd,
        }
        # Set initial size
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        return {"terminal_id": terminal_id}


@app.delete("/api/terminal/{terminal_id}")
async def delete_terminal(terminal_id: str):
    """Kill a terminal process and clean up."""
    if terminal_id not in lcars_terminals:
        return JSONResponse({"error": "Terminal not found"}, status_code=404)

    info = lcars_terminals.pop(terminal_id)
    pid = info["pid"]
    fd = info["fd"]

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    async def _force_kill():
        """Wait for graceful exit, then force kill and reap."""
        await asyncio.sleep(5)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    asyncio.create_task(_force_kill())
    return {"status": "terminated"}


@app.get("/api/terminals")
async def list_terminals():
    """List all LCARS-managed terminals."""
    return [
        {"terminal_id": tid, "cwd": info["cwd"], "pid": info["pid"]}
        for tid, info in lcars_terminals.items()
    ]


# ---------------------------------------------------------------------------
# Session Control
# ---------------------------------------------------------------------------

@app.post("/api/session/{pid}/kill")
async def kill_session(pid: int):
    """Send SIGTERM to a tracked Claude process."""
    processes = find_claude_processes()
    if processes is None:
        return JSONResponse({"status": "error", "detail": "Process detection failed"}, status_code=500)

    tracked_pids = {p.pid for p in processes}
    if pid not in tracked_pids:
        return JSONResponse({"status": "error", "detail": "PID not tracked"}, status_code=404)

    try:
        os.kill(pid, signal.SIGTERM)
        return {"status": "ok"}
    except ProcessLookupError:
        return JSONResponse({"status": "error", "detail": "Process not found"}, status_code=404)
    except PermissionError:
        return JSONResponse({"status": "error", "detail": "Permission denied"}, status_code=500)


# ---------------------------------------------------------------------------
# Terminal WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws/terminal/{terminal_id}")
async def terminal_websocket(websocket: WebSocket, terminal_id: str):
    """Bidirectional WebSocket for terminal I/O."""
    if terminal_id not in lcars_terminals:
        await websocket.close(code=4004, reason="Terminal not found")
        return

    await websocket.accept()
    info = lcars_terminals[terminal_id]
    fd = info["fd"]

    # Use asyncio event-driven reader instead of blocking thread pool
    pty_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _on_pty_readable():
        """Callback when PTY fd has data (called by event loop)."""
        try:
            data = os.read(fd, 4096)
            if data:
                pty_queue.put_nowait(data)
            else:
                pty_queue.put_nowait(None)
        except OSError:
            pty_queue.put_nowait(None)

    loop.add_reader(fd, _on_pty_readable)

    async def read_pty():
        """Forward PTY output to browser via WebSocket."""
        try:
            while True:
                data = await pty_queue.get()
                if data is None:
                    break
                await websocket.send_text(data.decode("utf-8", errors="replace"))
        except Exception:
            pass

    read_task = asyncio.create_task(read_pty())

    try:
        while True:
            message = await websocket.receive_text()

            # Check for resize messages
            try:
                msg = json.loads(message)
                if isinstance(msg, dict) and msg.get("type") == "resize":
                    cols = msg.get("cols", 80)
                    rows = msg.get("rows", 24)
                    pid = info["pid"]
                    fcntl.ioctl(
                        fd,
                        termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0),
                    )
                    # Always send SIGWINCH so the program redraws
                    try:
                        os.kill(pid, signal.SIGWINCH)
                    except ProcessLookupError:
                        pass
                    continue
            except (json.JSONDecodeError, ValueError):
                pass

            # Regular keystroke — write to PTY
            try:
                os.write(fd, message.encode("utf-8"))
            except OSError:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        loop.remove_reader(fd)
        read_task.cancel()


@app.get("/")
async def index():
    """Serve the LCARS dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def _start_zombie_reaper():
    """Periodically reap zombie child processes and clean dead terminals."""

    async def _reaper():
        while True:
            await asyncio.sleep(10)
            dead = []
            for tid, info in lcars_terminals.items():
                try:
                    result = os.waitpid(info["pid"], os.WNOHANG)
                    if result[0] != 0:
                        dead.append(tid)
                except ChildProcessError:
                    dead.append(tid)
            for tid in dead:
                info = lcars_terminals.pop(tid, None)
                if info:
                    try:
                        os.close(info["fd"])
                    except OSError:
                        pass

    asyncio.create_task(_reaper())


def start_web_server(port: int = 1701) -> None:
    """Start the web server and open the browser."""
    import uvicorn

    webbrowser.open(f"http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
