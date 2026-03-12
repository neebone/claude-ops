"""File watching and process detection for Claude Code sessions."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_ops.parser import SessionStatus

IDLE_THRESHOLD = timedelta(seconds=30)


def _run_ps() -> str | None:
    """Run ps aux and return output, or None on failure."""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _get_process_cwd(pid: int) -> str | None:
    """Get the working directory of a process via /proc."""
    try:
        cwd = Path(f"/proc/{pid}/cwd").resolve()
        return str(cwd)
    except (OSError, PermissionError):
        return None


def find_claude_processes() -> set[str] | None:
    """Find working directories of all running claude processes.

    Returns a set of cwd strings, or None if ps failed.
    """
    output = _run_ps()
    if output is None:
        return None

    cwds: set[str] = set()
    for line in output.strip().split("\n"):
        if "claude" not in line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        cwd = _get_process_cwd(pid)
        if cwd:
            cwds.add(cwd)
    return cwds


def match_session_status(
    session_cwd: str,
    last_activity: datetime,
    claude_cwds: set[str] | None,
) -> SessionStatus:
    """Determine session status from process info and activity time."""
    if claude_cwds is None:
        return SessionStatus.UNKNOWN

    if session_cwd not in claude_cwds:
        return SessionStatus.DONE

    now = datetime.now(timezone.utc)
    if now - last_activity > IDLE_THRESHOLD:
        return SessionStatus.IDLE

    return SessionStatus.ACTIVE
