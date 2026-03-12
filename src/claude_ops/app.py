"""Claude Ops - TUI dashboard for monitoring Claude Code sessions."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Static

from claude_ops.parser import (
    Session, Agent, ActivityEvent, EventType, SessionStatus, AgentStatus,
    discover_sessions, extract_events,
)
from claude_ops.watcher import find_claude_processes, match_session_status

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
PROCESS_CHECK_INTERVAL = 5.0
MAX_ACTIVITY_EVENTS = 200


def format_duration(start: datetime, end: datetime | None = None) -> str:
    """Format a duration as human-readable string."""
    end = end or datetime.now(timezone.utc)
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "0s"
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    if minutes < 60:
        seconds = total_seconds % 60
        return f"{minutes}m {seconds}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def format_tokens(count: int) -> str:
    """Format token count with k/M suffix."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def format_cost(cost: float) -> str:
    """Format cost as dollar string."""
    return f"${cost:.2f}"


def status_dot(status: SessionStatus | AgentStatus) -> str:
    """Return a colored dot character for status."""
    if status in (SessionStatus.ACTIVE, AgentStatus.ACTIVE):
        return "[green]●[/]"
    elif status in (SessionStatus.IDLE, AgentStatus.IDLE):
        return "[yellow]●[/]"
    elif status == SessionStatus.DONE:
        return "[dim]●[/]"
    return "[dim]?[/]"


SESSION_COLORS = ["cyan", "magenta", "green", "yellow", "blue", "red"]


def session_color(slug: str) -> str:
    """Assign a consistent color to a session based on its slug."""
    idx = hash(slug) % len(SESSION_COLORS)
    return SESSION_COLORS[idx]


class HeaderBar(Static):
    """Top bar showing aggregate stats."""

    def render_stats(self, sessions: list[Session]) -> str:
        active = sum(1 for s in sessions if s.status == SessionStatus.ACTIVE)
        idle = sum(1 for s in sessions if s.status == SessionStatus.IDLE)
        total_agents = sum(len(s.agents) for s in sessions)
        total_cost = sum(s.cost_usd + sum(a.cost_usd for a in s.agents) for s in sessions)

        longest = ""
        if sessions:
            earliest = min(s.start_time for s in sessions)
            longest = format_duration(earliest)

        return (
            f" [green]●[/] {active} active  "
            f"[yellow]●[/] {idle} idle  "
            f"[bold]▲[/] {total_agents} agents  "
            f"[bold]⏱[/] {longest}  "
            f"[bold]$[/] {format_cost(total_cost)}"
        )


class SessionDetail(Static):
    """Right panel showing selected session details."""

    def render_session(self, session: Session | None) -> str:
        if session is None:
            return "[dim]No session selected[/]"

        s = session
        dot = status_dot(s.status)
        project_display = s.project.replace("-home-allan-", "").replace("-", "/")

        lines = [
            f"[bold]{project_display}[/]",
            "",
            f"  Status:   {dot} {s.status.value.title()}",
            f"  Branch:   {s.branch}",
            f"  CWD:      {s.cwd}",
            f"  Uptime:   {format_duration(s.start_time)}",
            f"  Messages: {s.message_counts.get('user', 0)} user · {s.message_counts.get('assistant', 0)} assistant",
            f"  Tokens:   {format_tokens(s.token_counts.get('input', 0))} in · {format_tokens(s.token_counts.get('output', 0))} out",
            f"  Cost:     {format_cost(s.cost_usd)}",
            f"  Version:  {s.version}",
        ]

        if s.agents:
            lines.append("")
            lines.append(f"  [bold]AGENTS ({len(s.agents)})[/]")
            lines.append(f"  {'─' * 35}")
            for agent in s.agents:
                agent_dot = status_dot(agent.status)
                model_short = agent.model.split("-")[1] if "-" in agent.model else agent.model
                if agent.status == AgentStatus.IDLE:
                    time_str = f"idle {format_duration(agent.last_activity)}"
                else:
                    time_str = format_duration(agent.start_time)
                lines.append(f"  {agent_dot} agent-{agent.id[:4]} · {model_short} · {time_str}")
                lines.append(f"    [dim]\"{agent.task_summary[:60]}\"[/]")

        return "\n".join(lines)


class ActivityFeed(Static):
    """Bottom panel showing unified activity feed."""

    def render_events(self, events: list[ActivityEvent]) -> str:
        if not events:
            return "[dim]No activity yet[/]"

        lines = []
        for event in events[-20:]:
            ts = event.timestamp.strftime("%H:%M:%S")
            color = session_color(event.session_slug)
            slug = event.session_slug[:15].ljust(15)
            etype = event.event_type.value.ljust(10)
            lines.append(f"  [dim]{ts}[/]  [{color}]{slug}[/]  [bold]{etype}[/]  {event.summary[:60]}")

        return "\n".join(lines)


class ClaudeOpsApp(App):
    """Main Claude Ops TUI application."""

    TITLE = "Claude Ops"
    CSS = """
    #header-bar {
        dock: top;
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    #main {
        height: 1fr;
    }
    #session-list {
        width: 1fr;
        border-right: solid $primary;
        padding: 0 1;
    }
    #session-detail {
        width: 1fr;
        padding: 0 1;
    }
    #activity-feed {
        dock: bottom;
        height: 12;
        border-top: solid $primary;
        padding: 0 1;
    }
    #activity-title {
        text-style: bold;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    sessions: reactive[list[Session]] = reactive(list, recompose=False)
    selected_index: reactive[int] = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self.activity_events: deque[ActivityEvent] = deque(maxlen=MAX_ACTIVITY_EVENTS)
        self._seen_event_keys: set[tuple] = set()

    def compose(self) -> ComposeResult:
        yield Static(id="header-bar")
        with Horizontal(id="main"):
            with VerticalScroll(id="session-list"):
                yield Static("[dim]Loading sessions...[/]", id="session-list-content", markup=True)
            with VerticalScroll(id="session-detail"):
                yield SessionDetail(id="detail-widget")
        with VerticalScroll(id="activity-feed"):
            yield Static("[bold]ACTIVITY FEED[/]", id="activity-title", markup=True)
            yield ActivityFeed(id="feed-widget")

    def on_mount(self) -> None:
        self.load_sessions()
        self.set_interval(PROCESS_CHECK_INTERVAL, self.refresh_statuses)
        self.watch_files()

    @work(thread=True)
    def watch_files(self) -> None:
        """Watch for JSONL file changes using watchfiles."""
        try:
            from watchfiles import watch
            for _changes in watch(str(CLAUDE_PROJECTS_DIR), recursive=True):
                self.call_from_thread(self.load_sessions)
        except ImportError:
            pass
        except Exception:
            pass

    def load_sessions(self) -> None:
        """Load all sessions and update the UI."""
        sessions = discover_sessions(CLAUDE_PROJECTS_DIR)
        claude_cwds = find_claude_processes()

        for s in sessions:
            s.status = match_session_status(s.cwd, s.last_activity, claude_cwds)
            now = datetime.now(timezone.utc)
            for agent in s.agents:
                if now - agent.last_activity > timedelta(seconds=30):
                    agent.status = AgentStatus.IDLE
                else:
                    agent.status = AgentStatus.ACTIVE

        # Collect new activity events
        for s in sessions:
            session_file = self._find_session_file(s)
            if session_file:
                self._add_events(extract_events(session_file, s.slug))
                agent_dir = session_file.parent / s.id / "subagents"
                if agent_dir.is_dir():
                    for agent_file in agent_dir.glob("agent-*.jsonl"):
                        self._add_events(extract_events(agent_file, s.slug, is_agent=True))

        sorted_events = sorted(self.activity_events, key=lambda e: e.timestamp)
        self.activity_events = deque(sorted_events, maxlen=MAX_ACTIVITY_EVENTS)

        self.sessions = sessions
        self.update_ui()

    def _add_events(self, events: list[ActivityEvent]) -> None:
        """Add events, deduplicating by key."""
        for event in events:
            key = (event.timestamp, event.session_slug, event.event_type, event.summary)
            if key not in self._seen_event_keys:
                self._seen_event_keys.add(key)
                self.activity_events.append(event)

    def _find_session_file(self, session: Session) -> Path | None:
        """Find the JSONL file for a session."""
        for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session.id}.jsonl"
            if candidate.exists():
                return candidate
        return None

    def refresh_statuses(self) -> None:
        """Refresh process-based status detection."""
        claude_cwds = find_claude_processes()
        now = datetime.now(timezone.utc)
        for s in self.sessions:
            s.status = match_session_status(s.cwd, s.last_activity, claude_cwds)
            for agent in s.agents:
                if now - agent.last_activity > timedelta(seconds=30):
                    agent.status = AgentStatus.IDLE
                else:
                    agent.status = AgentStatus.ACTIVE
        self.update_ui()

    def update_ui(self) -> None:
        """Update all UI widgets with current data."""
        # Header
        header = self.query_one("#header-bar", Static)
        header_bar = HeaderBar()
        header.update(header_bar.render_stats(self.sessions))

        # Session list
        session_list = self.query_one("#session-list-content", Static)
        if not self.sessions:
            session_list.update("[dim]No active sessions found[/]")
        else:
            lines = []
            for i, s in enumerate(self.sessions):
                dot = status_dot(s.status)
                idle_text = ""
                if s.status == SessionStatus.IDLE:
                    idle_text = f" [yellow](idle {format_duration(s.last_activity)})[/]"

                project_display = s.project.replace("-home-allan-", "").replace("-", "/")
                selected = ">> " if i == self.selected_index else "   "
                lines.append(
                    f"{selected}{dot} [bold]{project_display}[/]{idle_text}\n"
                    f"     [dim]{s.cwd}[/]\n"
                    f"     [dim]{s.branch} · {format_duration(s.start_time)} · {format_cost(s.cost_usd)}[/]"
                )
                for agent in s.agents:
                    agent_dot = status_dot(agent.status)
                    if agent.status == AgentStatus.IDLE:
                        agent_time = f"idle {format_duration(agent.last_activity)}"
                    else:
                        agent_time = format_duration(agent.start_time)
                    lines.append(f"     ├─ {agent_dot} [dim]agent-{agent.id[:4]} {agent_time}[/]")
                lines.append("")
            session_list.update("\n".join(lines))

        # Detail
        detail = self.query_one("#detail-widget", SessionDetail)
        selected = self.sessions[self.selected_index] if self.sessions and self.selected_index < len(self.sessions) else None
        detail.update(detail.render_session(selected))

        # Activity feed
        feed = self.query_one("#feed-widget", ActivityFeed)
        feed.update(feed.render_events(list(self.activity_events)))

        # Auto-scroll activity feed to bottom
        feed_scroll = self.query_one("#activity-feed", VerticalScroll)
        feed_scroll.scroll_end(animate=False)

    def action_refresh(self) -> None:
        self.load_sessions()

    def on_key(self, event) -> None:
        if event.key == "up" and self.selected_index > 0:
            self.selected_index -= 1
            self.update_ui()
        elif event.key == "down" and self.selected_index < len(self.sessions) - 1:
            self.selected_index += 1
            self.update_ui()


def main() -> None:
    """Entry point for claude-ops CLI."""
    import sys

    if "--web" in sys.argv:
        try:
            from claude_ops.server import start_web_server
        except ImportError:
            print(
                "Web dependencies not installed. "
                "Install with: pip install claude-ops[web]"
            )
            sys.exit(1)
        port = 1701
        # Check for --port flag
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
        start_web_server(port=port)
    else:
        app = ClaudeOpsApp()
        app.run()


if __name__ == "__main__":
    main()
