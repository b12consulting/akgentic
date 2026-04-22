"""Team selection and creation flow for the REPL startup menu."""

from __future__ import annotations

from akgentic.infra.cli.client import ApiClient, ApiError, TeamInfo
from akgentic.infra.cli.renderers import RichRenderer

_STOPPED_PAGE_SIZE = 5


def _short_id(team_id: str) -> str:
    """Truncate a UUID to the first 13 characters."""
    return team_id[:13] if len(team_id) > 13 else team_id


class TeamSelector:
    """Interactive team selection / creation flow before entering chat."""

    def __init__(self, client: ApiClient, renderer: RichRenderer) -> None:
        self._client = client
        self._renderer = renderer

    def run(self) -> str | None:
        """Show the startup menu and return the selected/created team_id, or None to quit."""
        while True:
            teams = self._fetch_teams()
            running = [t for t in teams if t.status == "running"]
            stopped = [t for t in teams if t.status == "stopped"]

            self._render_menu(running)
            choice = input("\n  > ").strip()

            result = self._handle_choice(choice, running, stopped)
            if result is not None:
                return result if result != "" else None

    def _render_menu(self, running: list[TeamInfo]) -> None:
        """Display the welcome screen with running teams and catalog."""
        catalog = self._fetch_catalog()
        self._renderer.render_border()
        self._renderer.render_welcome_header()
        if running:
            numbered = [
                (i + 1, t.name, _short_id(t.team_id), t.status) for i, t in enumerate(running)
            ]
            self._renderer.render_team_list(numbered, title="Running teams:")
        if catalog:
            self._renderer.render_catalog_list(catalog)
        self._renderer.render_border()
        self._renderer.render_startup_hints(len(running), has_stopped=True)

    def _handle_choice(
        self,
        choice: str,
        running: list[TeamInfo],
        stopped: list[TeamInfo],
    ) -> str | None:
        """Process a user choice. Returns team_id, empty string for quit, or None to loop."""
        if not choice or choice in ("/quit", "q"):
            return ""  # signal quit

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(running):
                return running[idx].team_id
            self._renderer.render_error(f"Invalid selection: {choice}")
            return None

        if choice.startswith("c "):
            return self._handle_create(choice[2:].strip())

        if choice == "s":
            return self._browse_stopped(stopped)

        self._renderer.render_error(f"Unknown choice: {choice}")
        return None

    def _handle_create(self, entry_id: str) -> str | None:
        """Create a team from a catalog entry. Returns team_id or None on failure."""
        if not entry_id:
            self._renderer.render_error("Usage: c <catalog_entry>")
            return None
        try:
            team = self._client.create_team(entry_id)
            return team.team_id
        except ApiError:
            self._renderer.render_error(f"Failed to create team from '{entry_id}'")
            return None

    def _fetch_teams(self) -> list[TeamInfo]:
        """Fetch all teams from the server."""
        try:
            return self._client.list_teams()
        except ApiError:
            return []

    def _fetch_catalog(self) -> list[tuple[str, str]]:
        """Fetch catalog entries as (id, description) tuples."""
        try:
            entries = self._client.list_catalog_teams()
            return [(e.id, e.description) for e in entries]
        except Exception:  # noqa: BLE001
            return []

    def _browse_stopped(self, stopped: list[TeamInfo]) -> str | None:
        """Paginated browser for stopped teams. Returns team_id or None to go back."""
        if not stopped:
            self._renderer.render_system_message("No stopped teams.")
            return None

        page = 0
        while True:
            start = page * _STOPPED_PAGE_SIZE
            page_teams = stopped[start : start + _STOPPED_PAGE_SIZE]
            if not page_teams:
                page = 0
                continue

            has_next = start + _STOPPED_PAGE_SIZE < len(stopped)

            self._renderer.render_border()
            numbered = [
                (i + 1, t.name, _short_id(t.team_id), t.status) for i, t in enumerate(page_teams)
            ]
            title = f"Stopped teams ({len(stopped)} total):"
            self._renderer.render_team_list(numbered, title=title)
            self._renderer.render_border()
            self._renderer.render_pagination_hints(has_next)

            choice = input("\n  > ").strip()

            if not choice or choice == "b":
                return None

            if choice == "n" and has_next:
                page += 1
                continue

            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(page_teams):
                    team = page_teams[idx]
                    try:
                        self._client.restore_team(team.team_id)
                        return team.team_id
                    except ApiError:
                        self._renderer.render_error(f"Failed to restore team {team.name}")
                        continue
                self._renderer.render_error(f"Invalid selection: {choice}")
                continue

            self._renderer.render_error(f"Unknown choice: {choice}")
