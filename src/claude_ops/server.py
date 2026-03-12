"""FastAPI web server for the LCARS browser dashboard."""

from __future__ import annotations

import asyncio
import json
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from claude_ops.parser import (
    AgentStatus,
    Session,
    SessionStatus,
    discover_sessions,
    extract_events,
)
from claude_ops.watcher import find_claude_processes, match_session_status

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATIC_DIR = Path(__file__).parent / "static"
MAX_ACTIVITY_EVENTS = 200
WATCH_INTERVAL = 2.0


def _session_to_dict(session: Session) -> dict[str, Any]:
    """Convert a Session dataclass to a JSON-serializable dict."""
    return {
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


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Convert an ActivityEvent to a JSON-serializable dict."""
    return {
        "timestamp": event.timestamp.isoformat(),
        "session_slug": event.session_slug,
        "event_type": event.event_type.value,
        "summary": event.summary,
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
    claude_cwds = find_claude_processes()
    now = datetime.now(timezone.utc)

    for s in sessions:
        s.status = match_session_status(s.cwd, s.last_activity, claude_cwds)
        for agent in s.agents:
            if now - agent.last_activity > timedelta(seconds=30):
                agent.status = AgentStatus.IDLE
            else:
                agent.status = AgentStatus.ACTIVE

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

    total_cost = sum(
        s.cost_usd + sum(a.cost_usd for a in s.agents) for s in sessions
    )

    return {
        "type": "state",
        "sessions": [_session_to_dict(s) for s in sessions],
        "events": [_event_to_dict(e) for e in all_events],
        "total_cost_usd": total_cost,
    }


# --- FastAPI app (module-level to avoid closure issues with WebSocket) ---

app = FastAPI(title="Claude Ops LCARS")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint that pushes state updates to the client."""
    await websocket.accept()
    try:
        while True:
            state = _load_state()
            await websocket.send_text(json.dumps(state))
            await asyncio.sleep(WATCH_INTERVAL)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.get("/")
async def index():
    """Serve the LCARS dashboard."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def start_web_server(port: int = 1701) -> None:
    """Start the web server and open the browser."""
    import uvicorn

    webbrowser.open(f"http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
