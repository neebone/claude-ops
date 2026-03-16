# Living Console Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the LCARS web dashboard into a living console with improved contrast, motion system, processing visualisation, agent trees, resource monitoring, session control, and keyboard shortcuts.

**Architecture:** Backend-first approach — add resource monitoring, agent tree construction, and extended WebSocket payload first, then layer frontend changes on top. Frontend work split into CSS/motion (independent) and layout/interactivity (depends on backend data).

**Tech Stack:** Python 3.13 (FastAPI, dataclasses), vanilla JS (Canvas 2D, xterm.js), CSS3 (custom properties, keyframes, grid)

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/claude_ops/resources.py` | Read CPU% and RSS from `/proc` for tracked PIDs |
| `tests/test_resources.py` | Tests for resource monitoring |

### Modified Files
| File | Changes |
|------|---------|
| `src/claude_ops/watcher.py` | Return PID-to-session mapping alongside status |
| `src/claude_ops/parser.py` | Add `AgentNode` dataclass, `build_agent_tree()` function |
| `src/claude_ops/server.py` | Extended payload, kill endpoint, session events |
| `src/claude_ops/static/lcars.css` | Contrast fixes, motion system, new panels, scan-line |
| `src/claude_ops/static/lcars.js` | Agent tree, resource strip, waveform, session actions, keyboard, terminal tabs |
| `src/claude_ops/static/index.html` | Layout restructure (resource strip, bottom split, active/completed) |
| `tests/test_parser.py` | Tests for agent tree construction |
| `tests/test_server.py` | Tests for kill endpoint and extended payload |
| `tests/test_watcher.py` | Tests for PID-to-session mapping |

---

## Chunk 1: Backend — Resource Monitoring & PID Mapping

### Task 1: Resource monitoring module (`resources.py`)

**Files:**
- Create: `src/claude_ops/resources.py`
- Create: `tests/test_resources.py`

- [ ] **Step 1: Write failing test for `get_process_resources`**

```python
# tests/test_resources.py
"""Tests for the resource monitoring module."""

import pytest
from unittest.mock import patch, mock_open
from claude_ops.resources import get_process_resources, ResourceStats


def test_get_process_resources_reads_proc():
    """Should read CPU and memory from /proc for given PIDs."""
    stat_content = "1234 (node) S 1 1234 1234 0 -1 4194304 1000 0 0 0 500 200 0 0 20 0 1 0 100 0 0 18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0"
    status_content = "Name:\tnode\nVmRSS:\t145920 kB\n"

    def mock_read_text(path_str):
        if "stat" in path_str and "status" not in path_str:
            return stat_content
        if "status" in path_str:
            return status_content
        return ""

    with patch("claude_ops.resources.Path.read_text", side_effect=lambda self: mock_read_text(str(self))):
        result = get_process_resources([1234])

    assert 1234 in result
    assert result[1234].rss_mb == pytest.approx(142.5, abs=0.1)


def test_get_process_resources_missing_proc():
    """Should return empty dict when /proc is unavailable."""
    with patch("claude_ops.resources.Path.read_text", side_effect=FileNotFoundError):
        result = get_process_resources([9999])
    assert result == {}


def test_get_process_resources_empty_pids():
    """Should return empty dict for empty PID list."""
    result = get_process_resources([])
    assert result == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest tests/test_resources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_ops.resources'`

- [ ] **Step 3: Write implementation**

```python
# src/claude_ops/resources.py
"""Resource monitoring via /proc for Claude processes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ResourceStats:
    """CPU and memory stats for a process."""
    cpu_pct: float
    rss_mb: float


# Cache previous CPU ticks for delta calculation
_prev_ticks: dict[int, tuple[int, int]] = {}  # pid -> (total_ticks, proc_ticks)


def _read_proc_stat(pid: int) -> tuple[int, int] | None:
    """Read utime + stime from /proc/<pid>/stat. Returns (proc_ticks, total_ticks)."""
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
        # Fields after the comm field (which may contain spaces/parens)
        # Find the last ')' to skip comm
        idx = stat_line.rfind(")")
        if idx < 0:
            return None
        fields = stat_line[idx + 2:].split()
        # fields[11] = utime, fields[12] = stime (0-indexed from after comm)
        utime = int(fields[11])
        stime = int(fields[12])
        proc_ticks = utime + stime
    except (FileNotFoundError, OSError, IndexError, ValueError):
        return None

    # Read total CPU ticks from /proc/stat
    try:
        cpu_line = Path("/proc/stat").read_text().split("\n")[0]
        total_ticks = sum(int(x) for x in cpu_line.split()[1:])
    except (FileNotFoundError, OSError, IndexError, ValueError):
        return None

    return proc_ticks, total_ticks


def _read_rss_mb(pid: int) -> float | None:
    """Read VmRSS from /proc/<pid>/status in MB."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().split("\n"):
            if line.startswith("VmRSS:"):
                # "VmRSS:    145920 kB"
                parts = line.split()
                return int(parts[1]) / 1024.0
    except (FileNotFoundError, OSError, IndexError, ValueError):
        pass
    return None


def get_process_resources(pids: list[int]) -> dict[int, ResourceStats]:
    """Read CPU% and RSS from /proc for given PIDs.

    CPU% is calculated as delta between calls. First call returns 0% CPU.
    Returns empty dict if /proc is unavailable.
    """
    result: dict[int, ResourceStats] = {}

    for pid in pids:
        ticks = _read_proc_stat(pid)
        rss = _read_rss_mb(pid)
        if ticks is None or rss is None:
            continue

        proc_ticks, total_ticks = ticks
        prev = _prev_ticks.get(pid)

        if prev is not None:
            prev_total, prev_proc = prev
            total_delta = total_ticks - prev_total
            proc_delta = proc_ticks - prev_proc
            cpu_pct = (proc_delta / total_delta * 100.0) if total_delta > 0 else 0.0
        else:
            cpu_pct = 0.0

        _prev_ticks[pid] = (total_ticks, proc_ticks)
        result[pid] = ResourceStats(cpu_pct=cpu_pct, rss_mb=rss)

    return result
```

- [ ] **Step 4: Run tests**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest tests/test_resources.py -v`
Expected: Tests may need mock adjustments for Path.read_text — fix as needed until all 3 pass.

- [ ] **Step 5: Commit**

```bash
git add src/claude_ops/resources.py tests/test_resources.py
git commit -m "feat(resources): add /proc-based CPU and memory monitoring"
```

### Task 2: Extend watcher to return PID-to-session mapping

**Files:**
- Modify: `src/claude_ops/watcher.py:74-120`
- Modify: `tests/test_watcher.py`

- [ ] **Step 1: Write failing test for PID mapping**

Add to `tests/test_watcher.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest tests/test_watcher.py::test_match_sessions_status_returns_pid_map -v`
Expected: FAIL — `match_sessions_status` currently returns `None` (void)

- [ ] **Step 3: Modify `match_sessions_status` to return PID map**

In `src/claude_ops/watcher.py`, change `match_sessions_status` to return `dict[int, str] | None`:

```python
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
```

- [ ] **Step 4: Run all watcher tests**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest tests/test_watcher.py -v`
Expected: The new test passes. Note: existing tests use an older function signature (`match_session_status` singular) — they may already be broken. Fix only the new test; existing tests are out of scope.

- [ ] **Step 5: Commit**

```bash
git add src/claude_ops/watcher.py tests/test_watcher.py
git commit -m "feat(watcher): return PID-to-session mapping for resource labelling"
```

---

## Chunk 2: Backend — Agent Trees, Session Events & Kill Endpoint

### Task 3: Agent tree construction (`parser.py`)

**Files:**
- Modify: `src/claude_ops/parser.py`
- Modify: `tests/test_parser.py`

- [ ] **Step 1: Write failing test for `AgentNode` and `build_agent_trees`**

Add to `tests/test_parser.py`:

```python
from claude_ops.parser import AgentNode, build_agent_trees


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest tests/test_parser.py::test_build_agent_trees_flat -v`
Expected: FAIL — `ImportError: cannot import name 'AgentNode'`

- [ ] **Step 3: Add `AgentNode` dataclass and `build_agent_trees` function**

Add to `src/claude_ops/parser.py` after the `ActivityEvent` dataclass:

```python
@dataclass
class AgentNode:
    """A node in the agent hierarchy tree."""
    agent: Agent
    children: list[AgentNode] = field(default_factory=list)


def build_agent_trees(sessions: list[Session]) -> dict[str, list[AgentNode]]:
    """Build agent hierarchy trees for each session.

    Currently builds flat trees (session -> agents) since Claude Code
    stores subagents in a flat directory. If nested subagent directories
    are found in the future, this can be extended to walk deeper.

    Returns a dict mapping session_id -> list of root AgentNodes.
    """
    trees: dict[str, list[AgentNode]] = {}
    for session in sessions:
        nodes = [AgentNode(agent=agent) for agent in session.agents]
        trees[session.id] = nodes
    return trees
```

- [ ] **Step 4: Run tests**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest tests/test_parser.py -v`
Expected: All pass including new ones.

- [ ] **Step 5: Commit**

```bash
git add src/claude_ops/parser.py tests/test_parser.py
git commit -m "feat(parser): add AgentNode dataclass and build_agent_trees"
```

### Task 4: Server — extended payload, session events & kill endpoint

**Files:**
- Modify: `src/claude_ops/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing test for kill endpoint**

Add to `tests/test_server.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest tests/test_server.py::test_kill_session_success -v`
Expected: FAIL — 404, no route matched

- [ ] **Step 3: Add kill endpoint to `server.py`**

Add after the terminal REST endpoints section:

```python
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
```

- [ ] **Step 4: Run kill endpoint tests**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest tests/test_server.py::test_kill_session_success tests/test_server.py::test_kill_session_not_tracked -v`
Expected: Both pass.

- [ ] **Step 5: Add `_agent_node_to_dict` helper to `server.py`**

Add this function after `_event_to_dict` (around line 105):

```python
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
```

- [ ] **Step 6: Update imports at top of `server.py`**

Add `build_agent_trees` to the parser import:

```python
from claude_ops.parser import (
    AgentStatus,
    Session,
    SessionStatus,
    discover_sessions,
    extract_events,
    build_agent_trees,
)
from claude_ops.resources import get_process_resources
```

- [ ] **Step 7: Extend `_load_state` in `server.py`**

Make these specific modifications to the `_load_state` function:

**A) Change the `match_sessions_status` call (line ~125) to capture the return value:**
```python
# Before:
match_sessions_status(sessions, processes)
# After:
pid_map = match_sessions_status(sessions, processes)
```

**B) After the existing agent status loop (after line ~132), insert resource monitoring:**
```python
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
```

**C) Build a PID-to-cwd lookup so session dicts can include their PID:**
```python
    # Build cwd-to-PID lookup for session kill buttons
    pid_by_cwd: dict[str, int] = {}
    if processes:
        for proc in processes:
            resolved = os.path.realpath(proc.cwd)
            pid_by_cwd[resolved] = proc.pid
```

**D) After the existing event deduplication and `all_events` sorting (after line ~158), insert:**
```python
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
```

**E) In `_session_to_dict`, add PID to the result dict. After the `result = {` block (around line ~66), add:**
```python
    # Include PID for kill button (matched by cwd)
    if session.cwd:
        resolved_cwd = os.path.realpath(session.cwd)
        for proc in (find_claude_processes() or []):  # use cached processes
            if os.path.realpath(proc.cwd) == resolved_cwd:
                result["pid"] = proc.pid
                break
```

Actually, to avoid calling `find_claude_processes()` again inside `_session_to_dict`, pass `pid_by_cwd` as a parameter. Change the signature:

```python
def _session_to_dict(
    session: Session,
    matched_terminal_ids: set[str] | None = None,
    pid_by_cwd: dict[str, int] | None = None,
) -> dict[str, Any]:
```

And in the result dict, add:
```python
    if pid_by_cwd and session.cwd:
        resolved = os.path.realpath(session.cwd)
        if resolved in pid_by_cwd:
            result["pid"] = pid_by_cwd[resolved]
```

Update the call site in `_load_state` to pass `pid_by_cwd`:
```python
    d = _session_to_dict(s, matched_terminal_ids, pid_by_cwd)
```

**F) Add new fields to the return dict at the end of `_load_state`:**
```python
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
```

- [ ] **Step 6: Run all server tests**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest tests/test_server.py -v`
Expected: All pass. The existing `test_load_state_includes_terminals` test may need updating to account for new fields — add assertions for `resources`, `agent_trees`, `session_events`.

- [ ] **Step 7: Commit**

```bash
git add src/claude_ops/server.py tests/test_server.py
git commit -m "feat(server): add kill endpoint, agent trees, resources, session events"
```

---

## Chunk 3: Frontend — CSS Contrast & Motion System

### Task 5: CSS contrast fixes

**Files:**
- Modify: `src/claude_ops/static/lcars.css`

- [ ] **Step 1: Update CSS variables and value colours**

Add new variables to `:root`:
```css
--lcars-gold: #FFCC99;
--lcars-dark: #1A1A2E;
```

Update `.lcars-detail-value` and `.lcars-value` to use bright white:
```css
.lcars-detail-value {
  color: #FFFFFF;
}
.lcars-value {
  color: var(--lcars-gold);
}
```

Update `.lcars-detail-label` to use full-brightness accent instead of dim:
```css
.lcars-detail-label {
  color: var(--lcars-lavender);
}
```

Add status glow:
```css
.status-active { color: var(--lcars-green); animation: pulse 2s infinite; text-shadow: 0 0 6px var(--lcars-green); }
.status-idle   { color: var(--lcars-yellow); text-shadow: 0 0 4px var(--lcars-yellow); }
```

Update session meta and event summary to be more readable:
```css
.lcars-session-item .session-meta { color: var(--lcars-lavender); }
.lcars-event-row .event-summary { color: var(--lcars-text); }
```

- [ ] **Step 2: Verify visually**

Run: `cd /home/allan/code/neebone/claude-ops && python -m claude_ops --web --port 1701`
Open http://localhost:1701 and verify contrast improvements. Kill server after.

- [ ] **Step 3: Commit**

```bash
git add src/claude_ops/static/lcars.css
git commit -m "fix(lcars): improve text contrast and readability"
```

### Task 6: CSS motion system

**Files:**
- Modify: `src/claude_ops/static/lcars.css`

- [ ] **Step 1: Add motion system CSS**

Add to `lcars.css`:

```css
/* --------------------------------------------------------------------------
   Motion System
   -------------------------------------------------------------------------- */

/* Respect user preference */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

/* Data stream bar — animated line across panel headers */
.lcars-section-bar {
  position: relative;
  overflow: hidden;
}

.lcars-section-bar::after {
  content: '';
  position: absolute;
  top: 50%;
  left: -100%;
  width: 60%;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(0,0,0,0.3), transparent);
  animation: dataStream 3s linear infinite;
  animation-play-state: var(--stream-state, paused);
}

@keyframes dataStream {
  from { left: -60%; }
  to { left: 100%; }
}

/* Status transitions — smooth colour changes */
.status-active::before,
.status-idle::before,
.status-done::before {
  transition: color 300ms ease;
}

.lcars-session-item {
  transition: background 0.15s, border-left-color 300ms ease;
}

/* Content fade-in */
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.lcars-detail-row {
  animation: fadeIn 0.2s ease-out;
}

/* Activity event flash — brightness pulse on new rows */
@keyframes eventFlash {
  0% { background: rgba(240, 160, 122, 0.2); }
  100% { background: transparent; }
}

.lcars-event-row:not(.lcars-no-anim) {
  animation: slideInRight 0.3s ease-out, eventFlash 0.4s ease-out;
}

/* Scan-line overlay (off by default, toggled via JS class) */
.lcars-body.scanlines::after {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgba(0, 0, 0, 0.03) 2px,
    rgba(0, 0, 0, 0.03) 4px
  );
  pointer-events: none;
  z-index: 100;
}

.lcars-body {
  position: relative;
}
```

- [ ] **Step 2: Commit**

```bash
git add src/claude_ops/static/lcars.css
git commit -m "feat(lcars): add motion system — data streams, transitions, scan-lines"
```

### Task 7: Processing waveform visualisation

**Files:**
- Modify: `src/claude_ops/static/lcars.js`
- Modify: `src/claude_ops/static/lcars.css`
- Modify: `src/claude_ops/static/index.html`

- [ ] **Step 1: Add waveform canvas to HTML**

In `index.html`, replace the activity panel section with a split bottom:

```html
<div class="lcars-bottom-split">
  <div class="lcars-panel lcars-panel-activity">
    <div class="lcars-section-bar lcars-bg-blue">Activity</div>
    <div class="lcars-panel-content lcars-activity-scroll" id="activity-feed">
      <div class="lcars-empty">No activity</div>
    </div>
  </div>
  <div class="lcars-panel lcars-panel-waveform">
    <div class="lcars-section-bar lcars-bg-peach">Processing</div>
    <canvas id="waveform-canvas"></canvas>
  </div>
</div>
```

- [ ] **Step 2: Add waveform CSS**

```css
/* Bottom split: activity + waveform side by side */
.lcars-bottom-split {
  display: flex;
  flex-direction: row;
  gap: var(--gap);
  flex: 0 0 auto;
  min-height: 200px;
  max-height: 40vh;
}

.lcars-bottom-split .lcars-panel-activity {
  flex: 3;
  min-width: 0;
}

.lcars-panel-waveform {
  flex: 2;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.lcars-panel-waveform canvas {
  flex: 1;
  width: 100%;
  min-height: 0;
  background: var(--lcars-bg);
}
```

- [ ] **Step 3: Add waveform JS**

Add to `lcars.js` before the `init()` function:

```javascript
// ---------------------------------------------------------------------------
// Processing Waveform Visualisation
// ---------------------------------------------------------------------------

let waveformCanvas = null;
let waveformCtx = null;
let waveformAnimId = null;
let waveformData = { amplitude: 0, frequency: 1 };
let lastFrameTime = 0;
const TARGET_FRAME_MS = 1000 / 30; // 30fps

function initWaveform() {
  waveformCanvas = document.getElementById('waveform-canvas');
  if (!waveformCanvas) return;
  waveformCtx = waveformCanvas.getContext('2d');
  resizeWaveform();
  window.addEventListener('resize', resizeWaveform);
  waveformAnimId = requestAnimationFrame(drawWaveform);
}

function resizeWaveform() {
  if (!waveformCanvas) return;
  const rect = waveformCanvas.parentElement.getBoundingClientRect();
  waveformCanvas.width = rect.width;
  waveformCanvas.height = rect.height - 28; // subtract section bar
}

function updateWaveformData(state) {
  if (!state || !state.sessions) {
    waveformData.amplitude = 0;
    waveformData.frequency = 1;
    return;
  }
  const activeSessions = (state.sessions || []).filter(s => s.status === 'active');
  const totalTokens = activeSessions.reduce((sum, s) => {
    const tc = s.token_counts || {};
    return sum + (tc.input || 0) + (tc.output || 0);
  }, 0);
  // Normalise: amplitude 0-1 based on token count (log scale)
  waveformData.amplitude = totalTokens > 0 ? Math.min(1, Math.log10(totalTokens) / 7) : 0;
  waveformData.frequency = Math.max(1, activeSessions.length * 2);
}

function drawWaveform(timestamp) {
  waveformAnimId = requestAnimationFrame(drawWaveform);

  // Throttle to target FPS
  if (timestamp - lastFrameTime < TARGET_FRAME_MS) return;
  lastFrameTime = timestamp;

  const ctx = waveformCtx;
  const w = waveformCanvas.width;
  const h = waveformCanvas.height;
  if (!ctx || w === 0 || h === 0) return;

  ctx.clearRect(0, 0, w, h);

  const amp = waveformData.amplitude;
  const freq = waveformData.frequency;
  const midY = h / 2;
  const maxAmp = midY * 0.8;
  const t = timestamp / 1000;

  // Draw waveform
  const gradient = ctx.createLinearGradient(0, midY - maxAmp, 0, midY + maxAmp);
  gradient.addColorStop(0, '#F0A07A');
  gradient.addColorStop(0.5, '#FFCC99');
  gradient.addColorStop(1, '#F0A07A');

  ctx.strokeStyle = gradient;
  ctx.lineWidth = amp > 0.01 ? 2 : 1;
  ctx.globalAlpha = amp > 0.01 ? 0.8 : 0.3;

  ctx.beginPath();
  for (let x = 0; x < w; x++) {
    const xNorm = x / w * Math.PI * 2 * freq;
    // Composite wave: main + harmonic + noise
    const wave = Math.sin(xNorm + t * 2) * 0.6
      + Math.sin(xNorm * 2.3 + t * 3.1) * 0.25
      + Math.sin(xNorm * 5.7 + t * 1.7) * 0.15;
    const baseAmp = amp > 0.01 ? amp : 0.05; // idle hum
    const y = midY + wave * maxAmp * baseAmp;
    if (x === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.globalAlpha = 1;
}
```

- [ ] **Step 4: Wire up waveform in `init()` and `render()`**

In `init()`, add `initWaveform();` after `connect();`.

In `render()`, add `updateWaveformData(state);` after `renderActivityFeed`.

- [ ] **Step 5: Verify visually**

Run the web server, confirm waveform renders and responds to session activity.

- [ ] **Step 6: Commit**

```bash
git add src/claude_ops/static/lcars.js src/claude_ops/static/lcars.css src/claude_ops/static/index.html
git commit -m "feat(lcars): add processing waveform visualisation"
```

---

## Chunk 4: Frontend — Layout, Agent Tree, Resource Strip

### Task 8: HTML/CSS layout restructure

**Files:**
- Modify: `src/claude_ops/static/index.html`
- Modify: `src/claude_ops/static/lcars.css`

- [ ] **Step 1: Add resource monitor strip to HTML**

Insert between `main-top` and the divider in `index.html`:

```html
<div class="lcars-resource-strip" id="resource-strip"></div>
```

- [ ] **Step 2: Add resource strip CSS**

```css
/* --------------------------------------------------------------------------
   Resource Monitor Strip
   -------------------------------------------------------------------------- */
.lcars-resource-strip {
  display: flex;
  flex-direction: row;
  gap: 8px;
  padding: 4px 8px;
  flex-shrink: 0;
  min-height: 0;
  overflow: hidden;
}

.lcars-resource-strip:empty {
  display: none;
}

.lcars-resource-gauge {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 120px;
  max-width: 250px;
}

.lcars-resource-gauge .gauge-label {
  font-size: 10px;
  font-weight: 700;
  color: var(--lcars-lavender);
  text-transform: uppercase;
  letter-spacing: 1px;
  white-space: nowrap;
  min-width: 60px;
}

.lcars-resource-gauge .gauge-bar {
  flex: 1;
  height: 8px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 4px;
  overflow: hidden;
  min-width: 40px;
}

.lcars-resource-gauge .gauge-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 300ms ease, background 300ms ease;
}

.lcars-resource-gauge .gauge-value {
  font-family: 'Courier New', monospace;
  font-size: 10px;
  color: var(--lcars-gold);
  white-space: nowrap;
  min-width: 50px;
  text-align: right;
}
```

- [ ] **Step 3: Add active/completed session split CSS**

```css
/* Active/Completed session split */
.lcars-session-divider {
  display: flex;
  align-items: center;
  padding: 4px 12px;
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: var(--lcars-dim);
  gap: 6px;
  flex-shrink: 0;
}

.lcars-session-divider::before {
  content: '';
  flex: 1;
  height: 2px;
  background: var(--lcars-dim);
  opacity: 0.3;
}

.lcars-session-divider::after {
  content: '';
  flex: 1;
  height: 2px;
  background: var(--lcars-dim);
  opacity: 0.3;
}

.lcars-completed-zone {
  overflow: hidden;
  transition: max-height 300ms ease;
}

.lcars-completed-zone.collapsed {
  max-height: 0;
}

.lcars-completed-item {
  padding: 4px 12px;
  font-size: 11px;
  color: var(--lcars-dim);
  display: flex;
  justify-content: space-between;
  cursor: pointer;
  transition: background 0.15s;
}

.lcars-completed-item:hover {
  background: rgba(255, 255, 255, 0.04);
}
```

- [ ] **Step 4: Add header stat pills for total tokens and uptime**

In `index.html`, add after the cost pill:

```html
<span class="lcars-pill lcars-pill-peach" id="stat-tokens">0 tokens</span>
```

- [ ] **Step 5: Commit**

```bash
git add src/claude_ops/static/index.html src/claude_ops/static/lcars.css
git commit -m "feat(lcars): layout restructure — resource strip, session split, bottom split"
```

### Task 9: Frontend JS — resource strip, agent tree, session split, header stats

**Files:**
- Modify: `src/claude_ops/static/lcars.js`

- [ ] **Step 1: Add DOM references**

In `cacheDom()`:
```javascript
dom.resourceStrip = document.getElementById('resource-strip');
dom.statTokens = document.getElementById('stat-tokens');
dom.waveformCanvas = document.getElementById('waveform-canvas');
```

- [ ] **Step 2: Add `renderResourceStrip` function**

```javascript
function renderResourceStrip(resources) {
  if (!resources || Object.keys(resources).length === 0) {
    dom.resourceStrip.innerHTML = '';
    return;
  }

  dom.resourceStrip.innerHTML = Object.entries(resources).map(([pid, stats]) => {
    const cpuColor = stats.cpu_pct > 80 ? 'var(--lcars-red)'
      : stats.cpu_pct > 50 ? 'var(--lcars-yellow)'
      : 'var(--lcars-green)';
    const cpuWidth = Math.min(100, Math.max(2, stats.cpu_pct));
    return `
      <div class="lcars-resource-gauge">
        <span class="gauge-label">${truncate(stats.label || pid, 12)}</span>
        <div class="gauge-bar">
          <div class="gauge-fill" style="width: ${cpuWidth}%; background: ${cpuColor}"></div>
        </div>
        <span class="gauge-value">${stats.cpu_pct.toFixed(0)}% ${stats.rss_mb.toFixed(0)}MB</span>
      </div>
    `;
  }).join('');
}
```

- [ ] **Step 3: Add `renderAgentTree` function (replaces `renderAgents`)**

```javascript
function renderAgentTree(agentTrees, selectedId) {
  const nodes = agentTrees ? agentTrees[selectedId] : null;
  if (!nodes || nodes.length === 0) {
    dom.agentsPanel.innerHTML = '<div class="lcars-empty">NO AGENTS</div>';
    return;
  }

  function renderNode(node, depth) {
    const a = node.agent;
    const shortId = (a.id || '').slice(0, 8);
    const model = shortModelName(a.model);
    const tc = a.token_counts || {};
    const indent = depth * 16;
    const childrenHtml = (node.children || []).map(c => renderNode(c, depth + 1)).join('');

    return `
      <div class="lcars-agent-card" style="margin-left: ${indent}px; ${depth > 0 ? 'border-left-color: var(--lcars-blue);' : ''}">
        <div>
          <span class="status-${a.status}"></span>
          <strong>${shortId}</strong> &middot; ${model.toUpperCase()}
        </div>
        <div class="session-meta">${formatDuration(a.start_time)} &middot; ${formatTokens(tc.input)} IN / ${formatTokens(tc.output)} OUT &middot; ${formatCost(a.cost_usd)}</div>
        <div class="session-meta">${truncate(a.task_summary, 60)}</div>
      </div>
      ${childrenHtml}
    `;
  }

  dom.agentsPanel.innerHTML = nodes.map(n => renderNode(n, 0)).join('');
}
```

- [ ] **Step 4: Update `renderSessionList` for active/completed split**

Replace the inner logic of `renderSessionList` to split sessions into two groups. After the pending terminal auto-select logic, replace the rendering with:

```javascript
    // Split into active and completed
    const activeSessions = sessions.filter(s => s.status === 'active' || s.status === 'idle');
    const completedSessions = sessions.filter(s => s.status === 'done');

    // Auto-select first active session if none selected
    if (!selectedSessionId || !sessions.find(s => s.id === selectedSessionId)) {
      selectedSessionId = (activeSessions[0] || sessions[0]).id;
    }

    // Render active sessions
    let html = activeSessions.map(session => renderSessionCard(session)).join('');

    // Render completed section if any
    if (completedSessions.length > 0) {
      html += `
        <div class="lcars-session-divider" id="completed-divider">
          COMPLETED (${completedSessions.length})
        </div>
        <div class="lcars-completed-zone${completedCollapsed ? ' collapsed' : ''}" id="completed-zone">
          ${completedSessions.map(s => `
            <div class="lcars-completed-item" data-session-id="${s.id}">
              <span>${formatProject(s.project)}</span>
              <span>${formatCost(s.cost_usd)}</span>
            </div>
          `).join('')}
        </div>
      `;
    }

    dom.sessionList.innerHTML = html;

    // Divider click toggles collapse
    const divider = document.getElementById('completed-divider');
    if (divider) {
      divider.addEventListener('click', () => {
        completedCollapsed = !completedCollapsed;
        const zone = document.getElementById('completed-zone');
        if (zone) zone.classList.toggle('collapsed', completedCollapsed);
      });
    }

    // Click handlers for both active cards and completed items
    dom.sessionList.querySelectorAll('.lcars-session-item, .lcars-completed-item').forEach(el => {
      el.addEventListener('click', () => {
        sound.click();
        selectedSessionId = el.dataset.sessionId;
        render(currentState);
      });
    });
```

Extract the card rendering into a helper:

```javascript
function renderSessionCard(session) {
  const color = sessionColor(session.slug);
  const selected = session.id === selectedSessionId ? ' selected' : '';
  const agentCount = session.agents ? session.agents.length : 0;
  const agentLine = agentCount > 0
    ? `<div class="session-agents-summary">${agentCount} AGENT${agentCount > 1 ? 'S' : ''}</div>`
    : '';
  const lcarsBadge = session.terminal_id
    ? '<span class="lcars-badge">LCARS</span>'
    : '';

  return `
    <div class="lcars-session-item${selected}" data-session-id="${session.id}" style="border-left-color: ${color}">
      <div class="session-name">
        <span class="status-${session.status}" title="${session.status}"></span>
        ${formatProject(session.project)}
        ${lcarsBadge}
      </div>
      <div class="session-meta">${session.branch || '--'} &middot; ${formatDuration(session.start_time)} &middot; ${formatCost(session.cost_usd)}</div>
      ${agentLine}
    </div>
  `;
}
```

Add state variable at the top of the IIFE (near `let userScrolledUp`):

```javascript
let completedCollapsed = true;
```

- [ ] **Step 5: Update `renderStats` for total tokens**

```javascript
function renderStats(sessions, totalCost) {
  const active = sessions.filter(s => s.status === 'active').length;
  const idle = sessions.filter(s => s.status === 'idle').length;
  const agents = sessions.reduce((n, s) => n + (s.agents ? s.agents.length : 0), 0);
  const totalTokens = sessions.reduce((n, s) => {
    const tc = s.token_counts || {};
    return n + (tc.input || 0) + (tc.output || 0) + (tc.cache_read || 0) + (tc.cache_write || 0);
  }, 0);

  dom.statActive.textContent = `${active} active`;
  dom.statIdle.textContent = `${idle} idle`;
  dom.statAgents.textContent = `${agents} agents`;
  dom.statCost.textContent = formatCost(totalCost);
  if (dom.statTokens) dom.statTokens.textContent = formatTokens(totalTokens) + ' tokens';
}
```

- [ ] **Step 6: Update `render()` to call new functions**

```javascript
function render(state) {
  mergedSessions = mergeTerminalSessions(state.sessions || [], state.lcars_terminals || []);
  renderStats(mergedSessions, state.total_cost_usd || 0);
  renderSessionList(mergedSessions);
  renderSessionDetail(mergedSessions);
  renderAgentTree(state.agent_trees, selectedSessionId);
  renderResourceStrip(state.resources);
  renderActivityFeed(state.events || []);
  updatePanelLayout();
  updateWaveformData(state);

  // Activate data stream animation when data is flowing
  const body = document.querySelector('.lcars-body');
  const hasActive = mergedSessions.some(s => s.status === 'active');
  body.style.setProperty('--stream-state', hasActive ? 'running' : 'paused');
}
```

- [ ] **Step 7: Commit**

```bash
git add src/claude_ops/static/lcars.js
git commit -m "feat(lcars): resource strip, agent tree, session split, token stats"
```

---

## Chunk 5: Frontend — Session Actions, Keyboard Shortcuts & Terminal Tabs

### Task 10: Session card actions

**Files:**
- Modify: `src/claude_ops/static/lcars.js`
- Modify: `src/claude_ops/static/lcars.css`

- [ ] **Step 1: Add session action bar CSS**

```css
/* --------------------------------------------------------------------------
   Session Card Actions
   -------------------------------------------------------------------------- */
.lcars-session-item {
  position: relative;
  overflow: hidden;
}

.lcars-session-actions {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 8px;
  background: linear-gradient(90deg, transparent, rgba(0,0,0,0.8) 20%);
  transform: translateX(100%);
  transition: transform 150ms ease;
}

.lcars-session-item:hover .lcars-session-actions {
  transform: translateX(0);
}

.lcars-action-btn {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border: none;
  border-radius: 8px;
  font-family: 'Antonio', sans-serif;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1px;
  cursor: pointer;
  transition: filter 0.15s;
}

.lcars-action-btn:hover { filter: brightness(1.3); }
.lcars-action-btn.kill { background: var(--lcars-red); color: var(--lcars-dark); }
.lcars-action-btn.copy-path { background: var(--lcars-blue); color: var(--lcars-dark); }
.lcars-action-btn.copy-id { background: var(--lcars-lavender); color: var(--lcars-dark); }
```

- [ ] **Step 2: Add action buttons to session card rendering**

In `renderSessionList`, inside each session card's HTML, add:

```javascript
const actionsHtml = `
  <div class="lcars-session-actions">
    ${session.status !== 'done' ? `<button class="lcars-action-btn kill" data-action="kill" data-pid="${session.pid || ''}" title="Kill session">KILL</button>` : ''}
    <button class="lcars-action-btn copy-path" data-action="copy-path" data-value="${session.cwd || ''}" title="Copy working directory">PATH</button>
    <button class="lcars-action-btn copy-id" data-action="copy-id" data-value="${session.id}" title="Copy session ID">ID</button>
  </div>
`;
```

Add click handlers for action buttons after session list click handlers:

```javascript
dom.sessionList.querySelectorAll('.lcars-action-btn').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const action = btn.dataset.action;
    if (action === 'kill') {
      const pid = btn.dataset.pid;
      if (pid) {
        fetch(`/api/session/${pid}/kill`, { method: 'POST' })
          .then(r => r.json())
          .then(d => {
            showToast(d.status === 'ok' ? 'SESSION TERMINATED' : 'KILL FAILED');
            sound.sessionEnd();
          });
      }
    } else if (action === 'copy-path' || action === 'copy-id') {
      navigator.clipboard.writeText(btn.dataset.value).then(() => {
        showToast('COPIED TO CLIPBOARD');
        sound.click();
      });
    }
  });
});
```

Note: The kill button needs the PID. We need to include PID info in the session data from the backend. Add `pid_map` data to session dicts in `_session_to_dict` — or pass it via `resources` data (client can correlate by session slug/cwd). Simplest approach: include matched PIDs in the session dict from `_load_state`.

- [ ] **Step 3: Commit**

```bash
git add src/claude_ops/static/lcars.js src/claude_ops/static/lcars.css
git commit -m "feat(lcars): session card actions — kill, copy path, copy ID"
```

### Task 11: Keyboard shortcuts

**Files:**
- Modify: `src/claude_ops/static/lcars.js`
- Modify: `src/claude_ops/static/lcars.css`

- [ ] **Step 1: Add keyboard shortcut handler**

```javascript
function setupKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    // Don't handle shortcuts when typing in inputs or terminal is focused
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (activeTerminal && dom.panelTerminal.style.display !== 'none') return;

    const sessions = mergedSessions || [];

    switch (e.key) {
      case 'j':
      case 'ArrowDown': {
        e.preventDefault();
        const idx = sessions.findIndex(s => s.id === selectedSessionId);
        if (idx < sessions.length - 1) {
          selectedSessionId = sessions[idx + 1].id;
          sound.click();
          render(currentState);
        }
        break;
      }
      case 'k':
      case 'ArrowUp': {
        e.preventDefault();
        const idx = sessions.findIndex(s => s.id === selectedSessionId);
        if (idx > 0) {
          selectedSessionId = sessions[idx - 1].id;
          sound.click();
          render(currentState);
        }
        break;
      }
      case 'Enter': {
        // Select/expand current session
        updatePanelLayout();
        break;
      }
      case 't': {
        e.preventDefault();
        createNewSession();
        break;
      }
      case 'Escape': {
        selectedSessionId = null;
        render(currentState);
        break;
      }
      case '?': {
        e.preventDefault();
        toggleShortcutOverlay();
        break;
      }
    }
  });
}
```

- [ ] **Step 2: Add shortcut overlay**

```javascript
function toggleShortcutOverlay() {
  let overlay = document.getElementById('shortcut-overlay');
  if (overlay) {
    overlay.remove();
    return;
  }
  overlay = document.createElement('div');
  overlay.id = 'shortcut-overlay';
  overlay.className = 'lcars-shortcut-overlay';
  overlay.innerHTML = `
    <div class="lcars-shortcut-panel">
      <div class="lcars-section-bar lcars-bg-lavender">Keyboard Shortcuts</div>
      <div class="lcars-shortcut-list">
        <div><kbd>j</kbd> / <kbd>↓</kbd> — Next session</div>
        <div><kbd>k</kbd> / <kbd>↑</kbd> — Previous session</div>
        <div><kbd>Enter</kbd> — Select session</div>
        <div><kbd>t</kbd> — New terminal</div>
        <div><kbd>Esc</kbd> — Deselect</div>
        <div><kbd>?</kbd> — Toggle this overlay</div>
      </div>
    </div>
  `;
  overlay.addEventListener('click', () => overlay.remove());
  document.body.appendChild(overlay);
}
```

Add CSS:

```css
/* Keyboard shortcut overlay */
.lcars-shortcut-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}

.lcars-shortcut-panel {
  width: 320px;
  border: 2px solid var(--lcars-lavender);
  border-radius: 8px;
  overflow: hidden;
}

.lcars-shortcut-list {
  padding: 16px 20px;
  font-size: 13px;
  line-height: 2;
}

.lcars-shortcut-list kbd {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--lcars-lavender);
  color: var(--lcars-dark);
  font-family: 'Courier New', monospace;
  font-size: 12px;
  font-weight: 700;
  margin: 0 2px;
}
```

- [ ] **Step 3: Wire up in `init()`**

Add `setupKeyboardShortcuts();` in `init()`.

- [ ] **Step 4: Commit**

```bash
git add src/claude_ops/static/lcars.js src/claude_ops/static/lcars.css
git commit -m "feat(lcars): keyboard shortcuts and help overlay"
```

### Task 12: Terminal tabs

**Files:**
- Modify: `src/claude_ops/static/lcars.js`
- Modify: `src/claude_ops/static/lcars.css`

- [ ] **Step 1: Refactor terminal state from single to Map**

Replace:
```javascript
let activeTerminal = null;   // { id, ws, xterm, fitAddon }
```

With:
```javascript
let terminals = new Map();     // id -> { id, ws, xterm, fitAddon, container }
let activeTerminalId = null;
```

- [ ] **Step 2: Add terminal tabs HTML structure**

In `index.html`, update the terminal panel:

```html
<div class="lcars-panel lcars-panel-terminal" id="panel-terminal" style="display: none;">
  <div class="lcars-section-bar lcars-bg-ice" id="terminal-section-bar">
    <span class="lcars-section-label">Terminal</span>
    <div class="lcars-terminal-tabs" id="terminal-tabs"></div>
  </div>
  <div class="lcars-terminal-container" id="terminal-container"></div>
</div>
```

- [ ] **Step 3: Add terminal tabs CSS**

```css
.lcars-terminal-tabs {
  display: flex;
  gap: 2px;
  margin-left: 12px;
  align-items: center;
}

.lcars-terminal-tab {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 6px 6px 0 0;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1px;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.08);
  color: var(--lcars-text);
  transition: background 0.15s;
}

.lcars-terminal-tab.active {
  background: var(--lcars-ice);
  color: var(--lcars-dark);
}

.lcars-terminal-tab .tab-close {
  font-size: 12px;
  margin-left: 4px;
  opacity: 0.6;
  cursor: pointer;
}

.lcars-terminal-tab .tab-close:hover {
  opacity: 1;
}
```

- [ ] **Step 4: Rewrite `connectTerminal` for Map-based multi-terminal**

Replace the `connectTerminal` function with:

```javascript
function connectTerminal(terminalId, wasHidden) {
  // If already exists, just switch to it
  if (terminals.has(terminalId)) {
    switchToTerminal(terminalId);
    if (wasHidden) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const t = terminals.get(activeTerminalId);
          if (!t) return;
          if (t.xterm.clearTextureAtlas) t.xterm.clearTextureAtlas();
          t.fitAddon.fit();
          sendTerminalResize();
          t.xterm.refresh(0, t.xterm.rows - 1);
          t.xterm.focus();
        });
      });
    }
    return;
  }

  // Create a container div for this terminal
  const container = document.createElement('div');
  container.id = 'term-' + terminalId;
  container.style.cssText = 'flex:1;min-height:0;overflow:hidden;display:none;';
  dom.terminalContainer.appendChild(container);

  var xterm = new Terminal({
    theme: LCARS_THEME,
    fontFamily: "'Courier New', monospace",
    fontSize: 14,
    cursorBlink: true,
    allowProposedApi: true,
  });

  var fitAddon = new FitAddon.FitAddon();
  xterm.loadAddon(fitAddon);
  xterm.open(container);

  var wsUrl = 'ws://' + window.location.host + '/ws/terminal/' + terminalId;
  var termWs = new WebSocket(wsUrl);

  termWs.addEventListener('open', function () {
    var dims = fitAddon.proposeDimensions();
    if (dims) {
      termWs.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
    }
  });

  termWs.addEventListener('message', function (event) { xterm.write(event.data); });

  xterm.onData(function (data) {
    if (termWs.readyState === WebSocket.OPEN) termWs.send(data);
  });

  terminals.set(terminalId, { id: terminalId, ws: termWs, xterm: xterm, fitAddon: fitAddon, container: container });
  switchToTerminal(terminalId);
  window.addEventListener('resize', handleTerminalResize);
}

function switchToTerminal(terminalId) {
  // Hide all terminal containers, show selected
  terminals.forEach((t, id) => {
    t.container.style.display = id === terminalId ? 'flex' : 'none';
  });
  activeTerminalId = terminalId;
  const t = terminals.get(terminalId);
  if (t) {
    t.fitAddon.fit();
    t.xterm.focus();
  }
  renderTerminalTabs();
}
```

- [ ] **Step 5: Rewrite `disconnectTerminal` to close a specific terminal**

```javascript
function closeTerminal(terminalId) {
  const t = terminals.get(terminalId);
  if (!t) return;

  if (t.ws) try { t.ws.close(); } catch (_) {}
  if (t.xterm) t.xterm.dispose();
  if (t.container) t.container.remove();
  terminals.delete(terminalId);

  // If we closed the active one, switch to another or hide panel
  if (activeTerminalId === terminalId) {
    const remaining = Array.from(terminals.keys());
    if (remaining.length > 0) {
      switchToTerminal(remaining[0]);
    } else {
      activeTerminalId = null;
      window.removeEventListener('resize', handleTerminalResize);
    }
  }
  renderTerminalTabs();

  // Also delete from backend
  fetch('/api/terminal/' + terminalId, { method: 'DELETE' });
}
```

- [ ] **Step 6: Add `renderTerminalTabs` function**

```javascript
function renderTerminalTabs() {
  const tabsEl = document.getElementById('terminal-tabs');
  if (!tabsEl) return;

  let idx = 0;
  tabsEl.innerHTML = '';
  terminals.forEach((t, id) => {
    idx++;
    const isActive = id === activeTerminalId;
    const tab = document.createElement('div');
    tab.className = 'lcars-terminal-tab' + (isActive ? ' active' : '');
    tab.innerHTML = `#${idx} <span class="tab-close">&times;</span>`;
    tab.addEventListener('click', (e) => {
      if (e.target.classList.contains('tab-close')) {
        closeTerminal(id);
      } else {
        switchToTerminal(id);
        sound.click();
      }
    });
    tabsEl.appendChild(tab);
  });
}
```

- [ ] **Step 7: Update all references to old `activeTerminal` pattern**

Replace these references throughout `lcars.js`:

```javascript
// handleTerminalResize — change from:
function handleTerminalResize() {
  if (!activeTerminal || !activeTerminal.fitAddon) return;
  activeTerminal.fitAddon.fit();
  sendTerminalResize();
}
// To:
function handleTerminalResize() {
  const t = terminals.get(activeTerminalId);
  if (!t || !t.fitAddon) return;
  t.fitAddon.fit();
  sendTerminalResize();
}

// sendTerminalResize — change from activeTerminal to:
function sendTerminalResize() {
  const t = terminals.get(activeTerminalId);
  if (!t || !t.ws || !t.fitAddon) return;
  if (t.ws.readyState !== WebSocket.OPEN) return;
  var dims = t.fitAddon.proposeDimensions();
  if (dims) {
    t.ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
  }
}

// visibilitychange handler — change activeTerminal references to:
document.addEventListener('visibilitychange', function () {
  const t = terminals.get(activeTerminalId);
  if (!document.hidden && t && t.fitAddon) {
    setTimeout(function () {
      t.fitAddon.fit();
      sendTerminalResize();
      t.xterm.refresh(0, t.xterm.rows - 1);
    }, 100);
  }
});

// Remove old disconnectTerminal function entirely (replaced by closeTerminal)
// Remove old activeTerminal variable (replaced by terminals Map + activeTerminalId)
```

- [ ] **Step 6: Verify terminal tabs work**

Run web server, spawn 2+ terminals, verify tabs appear, switching works, close buttons work.

- [ ] **Step 7: Commit**

```bash
git add src/claude_ops/static/lcars.js src/claude_ops/static/lcars.css src/claude_ops/static/index.html
git commit -m "feat(lcars): terminal tabs for multi-terminal management"
```

### Task 13: Scan-line toggle & audio-visual sync

**Files:**
- Modify: `src/claude_ops/static/lcars.js`
- Modify: `src/claude_ops/static/index.html`

- [ ] **Step 1: Add scan-line toggle button to footer**

In `index.html`, add after the sound button:

```html
<button class="lcars-btn lcars-bg-purple" id="btn-scanlines" title="Toggle scan-lines">Scan: Off</button>
```

- [ ] **Step 2: Add scan-line toggle handler**

In `setupButtons()`:

```javascript
dom.btnScanlines = document.getElementById('btn-scanlines');
dom.btnScanlines.addEventListener('click', () => {
  sound.click();
  const body = document.querySelector('.lcars-body');
  body.classList.toggle('scanlines');
  dom.btnScanlines.textContent = body.classList.contains('scanlines') ? 'SCAN: ON' : 'SCAN: OFF';
});
```

- [ ] **Step 3: Extend `detectChanges` for activity event audio-visual sync**

In `detectChanges`, add chirp for new activity events (already handled by `eventFlash` CSS animation on new rows — just add a chirp when events grow):

```javascript
// New activity events
const prevEventCount = (prev.events || []).length;
const currEventCount = (curr.events || []).length;
if (currEventCount > prevEventCount) {
  lcarsChirp(660, 0.06, 0.08); // subtle chirp for new events
}
```

- [ ] **Step 4: Add dashboard uptime to footer clock area**

In `updateClock()`, also show uptime:

```javascript
const dashboardStartTime = Date.now();

function updateClock() {
  const now = new Date();
  dom.clock.textContent = now.toLocaleTimeString('en-GB', { hour12: false });

  // Dashboard uptime
  const uptimeEl = document.getElementById('dashboard-uptime');
  if (uptimeEl) {
    const elapsed = Date.now() - dashboardStartTime;
    const hrs = Math.floor(elapsed / 3600000);
    const mins = Math.floor((elapsed % 3600000) / 60000);
    const secs = Math.floor((elapsed % 60000) / 1000);
    uptimeEl.textContent = `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
}
```

Add to `index.html` footer, before the clock:

```html
<span class="lcars-clock" id="dashboard-uptime" title="Dashboard uptime">00:00:00</span>
```

- [ ] **Step 5: Commit**

```bash
git add src/claude_ops/static/lcars.js src/claude_ops/static/lcars.css src/claude_ops/static/index.html
git commit -m "feat(lcars): scan-line toggle, audio-visual sync, dashboard uptime"
```

### Task 14: Session event stream in detail panel

**Files:**
- Modify: `src/claude_ops/static/lcars.js`

- [ ] **Step 1: Add `renderSessionEvents` function**

```javascript
function renderSessionEvents(sessionEvents, selectedId) {
  const events = sessionEvents ? sessionEvents[selectedId] : null;
  if (!events || events.length === 0) return;

  // Append event stream section to session detail
  const detail = dom.sessionDetail;
  const streamHtml = `
    <div class="lcars-detail-divider" style="margin: 12px 0 8px; border-top: 1px solid rgba(255,255,255,0.08);"></div>
    <div style="font-size: 11px; color: var(--lcars-lavender); text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 6px;">Recent Activity</div>
    <div class="lcars-session-events">
      ${events.map(evt => `
        <div class="lcars-event-row lcars-no-anim" style="padding: 2px 0; font-size: 11px;">
          <span class="event-time">${formatTime(evt.timestamp)}</span>
          <span class="event-type" style="min-width: 60px;">${evt.event_type || '--'}</span>
          <span class="event-summary">${truncate(evt.summary, 60)}</span>
        </div>
      `).join('')}
    </div>
  `;
  detail.insertAdjacentHTML('beforeend', streamHtml);
}
```

- [ ] **Step 2: Wire up in `render()`**

After `renderSessionDetail(mergedSessions)`:
```javascript
renderSessionEvents(state.session_events, selectedSessionId);
```

- [ ] **Step 3: Commit**

```bash
git add src/claude_ops/static/lcars.js
git commit -m "feat(lcars): session event stream in detail panel"
```

---

### Task 15: Final integration verification

- [ ] **Step 1: Run all backend tests**

Run: `cd /home/allan/code/neebone/claude-ops && python -m pytest -v`
Expected: All tests pass.

- [ ] **Step 2: Start web server and verify all features**

Run: `cd /home/allan/code/neebone/claude-ops && python -m claude_ops --web --port 1701`
Open http://localhost:1701 and verify:
- Contrast improvements visible (bright white values, gold accents)
- Data stream bars animate on section headers when sessions active
- Processing waveform renders in bottom-right
- Session list splits into Active Stations / Completed
- Agent tree shows hierarchical view when session selected
- Resource strip shows CPU/memory gauges for active processes
- Session card hover reveals Kill/Path/ID action buttons
- Kill button sends request and shows toast
- Keyboard shortcuts work (j/k, t, Esc, ?)
- Terminal tabs appear when multiple terminals open
- Scan-line toggle works
- Dashboard uptime ticks in footer

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "fix: integration fixes from manual verification"
```

---

## Deferred

The following spec requirement is deferred to a follow-up:
- **Per-client event deduplication (spec 4.3):** "server tracks last-sent event index per client connection" — currently all events are sent each cycle. At 4-8 sessions this is not a performance concern. Can be added later if payload size becomes an issue.

---

## Parallelism Guide

Tasks that can run in parallel (independent of each other):

| Group | Tasks | Why independent |
|-------|-------|-----------------|
| A | Task 1, Task 2 | Both backend, different modules |
| B | Task 3, Task 5, Task 6 | Parser vs CSS, no overlap |
| C | Task 10, Task 11, Task 12 | Different JS sections, no shared state changes |

Sequential dependencies:
- Tasks 1-4 (backend) must complete before Tasks 8-9 (frontend rendering of backend data)
- Task 7 (waveform) needs the HTML layout from Task 8
- Tasks 10-14 can run after Tasks 8-9

Recommended execution order:
1. **Parallel**: Tasks 1+2 (backend resources) and Tasks 5+6 (CSS)
2. **Parallel**: Tasks 3+4 (backend trees/endpoints)
3. **Sequential**: Task 7 (waveform), Task 8 (layout), Task 9 (JS rendering)
4. **Parallel**: Tasks 10+11+12 (actions, keyboard, tabs)
5. **Sequential**: Tasks 13, 14 (polish)
