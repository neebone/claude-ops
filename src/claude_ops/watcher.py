"""File watching and process detection for Claude Code sessions."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_ops.parser import Session, SessionStatus

IDLE_THRESHOLD = timedelta(seconds=30)


@dataclass
class ClaudeProcess:
    """A running Claude process with its metadata."""

    pid: int
    cwd: str


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


def find_claude_processes() -> list[ClaudeProcess] | None:
    """Find all running Claude processes with cwd and start time.

    Returns a list of ClaudeProcess, or None if detection failed.
    """
    output = _run_ps()
    if output is None:
        return None

    processes: list[ClaudeProcess] = []

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
        if not cwd:
            continue
        processes.append(ClaudeProcess(pid=pid, cwd=cwd))

    return processes


def match_sessions_status(
    sessions: list[Session],
    processes: list[ClaudeProcess] | None,
) -> None:
    """Match sessions to processes by cwd and assign status in-place.

    For each cwd, counts running Claude processes and assigns the N most
    recently active sessions as active/idle. Remaining sessions are marked done.
    """
    if processes is None:
        for s in sessions:
            s.status = SessionStatus.UNKNOWN
        return

    now = datetime.now(timezone.utc)

    # Count Claude processes per resolved cwd
    proc_count_by_cwd: dict[str, int] = {}
    for proc in processes:
        resolved = os.path.realpath(proc.cwd)
        proc_count_by_cwd[resolved] = proc_count_by_cwd.get(resolved, 0) + 1

    # Group sessions by resolved cwd
    sessions_by_cwd: dict[str, list[Session]] = {}
    for s in sessions:
        resolved = os.path.realpath(s.cwd) if s.cwd else ""
        sessions_by_cwd.setdefault(resolved, []).append(s)

    # For each cwd, assign the N most recently active sessions as active/idle
    for cwd, cwd_sessions in sessions_by_cwd.items():
        n_procs = proc_count_by_cwd.get(cwd, 0)
        if n_procs == 0:
            for s in cwd_sessions:
                s.status = SessionStatus.DONE
            continue

        # Sort by last_activity descending — most recent first
        cwd_sessions.sort(key=lambda s: s.last_activity, reverse=True)
        for i, s in enumerate(cwd_sessions):
            if i < n_procs:
                if now - s.last_activity > IDLE_THRESHOLD:
                    s.status = SessionStatus.IDLE
                else:
                    s.status = SessionStatus.ACTIVE
            else:
                s.status = SessionStatus.DONE
