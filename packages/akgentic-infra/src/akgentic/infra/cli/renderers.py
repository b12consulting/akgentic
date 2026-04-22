"""Rich terminal rendering engine for chat events."""

from __future__ import annotations

import json

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


class RichRenderer:
    """Renders chat events with rich formatting, markdown, and color-coded agents."""

    _PALETTE: list[str] = ["cyan", "green", "magenta", "yellow", "blue", "red"]

    _MAX_WIDTH: int = 100

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console(
            highlight=False, width=min(self._MAX_WIDTH, Console().width)
        )
        self._agent_colors: dict[str, str] = {}
        self._color_idx: int = 0

    def _get_agent_color(self, agent_name: str) -> str:
        """Return a consistent color for a given agent name (round-robin assignment)."""
        if agent_name not in self._agent_colors:
            self._agent_colors[agent_name] = self._PALETTE[self._color_idx % len(self._PALETTE)]
            self._color_idx += 1
        return self._agent_colors[agent_name]

    def render_agent_message(self, sender: str, content: str) -> None:
        """Render an agent message with color-coded sender and markdown content."""
        color = self._get_agent_color(sender)
        escaped = sender.replace("[", "\\[")
        self._console.print(f"[bold {color}]\\[{escaped}][/bold {color}]")
        self._console.print(Markdown(content))

    def render_error(self, content: str) -> None:
        """Render an error message with red styling."""
        self._console.print(f"[bold red]\\[error][/bold red] {content}")

    def render_tool_call(
        self,
        tool_name: str,
        tool_input: str,
        tool_output: str | None = None,
    ) -> None:
        """Render a tool invocation as a panel with optional syntax highlighting."""
        # Try to pretty-print and syntax-highlight JSON input
        try:
            parsed = json.loads(tool_input)
            formatted_input = json.dumps(parsed, indent=2)
            input_renderable: Syntax | Text = Syntax(formatted_input, "json", theme="monokai")
        except (json.JSONDecodeError, TypeError):
            input_renderable = Text(tool_input)

        parts: list[Syntax | Text] = [Text("Input:")]
        parts.append(input_renderable)

        if tool_output is not None:
            parts.append(Text("\nOutput:"))
            parts.append(Text(tool_output))

        panel = Panel(
            Group(*parts),
            title=f"Tool: {tool_name}",
            border_style="dim",
        )
        self._console.print(panel)

    def render_human_input_request(self, prompt_text: str) -> None:
        """Render a highlighted human input prompt."""
        panel = Panel(
            prompt_text,
            title="Human Input Required",
            border_style="bold yellow",
        )
        self._console.print(panel)

    def render_history_separator(self) -> None:
        """Render a styled history separator."""
        self._console.rule("history", style="dim")

    def render_system_message(self, text: str) -> None:
        """Render system/status messages in dim style."""
        self._console.print(f"[dim]{text}[/dim]")

    # -- Layout borders --

    def render_border(self, style: str = "bright_black") -> None:
        """Render a horizontal border line."""
        self._console.rule(style=style)

    def render_status_bar(self, team_name: str, team_id: str, status: str) -> None:
        """Render the bottom status bar with team info and usage hints."""
        status_icon = "\u25b6" if status == "running" else "\u23f8"
        status_color = "green" if status == "running" else "yellow"
        short_id = team_id[:13] if len(team_id) > 13 else team_id
        left = (
            f"[bold cyan]{team_name}[/bold cyan]  "
            f"[dim]{short_id}[/dim]  "
            f"[{status_color}]{status_icon} {status}[/{status_color}]"
        )
        hints = "[dim]@mention  /command  /help  /quit[/dim]"
        self._console.print(f"  {left}  {hints}")

    # -- Startup / welcome screen --

    def render_welcome_header(self) -> None:
        """Render the welcome screen header."""
        self._console.print()
        self._console.print("[bold]  Akgentic Chat[/bold]")
        self._console.print()

    def render_team_list(
        self,
        teams: list[tuple[int, str, str, str]],
        *,
        title: str = "Running teams:",
    ) -> None:
        """Render a numbered list of teams. Each tuple: (number, name, short_id, status)."""
        self._console.print(f"  [bold]{title}[/bold]")
        for num, name, short_id, status in teams:
            status_icon = "\u25b6" if status == "running" else "\u23f8"
            status_color = "green" if status == "running" else "yellow"
            self._console.print(
                f"    [bold white]\\[{num}][/bold white] "
                f"{name:<20s} [dim]{short_id}[/dim]  "
                f"[{status_color}]{status_icon} {status}[/{status_color}]"
            )
        self._console.print()

    def render_catalog_list(self, entries: list[tuple[str, str]]) -> None:
        """Render catalog entries. Each tuple: (entry_id, description)."""
        self._console.print("  [bold]Create new:[/bold]")
        for entry_id, description in entries:
            desc = description or ""
            self._console.print(f"    [bold white]\\[c {entry_id}][/bold white]  [dim]{desc}[/dim]")
        self._console.print()

    def render_startup_hints(self, max_num: int, has_stopped: bool) -> None:
        """Render the startup menu hints in the status bar area."""
        parts = []
        if max_num > 0:
            parts.append(f"[1-{max_num}] connect")
        parts.append("[c <name>] create")
        if has_stopped:
            parts.append("[s] stopped teams")
        self._console.print(f"  [dim]{'  '.join(parts)}[/dim]")

    def render_connection_status(self, status: str) -> None:
        """Render connection state changes with appropriate styling."""
        styles = {
            "connected": ("green", "Connected"),
            "reconnecting": ("yellow", "Reconnecting..."),
            "disconnected": ("red", "Disconnected"),
        }
        color, label = styles.get(status, ("dim", status))
        self._console.print(f"[{color}]{label}[/{color}]")

    def render_pagination_hints(self, has_next: bool) -> None:
        """Render pagination hints for stopped teams list."""
        parts = ["[number] restore & connect"]
        if has_next:
            parts.append("[n] next page")
        parts.append("[b] back")
        self._console.print(f"  [dim]{'  '.join(parts)}[/dim]")
