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
) -> dict[int, str] | None:
    """Match sessions to processes by cwd and assign status in-place.

    Returns a mapping of PID -> session slug for labelling resource gauges.
    When multiple sessions share a cwd, maps PID to cwd basename instead.
    Returns None if processes is None.
    """
    if processes is None:
        for s in sessions:
            s.status = SessionStatus.UNKNOWN
        return None

    now = datetime.now(timezone.utc)

    # Group processes by resolved cwd
    procs_by_cwd: dict[str, list[ClaudeProcess]] = {}
    for proc in processes:
        resolved = os.path.realpath(proc.cwd)
        procs_by_cwd.setdefault(resolved, []).append(proc)

    # Count per cwd
    proc_count_by_cwd: dict[str, int] = {
        cwd: len(procs) for cwd, procs in procs_by_cwd.items()
    }

    # Group sessions by resolved cwd
    sessions_by_cwd: dict[str, list[Session]] = {}
    for s in sessions:
        resolved = os.path.realpath(s.cwd) if s.cwd else ""
        sessions_by_cwd.setdefault(resolved, []).append(s)

    # Build PID-to-label mapping
    pid_map: dict[int, str] = {}

    for cwd, cwd_sessions in sessions_by_cwd.items():
        n_procs = proc_count_by_cwd.get(cwd, 0)
        if n_procs == 0:
            for s in cwd_sessions:
                s.status = SessionStatus.DONE
            continue

        cwd_sessions.sort(key=lambda s: s.last_activity, reverse=True)

        # Determine label: use session slug if 1 session, else cwd basename
        use_slug = len(cwd_sessions) == 1

        for i, s in enumerate(cwd_sessions):
            if i < n_procs:
                if now - s.last_activity > IDLE_THRESHOLD:
                    s.status = SessionStatus.IDLE
                else:
                    s.status = SessionStatus.ACTIVE
            else:
                s.status = SessionStatus.DONE

        # Map PIDs to labels
        cwd_procs = procs_by_cwd.get(cwd, [])
        for j, proc in enumerate(cwd_procs):
            if use_slug and cwd_sessions:
                pid_map[proc.pid] = cwd_sessions[min(j, len(cwd_sessions) - 1)].slug
            else:
                pid_map[proc.pid] = os.path.basename(cwd)

    return pid_map
