# Claude Ops - Design Spec

**Goal:** A TUI dashboard for monitoring all active Claude Code sessions across terminals in real-time.

**Architecture:** Python + Textual app that reads Claude Code's JSONL session files from `~/.claude/projects/`, detects active processes, and presents a live ops board with session details, subagent trees, activity feeds, and aggregate stats.

**Tech Stack:** Python 3.10+, Textual (TUI framework), watchfiles (inotify-based file watching)

---

## Data Source

Claude Code writes conversation data to JSONL files at:

- Sessions: `~/.claude/projects/<project-slug>/<session-id>.jsonl`
- Subagents: `~/.claude/projects/<project-slug>/<session-id>/subagents/agent-<agent-id>.jsonl`

Each line is a JSON object with these key fields:

| Field | Description |
|-------|-------------|
| `type` | `user`, `assistant`, `system`, `progress`, `file-history-snapshot` |
| `sessionId` | UUID identifying the session |
| `slug` | Human-readable session name (e.g. "drifting-fluttering-pixel") |
| `cwd` | Working directory |
| `gitBranch` | Current git branch |
| `version` | Claude Code version |
| `timestamp` | ISO 8601 timestamp |
| `agentId` | Present on subagent messages only |
| `isSidechain` | `true` for subagent messages |
| `message` | Contains `role`, `content`, and for assistants: `model`, `usage` |

### Token usage (in assistant messages)

```json
{
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 567,
    "cache_creation_input_tokens": 890,
    "cache_read_input_tokens": 456
  }
}
```

### Tool use (in assistant message content blocks)

```json
{
  "type": "tool_use",
  "name": "Read",
  "id": "toolu_...",
  "input": { "file_path": "/path/to/file" }
}
```

### Turn duration (system messages)

```json
{
  "type": "system",
  "subtype": "turn_duration",
  "durationMs": 12345
}
```

## Data Models

### Session

| Field | Type | Source |
|-------|------|--------|
| id | str | `sessionId` |
| slug | str | `slug` |
| project | str | derived from file path |
| cwd | str | `cwd` from latest message |
| branch | str | `gitBranch` from latest message |
| version | str | `version` |
| start_time | datetime | first message timestamp |
| last_activity | datetime | latest message timestamp |
| status | enum | Active / Idle / Done (from process detection) |
| message_counts | dict | `{"user": int, "assistant": int}` |
| token_counts | dict | `{"input": int, "output": int, "cache_read": int, "cache_write": int}` |
| cost_usd | float | calculated from token counts + model pricing |
| agents | list[Agent] | parsed from subagent files |

### Agent

| Field | Type | Source |
|-------|------|--------|
| id | str | `agentId` |
| session_id | str | parent session ID |
| model | str | from first assistant message `model` field |
| task_summary | str | truncated first user message content |
| start_time | datetime | first message timestamp |
| last_activity | datetime | latest message timestamp |
| status | enum | Active / Idle (from activity recency) |
| token_counts | dict | `{"input": int, "output": int, "cache_read": int, "cache_write": int}` |
| cost_usd | float | calculated from token counts |

### ActivityEvent

| Field | Type | Source |
|-------|------|--------|
| timestamp | datetime | message timestamp |
| session_slug | str | which session |
| event_type | enum | ToolUse / Message / AgentSpawn |
| summary | str | human-readable one-liner |

Event detection:
- **ToolUse** - assistant message content block with `type: tool_use`. Summary shows tool name + truncated first input value (80 chars).
- **Message** - user messages (not `isMeta`). Summary shows truncated content (80 chars).
- **AgentSpawn** - first message in a subagent JSONL file. Summary shows agent ID + model.

## Status Detection

### Session status

- **Active** (green) - matching `claude` process found by cwd, and JSONL activity within last 30 seconds
- **Idle** (yellow) - process exists but no JSONL writes in last 30 seconds (waiting for user input). Display shows idle duration (e.g. "idle 3m").
- **Done** (grey) - no matching process

Process detection: parse `ps aux` for `claude` processes, match by cwd. If multiple processes match the same cwd, use the most recently started one. If `ps` fails, treat all sessions as status unknown (show `?`). Recheck every 5 seconds.

### Agent status

Inferred from JSONL activity only (agents don't have their own OS process):
- **Active** - last message within 30 seconds
- **Idle** - last message older than 30 seconds

## Cost Calculation

Calculated from token counts using per-model pricing:

| Model | Input/1M | Output/1M | Cache Read/1M | Cache Write/1M |
|-------|----------|-----------|---------------|----------------|
| opus | $15.00 | $75.00 | $1.50 | $18.75 |
| sonnet | $3.00 | $15.00 | $0.30 | $3.75 |
| haiku | $0.80 | $4.00 | $0.08 | $1.00 |

Cost is calculated per-message using that message's `model` field, then summed per session/agent. Model identified by substring matching on the model string (e.g. "opus" in "claude-opus-4-6").

## Layout

```
+-- Claude Ops ----------------------------------------------------------------+
| # active sessions   ^ agents   clock longest uptime   $ total cost           |
+----------------------------------+-------------------------------------------+
|  SESSIONS                        |  SESSION DETAIL                           |
|                                  |                                           |
|  * eval-pipeline                 |  eval-pipeline                            |
|    claude-worktrees/eval-pipe..  |  -----------------------------------      |
|    master . 47m . $0.82          |  Status: * Active                         |
|    +-- agent-ade4 exploring...   |  Branch: master                           |
|    +-- agent-a3d8 reading...     |  CWD: ~/work/app/.claude/worktrees/..    |
|    +-- agent-a49e idle           |  Uptime: 47m 12s                          |
|                                  |  Messages: 34 user . 31 assistant         |
|  * wealth-advisor                |  Tokens: 142k in . 28k out               |
|    ~/work/wealth-advisor         |  Cost: $0.82                              |
|    feat/goals . 12m . $0.44      |  Version: 2.1.68                          |
|                                  |                                           |
|  o app (idle 3m)                 |  AGENTS (3)                               |
|    ~/work/app                    |  -----------------------------------      |
|    master . 31m . $0.88          |  * agent-ade4 . haiku . 4m               |
|                                  |    "Explore the LLM-as-judge..."          |
|                                  |  * agent-a3d8 . haiku . 2m               |
|                                  |    "Read evaluators/judge.py..."          |
|                                  |  o agent-a49e . haiku . idle 1m          |
|                                  |    "Check test fixtures..."               |
+----------------------------------+-------------------------------------------+
|  ACTIVITY FEED                                                                |
|                                                                               |
|  12:47:03  eval-pipeline   ToolUse   Read(/home/allan/work/app/src/...)       |
|  12:47:01  eval-pipeline   Agent     Spawned agent-a49e (haiku)               |
|  12:46:58  wealth-advisor  ToolUse   Bash(npm test)                           |
|  12:46:55  eval-pipeline   ToolUse   Grep("evaluate" in evals/)              |
|  12:46:51  app             Message   User: "looks good, commit it"            |
|  12:46:48  wealth-advisor  ToolUse   Edit(src/goals/planner.ts:42)            |
+-------------------------------------------------------------------------------+
```

### Panel descriptions

**Header bar:** Aggregate stats - active session count, total agent count, longest running session uptime, total cost across all sessions. Updates reactively.

**Sessions panel (left):** Scrollable list of all sessions sorted by most recent activity. Each entry shows: project name (derived from path), shortened cwd, git branch, uptime, cost. Subagents shown inline as a tree beneath their parent. Color-coded status dots. Keyboard-navigable with arrow keys. Selected session highlighted.

**Session detail panel (right):** Detailed view of the currently selected session. Top section shows session metadata (status, branch, cwd, uptime, message counts, token counts, cost, version). Bottom section shows agent list with model, uptime/idle time, and task summary (truncated first user message).

**Activity feed (bottom):** Unified chronological feed across all sessions. Shows timestamp, session slug, event type, and summary. In-memory ring buffer of last 200 events. On startup, populated by scanning the most recent messages from all active session files. Auto-scrolls to newest. Color-coded by session for visual distinction.

### Navigation

- Up/Down arrows: navigate session list
- Activity feed auto-scrolls
- `q` to quit

## File Structure

```
claude-ops/
  pyproject.toml
  README.md
  src/
    claude_ops/
      __init__.py
      app.py          # Textual app, widgets, layout, keybindings
      parser.py       # JSONL parsing, data models, cost calculation
      watcher.py      # File watching (watchfiles), process detection
  tests/
    test_parser.py
    test_watcher.py
    fixtures/         # Sample JSONL files for testing
```

## Dependencies

- `textual>=3.0` - TUI framework
- `watchfiles>=1.0` - inotify-based file watching

No other runtime dependencies.

## Entry Point

`claude-ops` CLI command via pyproject.toml `[project.scripts]` entry. Launches the Textual app. No arguments required - discovers sessions automatically from `~/.claude/projects/`.

## Refresh Strategy

- JSONL file changes: detected via `watchfiles` inotify watcher on `~/.claude/projects/` tree, triggers re-parse of changed files only
- New sessions/projects: the watcher covers the entire `~/.claude/projects/` tree recursively, so new directories and files are picked up automatically
- Deleted/corrupted files: if a JSONL file disappears or fails to parse, remove the session from the UI gracefully (no crash)
- Process detection: every 5 seconds via `ps aux` parsing
- UI updates: reactive via Textual's message system, no polling

## Session Discovery

On startup, scan all `~/.claude/projects/` subdirectories for `.jsonl` files. Only load sessions with activity in the last 24 hours to avoid parsing old data. Subagent directories are discovered by checking for `<session-id>/subagents/` directories alongside session JSONL files.
