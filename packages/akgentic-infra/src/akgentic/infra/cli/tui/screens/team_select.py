"""Full-screen team selection menu with pagination."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.events import Key
from textual.screen import Screen
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from akgentic.infra.cli.client import ApiClient, CatalogTeamInfo, TeamInfo

_log = logging.getLogger(__name__)

PAGE_SIZE = 5

_CSS_PATH = Path(__file__).parent.parent / "styles" / "team_select.tcss"


def _short_id(team_id: str) -> str:
    """Return team ID for display."""
    return team_id


class TeamSelectScreen(Screen[str | None]):
    """Full-screen team selection with pagination for running/stopped teams."""

    CSS_PATH = _CSS_PATH

    def __init__(self, client: ApiClient | None = None) -> None:
        super().__init__()
        self._client = client
        self._page: int = 0
        self._running_teams: list[TeamInfo] = []
        self._stopped_teams: list[TeamInfo] = []
        self._catalog: list[CatalogTeamInfo] = []

    def compose(self) -> ComposeResult:
        """Compose the team selection layout."""
        from textual.containers import Container

        header = Text()
        header.append("Akgentic -- Team Selection", style="bold")
        header.append("   \u25cf connecting", style="yellow")
        yield Static(header, id="team-header")
        yield VerticalScroll(id="team-list")
        with Container(id="team-input-area"):
            yield Input(placeholder="> ", id="team-input")
            yield Static("", id="team-hints")

    def on_mount(self) -> None:
        """Fetch data, render, and focus the input."""
        self._fetch_data_worker()
        self.query_one("#team-input", Input).focus()

    @work(thread=True)
    def _fetch_data_worker(self) -> None:
        """Fetch teams and catalog data in a background thread."""
        running, stopped, catalog = self._fetch_data()
        self.app.call_from_thread(self._on_data_loaded, running, stopped, catalog)

    def _fetch_data(
        self,
    ) -> tuple[list[TeamInfo], list[TeamInfo], list[CatalogTeamInfo]]:
        """Call API to get teams and catalog. Returns (running, stopped, catalog)."""
        from akgentic.infra.cli.client import ApiError

        running: list[TeamInfo] = []
        stopped: list[TeamInfo] = []
        catalog: list[CatalogTeamInfo] = []
        if self._client is None:
            return running, stopped, catalog
        try:
            teams = self._client.list_teams()
            running = [t for t in teams if t.status == "running"]
            stopped = [t for t in teams if t.status == "stopped"]
        except ApiError:
            _log.debug("Failed to fetch teams")
        try:
            catalog = self._client.list_catalog_teams()
        except ApiError:
            _log.debug("Failed to fetch catalog")
        return running, stopped, catalog

    def _on_data_loaded(
        self,
        running: list[TeamInfo],
        stopped: list[TeamInfo],
        catalog: list[CatalogTeamInfo],
    ) -> None:
        """Store fetched data and render."""
        self._running_teams = sorted(running, key=lambda t: t.created_at, reverse=True)
        self._stopped_teams = sorted(stopped, key=lambda t: t.created_at, reverse=True)
        self._catalog = catalog
        self._update_header(bool(running or stopped or catalog))
        self._render_page()

    def _update_header(self, server_reachable: bool) -> None:
        """Update the header with connection indicator."""
        header = Text()
        header.append("Akgentic -- Team Selection", style="bold")
        if server_reachable:
            header.append("   \u25cf connected", style="green")
        else:
            header.append("   \u2716 disconnected", style="red")
        try:
            self.query_one("#team-header", Static).update(header)
        except LookupError:
            pass

    def _max_pages(self) -> int:
        """Return total number of pages based on running and stopped lists."""
        max_items = max(len(self._running_teams), len(self._stopped_teams))
        if max_items == 0:
            return 1
        return math.ceil(max_items / PAGE_SIZE)

    def _render_page(self) -> None:
        """Clear and re-render the team list for the current page."""
        container = self.query_one("#team-list", VerticalScroll)
        container.remove_children()

        start = self._page * PAGE_SIZE
        end = start + PAGE_SIZE

        self._render_running_section(container, start, end)
        container.mount(Static(""))
        self._render_stopped_section(container, start, end)
        container.mount(Static(""))
        self._render_catalog_section(container)
        self._update_hints()

    def _render_running_section(self, container: VerticalScroll, start: int, end: int) -> None:
        """Render the running teams section."""
        if not self._running_teams:
            container.mount(Static("Running teams: (none)", classes="section-header"))
            return
        if len(self._running_teams) <= PAGE_SIZE:
            page = self._running_teams
            label_start = 0
            header = f"Running teams ({len(self._running_teams)}):"
        else:
            page = self._running_teams[start:end]
            label_start = start
            r_end = min(start + len(page), len(self._running_teams))
            header = f"Running teams ({start + 1}-{r_end} of {len(self._running_teams)}):"
        container.mount(Static(header, classes="section-header"))
        for i, team in enumerate(page):
            line = Text()
            line.append(f"  [{label_start + i + 1}]", style="bold cyan")
            line.append(f"  {team.name}", style="bold")
            line.append(f"  {_short_id(team.team_id)}", style="dim")
            line.append(f"  {team.created_at[:16]}", style="dim")
            line.append("  > running", style="green")
            container.mount(Static(line, classes="team-entry"))

    def _render_stopped_section(self, container: VerticalScroll, start: int, end: int) -> None:
        """Render the stopped teams section."""
        if not self._stopped_teams:
            container.mount(Static("Stopped teams: (none)", classes="section-header"))
            return
        if len(self._stopped_teams) <= PAGE_SIZE:
            page = self._stopped_teams
            label_start = 0
            header = f"Stopped teams ({len(self._stopped_teams)}):"
        else:
            page = self._stopped_teams[start:end]
            label_start = start
            s_end = min(start + len(page), len(self._stopped_teams))
            header = f"Stopped teams ({start + 1}-{s_end} of {len(self._stopped_teams)}):"
        container.mount(Static(header, classes="section-header"))
        for i, team in enumerate(page):
            line = Text()
            line.append(f"  [s{label_start + i + 1}]", style="bold yellow")
            line.append(f"  {team.name}", style="bold")
            line.append(f"  {_short_id(team.team_id)}", style="dim")
            line.append(f"  {team.created_at[:16]}", style="dim")
            line.append("  || stopped", style="yellow")
            container.mount(Static(line, classes="team-entry"))

    def _render_catalog_section(self, container: VerticalScroll) -> None:
        """Render the catalog entries section."""
        if not self._catalog:
            container.mount(Static("Create new: (none)", classes="section-header"))
            return
        container.mount(Static("Create new:", classes="section-header"))
        for entry in self._catalog:
            line = Text()
            line.append(f"  [c {entry.id}]", style="bold magenta")
            line.append(f"  {entry.description}", style="dim")
            container.mount(Static(line, classes="team-entry"))

    def _update_hints(self) -> None:
        """Update the hint bar based on current page state."""
        hints_parts: list[str] = []
        if self._running_teams:
            if len(self._running_teams) <= PAGE_SIZE:
                hints_parts.append(f"[1-{len(self._running_teams)}] connect")
            else:
                start = self._page * PAGE_SIZE + 1
                end = min(start + PAGE_SIZE - 1, len(self._running_teams))
                hints_parts.append(f"[{start}-{end}] connect")
        if self._stopped_teams:
            if len(self._stopped_teams) <= PAGE_SIZE:
                hints_parts.append(f"[s1-s{len(self._stopped_teams)}] restore")
            else:
                start = self._page * PAGE_SIZE + 1
                end = min(start + PAGE_SIZE - 1, len(self._stopped_teams))
                hints_parts.append(f"[s{start}-s{end}] restore")
        if self._catalog:
            hints_parts.append("[c <name>] create")
        if self._max_pages() > 1:
            hints_parts.append("<-> page")
        hints_parts.append("Esc back  [q] quit")
        hint_text = "  ".join(hints_parts)
        try:
            # Use Text() to avoid Rich markup interpretation of brackets
            self.query_one("#team-hints", Static).update(Text(hint_text, style="dim"))
        except LookupError:
            _log.debug("team-hints widget not available for update")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input from the Input widget."""
        text = event.value.strip()
        event.input.value = ""

        if not text:
            return

        if text == "q":
            self.dismiss("__quit__")
            return

        if text == "n":
            self._next_page()
            return

        if text == "p":
            self._prev_page()
            return

        if text.isdigit():
            self._select_running(int(text))
            return

        if text.startswith("s") and text[1:].isdigit():
            self._select_stopped(int(text[1:]))
            return

        if text.startswith("c "):
            name = text[2:].strip()
            if name:
                self._create_team(name)
                return

        self._show_error(f"Unknown command: {text}")

    def on_key(self, event: Key) -> None:
        """Handle key presses for pagination and quit."""
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "right":
            self._next_page()
        elif event.key == "left":
            self._prev_page()

    def _next_page(self) -> None:
        """Advance to the next page if available."""
        if self._page < self._max_pages() - 1:
            self._page += 1
            self._render_page()

    def _prev_page(self) -> None:
        """Go back one page if not on the first page."""
        if self._page > 0:
            self._page -= 1
            self._render_page()

    def _select_running(self, number: int) -> None:
        """Select a running team by global index."""
        idx = number - 1
        if 0 <= idx < len(self._running_teams):
            self.dismiss(self._running_teams[idx].team_id)
        else:
            self._show_error(f"Invalid selection: {number}")

    def _select_stopped(self, number: int) -> None:
        """Select a stopped team by global index, restore it."""
        idx = number - 1
        if 0 <= idx < len(self._stopped_teams):
            team = self._stopped_teams[idx]
            self._restore_team_worker(team.team_id)
        else:
            self._show_error(f"Invalid selection: s{number}")

    @work(thread=True)
    def _restore_team_worker(self, team_id: str) -> None:
        """Restore a stopped team in a background thread."""
        from akgentic.infra.cli.client import ApiError

        if self._client is None:
            return
        try:
            self._client.restore_team(team_id)
            self.app.call_from_thread(self.dismiss, team_id)
        except ApiError as exc:
            self.app.call_from_thread(self._show_error, f"Failed to restore team: {exc}")

    def _create_team(self, catalog_entry_id: str) -> None:
        """Create a team from a catalog entry."""
        self._create_team_worker(catalog_entry_id)

    @work(thread=True)
    def _create_team_worker(self, catalog_entry_id: str) -> None:
        """Create a team in a background thread."""
        from akgentic.infra.cli.client import ApiError

        if self._client is None:
            return
        try:
            team = self._client.create_team(catalog_entry_id)
            self.app.call_from_thread(self.dismiss, team.team_id)
        except ApiError as exc:
            self.app.call_from_thread(self._show_error, f"Failed to create team: {exc}")

    def _show_error(self, message: str) -> None:
        """Display an error message in the team list area, replacing any previous error."""
        container = self.query_one("#team-list", VerticalScroll)
        for old in container.query(".error-message"):
            old.remove()
        error_widget = Static(
            Text(message, style="bold red"),
            classes="error-message",
        )
        container.mount(error_widget)
