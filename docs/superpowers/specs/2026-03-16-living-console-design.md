# Claude Ops "Living Console" Design Spec

**Date:** 2026-03-16
**Status:** Draft
**Scope:** Visual polish, motion system, layout improvements, new data panels, session interactivity

## Context

Claude Ops is a monitoring dashboard for Claude Code sessions and agents, styled after Star Trek LCARS interfaces. It has dual TUI/web modes. This spec covers improvements to the **web mode only**.

The primary use case is **live ops monitoring** of 4-8 concurrent sessions/agents, with terminal hub as a strong secondary. Exited sessions should be visible but demoted.

### Current Pain Points

- Text contrast on dark backgrounds is poor in several places
- The interface feels static — lacks the motion and life of a real LCARS console
- Interactivity is limited beyond terminal spawning
- Agent relationships are flat (no parent-child tree)
- No system resource visibility (CPU/memory)
- Overall LCARS "feel" is close but not quite right

## Design

### 1. Visual & Motion System

#### 1.1 Contrast & Readability

- Data values (numbers, costs, token counts) render in bright white (`#FFFFFF`) or LCARS gold (`#FFCC99`)
- Labels on coloured LCARS bars use dark text (`#1A1A2E`) for readability
- Labels on black backgrounds use the bar's colour at full brightness
- Status indicators get a subtle text-shadow glow matching their colour (green/amber/red)
- Minimum contrast ratio target: 4.5:1 (WCAG AA)

#### 1.2 Motion System

All animations respect `prefers-reduced-motion`.

- **Data stream bars:** Thin (2px) horizontal lines that animate left-to-right across panel headers. Speed proportional to WebSocket message rate. Colour matches the panel's LCARS accent. Pause when no data flows.
- **Status transitions:** Session status changes (active/idle/done) animate via colour fade (300ms ease) on the status dot and card border. No instant colour swaps.
- **Content transitions:** New data in panels slides in (activity feed rows) or fades in (detail panel values). Duration: 200-300ms.
- **Scan-line overlay:** Optional subtle CSS scan-line effect on the main content area. Off by default, toggled via footer button. Implemented with repeating-linear-gradient.
- **Audio-visual sync:** When a new activity event arrives, the new row in the activity feed gets a brief brightness flash (CSS animation, 400ms). The existing chirp logic in `detectChanges()` fires on new sessions/agents/completions — extend it to also fire on new activity events, and synchronise the chirp with the row's flash animation.

#### 1.3 Processing Visualisation

A Canvas 2D animated waveform panel displaying real-time processing activity.

- **Data source:** Token throughput (tokens per second) and message rate from WebSocket updates
- **Rendering:** Oscilloscope-style waveform. Amplitude = token throughput, frequency = message rate
- **Idle state:** Gentle sine wave "hum" when sessions are active but quiet. Flatline when no sessions running.
- **Colour:** LCARS peach/gold gradient on black background
- **Position:** Bottom-right panel, sharing space with activity feed (activity 60%, viz 40%)
- **Performance:** requestAnimationFrame loop, throttled to 30fps. Canvas sized to container.
- **Fallback:** If Canvas performance is poor (frame time > 33ms consistently), automatically reduce to 15fps. The visualisation is decorative — degraded performance is acceptable.

### 2. Layout & Information Architecture

#### 2.1 Revised Layout Grid

```
+--------------------------------------------------+
| HEADER: stats pills + logo + total cost          |
+--------+---------------------+-------------------+
|SIDEBAR | SESSION LIST (250px)| DETAIL / TERMINAL  |
| LCARS  | +--Active Stations-+| + AGENT TREE       |
| blocks | | session cards    || (split right)      |
|        | +--Completed-------+|                    |
|        | | collapsed strip  ||                    |
+--------+---------------------+-------------------+
| RESOURCE MONITOR: CPU/mem gauges per process      |
+----------------------------------+---------------+
| ACTIVITY FEED (60%)             | WAVEFORM (40%) |
+----------------------------------+---------------+
```

#### 2.2 Active/Exited Session Split

- Session list divides into two zones separated by a thin LCARS divider bar
- **"Active Stations"** (top): Active and idle sessions, sorted by last activity descending
- **"Completed"** (bottom): Collapsed by default. Shows session name + cost in a compact single-line format. Click divider bar to expand/collapse.
- Exited sessions age out after 24h (existing backend behaviour)

#### 2.3 Agent Tree Panel

Replaces the current flat agent list on the right side of the detail area.

- Hierarchical view: parent session at root, child agents indented with connecting lines (CSS border-left + pseudo-elements)
- Each node displays: model badge (coloured pill), status dot, task summary (truncated to 60 chars), cost
- Collapsible: click a node to expand/collapse children
- Filtered: when a session is selected, shows only that session's agent tree. When none selected, shows all active trees.
- Connects to nested agent data from the backend (see section 4)

#### 2.4 Resource Monitor Strip

New horizontal panel between main content and bottom panels.

- One LCARS-style bar gauge per active Claude process
- PID-to-session mapping: `find_claude_processes()` returns PID + cwd; `match_sessions_status()` already maps cwd to sessions. Extend this to also return the PID-to-session mapping so the resource strip can label each gauge with the session slug. When multiple sessions share a cwd, label with the cwd basename instead.
- Each gauge shows: process label (session slug or cwd basename), CPU % fill bar, RSS memory value
- Bar fill colour: green (<50%), amber (50-80%), red (>80%) for CPU
- Memory shown as text value (e.g. "142 MB")
- Updates every 5 seconds
- Collapses to zero height when no active processes

#### 2.5 Header Stats Updates

Existing stats (active, idle, agents, cost) remain. Add:
- **Total tokens:** Compact format ("1.2M tokens")
- **Dashboard uptime:** Time since page load ("02:14:30")

### 3. Session Control & Interactivity

#### 3.1 Session Card Actions

On hover, a session card reveals a small action bar (slides in from right, 150ms):
- **Kill** (red pill button): Sends SIGTERM via `POST /api/session/{pid}/kill`. Shows confirmation toast. Button disabled for exited sessions.
- **Copy path** (blue pill): Copies session `cwd` to clipboard. Toast confirms.
- **Copy ID** (lavender pill): Copies session ID to clipboard. Toast confirms.

#### 3.2 Session Event Stream

The detail panel gains a **live event stream** sub-panel:
- Shows the last 20 tool calls and messages for the selected session
- Each row: timestamp, type icon (tool/message/agent-spawn), summary (truncated)
- Uses the existing `EventType` enum (`ToolUse`, `Message`, `AgentSpawn`) and `extract_events()` function
- Backend filters the existing event list by session ID and includes the last 20 per active session in `session_events`
- Updates in real-time from WebSocket payload
- Distinct from the global activity feed — filtered to one session
- Scrollable, auto-scrolls unless user has scrolled up

#### 3.3 Terminal Tabs

Currently `activeTerminal` is a single object in `lcars.js`. This requires refactoring to a `Map` of terminal instances keyed by terminal ID, with a separate `activeTerminalId` tracking which is displayed.

- Multiple open terminals displayed as tabs above the terminal panel
- Each tab shows: label ("LCARS Terminal #N" or session name), close button
- Click tab to switch: hides current xterm container, shows selected one
- Active tab highlighted with LCARS accent colour
- `t` keyboard shortcut spawns a new terminal
- Terminal xterm instances are kept alive when switching tabs (not destroyed/recreated)

#### 3.4 Keyboard Shortcuts

- `j` / `k` or arrow keys: Navigate session list
- `Enter`: Select/expand session
- `t`: Spawn new terminal
- `Esc`: Deselect session / close expanded panels
- `?`: Show keyboard shortcut overlay

### 4. Backend Changes

#### 4.1 Resource Monitoring (`resources.py` — new module)

```python
def get_process_resources(pids: list[int]) -> dict[int, ResourceStats]:
    """Read CPU% and RSS from /proc for given PIDs."""
```

- Reads `/proc/<pid>/stat` for CPU time (utime + stime), calculates % by comparing deltas between intervals
- Reads `/proc/<pid>/status` for VmRSS
- Returns `{pid: {"cpu_pct": float, "rss_mb": float}}`
- Graceful fallback: if `/proc` unavailable (macOS), returns empty dict
- Called on same 5-second interval as `find_claude_processes()`
- No new dependencies

#### 4.2 Agent Tree Construction (parser.py changes)

Parent-child relationships are determined by directory nesting:
- Agent JSONL files live at `<session-id>/subagents/agent-<id>.jsonl`
- Sub-agents of agents would be at `<session-id>/subagents/agent-<id>/subagents/agent-<child-id>.jsonl`
- The directory path encodes the hierarchy: each level of `/subagents/` nesting is one level in the tree
- If a flat structure is all that exists (no nested subagent dirs), the tree is one level deep: session → [agents]

New data structure:

```python
@dataclass
class AgentNode:
    agent: Agent
    children: list[AgentNode]
```

- `discover_sessions()` returns `agent_trees: dict[str, AgentNode]` mapping session IDs to their root agent nodes
- Tree built by walking the directory structure recursively, matching `subagents/` nesting
- Flat agent list still available for backward compatibility with TUI

#### 4.3 WebSocket Payload Extension

Current payload structure extended:

```json
{
  "sessions": [...],
  "agents": [...],
  "events": [...],
  "agent_trees": {"session_id": {"agent": {...}, "children": [...]}},
  "resources": {"12345": {"cpu_pct": 23.5, "rss_mb": 142}},
  "session_events": {"session_id": [{"timestamp": "...", "type": "...", "summary": "..."}]}
}
```

- `resources`: Per-PID CPU/memory, keyed by PID string
- `agent_trees`: Nested agent hierarchy per session
- `session_events`: Last 20 events per active session (for inspection panel), filtered from existing `extract_events()` output
- Efficiency: server tracks last-sent event index per client connection; only sends new events since last push. No client-to-server protocol change needed — the server maintains this state per WebSocket connection.

#### 4.4 Session Control Endpoint

```
POST /api/session/{pid}/kill
```

- Validates PID belongs to a tracked Claude process (cross-references `find_claude_processes()`)
- Sends `SIGTERM` to the process
- Returns `{"status": "ok"}` or `{"status": "error", "detail": "..."}`
- 404 if PID not tracked, 500 if signal fails

### 5. What's NOT In Scope

- No framework rewrite — vanilla JS stays
- No filtering/sorting UI for sessions
- No session history/archaeology features
- No new Python dependencies
- No TUI mode changes
- No macOS-specific resource monitoring (graceful degradation only)
