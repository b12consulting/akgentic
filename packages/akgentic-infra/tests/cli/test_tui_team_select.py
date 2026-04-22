"""Pilot tests for TeamSelectScreen and ChatApp integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from textual.widgets import Input

from akgentic.infra.cli.client import ApiClient, ApiError, CatalogTeamInfo, TeamInfo
from akgentic.infra.cli.tui.app import ChatApp
from akgentic.infra.cli.tui.screens.team_select import TeamSelectScreen


def _make_team(
    team_id: str, name: str, status: str = "running"
) -> TeamInfo:
    return TeamInfo(
        team_id=team_id,
        name=name,
        status=status,
        user_id="u1",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )


def _mock_client(
    running: int = 3, stopped: int = 2, catalog: int = 2
) -> MagicMock:
    """Create a mock ApiClient with configurable team counts."""
    client = MagicMock(spec=ApiClient)
    teams = [
        _make_team(f"run-{i}", f"team-{i}", "running")
        for i in range(1, running + 1)
    ] + [
        _make_team(f"stop-{i}", f"stopped-{i}", "stopped")
        for i in range(1, stopped + 1)
    ]
    client.list_teams.return_value = teams
    client.list_catalog_teams.return_value = [
        CatalogTeamInfo(id=f"cat-{i}", name=f"catalog-{i}", description=f"Desc {i}")
        for i in range(1, catalog + 1)
    ]
    return client


def _get_team_list_text(app: ChatApp) -> str:
    """Extract all rendered text from the team-list container."""
    children = app.screen.query_one("#team-list").children
    return " ".join(str(c.render()) for c in children)


async def _submit_input(app: ChatApp, text: str, pilot: object) -> None:
    """Set input value and post a Submitted message."""
    inp = app.screen.query_one("#team-input", Input)
    inp.value = text
    app.screen.post_message(Input.Submitted(inp, text))
    await pilot.pause()  # type: ignore[union-attr]


# -- Task 6: Basic rendering and selection tests --


@pytest.mark.asyncio
async def test_screen_renders_teams_and_catalog() -> None:
    """Screen renders running teams, stopped teams, and catalog entries."""
    client = _mock_client(running=2, stopped=1, catalog=1)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TeamSelectScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()

        all_text = _get_team_list_text(app)
        assert "team-1" in all_text
        assert "team-2" in all_text
        assert "stopped-1" in all_text
        assert "Desc 1" in all_text


@pytest.mark.asyncio
async def test_number_input_selects_running_team() -> None:
    """Entering a number selects the corresponding running team."""
    client = _mock_client(running=3, stopped=0, catalog=0)
    dismissed_with: list[str | None] = []

    class TrackingScreen(TeamSelectScreen):
        def dismiss(self, result: str | None = None) -> None:
            dismissed_with.append(result)
            return super().dismiss(result)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TrackingScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await _submit_input(app, "2", pilot)
        assert "run-2" in dismissed_with


@pytest.mark.asyncio
async def test_s_number_restores_stopped_team() -> None:
    """Entering s<N> restores the corresponding stopped team."""
    client = _mock_client(running=0, stopped=3, catalog=0)
    client.restore_team.return_value = _make_team("stop-1", "stopped-1", "running")
    dismissed_with: list[str | None] = []

    class TrackingScreen(TeamSelectScreen):
        def dismiss(self, result: str | None = None) -> None:
            dismissed_with.append(result)
            return super().dismiss(result)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TrackingScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await _submit_input(app, "s1", pilot)
        await pilot.pause()  # wait for worker
        await pilot.pause()
        client.restore_team.assert_called_once_with("stop-1")
        assert "stop-1" in dismissed_with


@pytest.mark.asyncio
async def test_c_name_creates_team() -> None:
    """Entering 'c <name>' creates a team from catalog."""
    client = _mock_client(running=0, stopped=0, catalog=1)
    client.create_team.return_value = _make_team("new-1", "new-team", "running")
    dismissed_with: list[str | None] = []

    class TrackingScreen(TeamSelectScreen):
        def dismiss(self, result: str | None = None) -> None:
            dismissed_with.append(result)
            return super().dismiss(result)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TrackingScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await _submit_input(app, "c cat-1", pilot)
        await pilot.pause()  # wait for worker
        await pilot.pause()
        client.create_team.assert_called_once_with("cat-1")
        assert "new-1" in dismissed_with


@pytest.mark.asyncio
async def test_q_dismisses_with_quit() -> None:
    """Entering 'q' dismisses the screen with __quit__ signal."""
    client = _mock_client(running=1, stopped=0, catalog=0)
    dismissed_with: list[str | None] = []

    class TrackingScreen(TeamSelectScreen):
        def dismiss(self, result: str | None = None) -> None:
            dismissed_with.append(result)
            return super().dismiss(result)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TrackingScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await _submit_input(app, "q", pilot)
        assert "__quit__" in dismissed_with


@pytest.mark.asyncio
async def test_invalid_number_shows_error() -> None:
    """Entering an out-of-range number shows an error message."""
    client = _mock_client(running=2, stopped=0, catalog=0)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TeamSelectScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await _submit_input(app, "99", pilot)
        all_text = _get_team_list_text(app)
        assert "Invalid selection" in all_text


@pytest.mark.asyncio
async def test_api_error_during_fetch_shows_empty() -> None:
    """ApiError during data fetch renders empty sections gracefully."""
    client = MagicMock(spec=ApiClient)
    client.list_teams.side_effect = ApiError(500, "connection refused")
    client.list_catalog_teams.side_effect = ApiError(500, "connection refused")

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TeamSelectScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        all_text = _get_team_list_text(app)
        assert "(none)" in all_text


# -- Task 7: Pagination tests --


@pytest.mark.asyncio
async def test_pagination_indicator_with_many_teams() -> None:
    """More than 5 running teams shows pagination indicator."""
    client = _mock_client(running=12, stopped=0, catalog=0)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TeamSelectScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        all_text = _get_team_list_text(app)
        assert "1-5 of 12" in all_text


@pytest.mark.asyncio
async def test_n_advances_page() -> None:
    """Typing 'n' advances to the next page with correct numbering."""
    client = _mock_client(running=8, stopped=0, catalog=0)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TeamSelectScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await _submit_input(app, "n", pilot)
        all_text = _get_team_list_text(app)
        assert "6-8 of 8" in all_text


@pytest.mark.asyncio
async def test_p_goes_back() -> None:
    """Typing 'p' goes back to previous page."""
    client = _mock_client(running=8, stopped=0, catalog=0)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TeamSelectScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await _submit_input(app, "n", pilot)
        await _submit_input(app, "p", pilot)
        all_text = _get_team_list_text(app)
        assert "1-5 of 8" in all_text


@pytest.mark.asyncio
async def test_right_arrow_advances_page() -> None:
    """Right arrow key advances page."""
    client = _mock_client(running=8, stopped=0, catalog=0)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TeamSelectScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        all_text = _get_team_list_text(app)
        assert "6-8 of 8" in all_text


@pytest.mark.asyncio
async def test_first_page_no_prev() -> None:
    """First page does not go back further."""
    client = _mock_client(running=8, stopped=0, catalog=0)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TeamSelectScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await _submit_input(app, "p", pilot)
        all_text = _get_team_list_text(app)
        assert "1-5 of 8" in all_text


@pytest.mark.asyncio
async def test_last_page_no_next() -> None:
    """Last page does not advance further."""
    client = _mock_client(running=3, stopped=0, catalog=0)

    class TestApp(ChatApp):
        def on_mount(self) -> None:
            self.push_screen(TeamSelectScreen(client=client))

    app = TestApp(team_name="t", team_id="pre-set", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await _submit_input(app, "n", pilot)
        all_text = _get_team_list_text(app)
        assert "Running teams (3):" in all_text


# -- Task 8: ChatApp integration tests --


@pytest.mark.asyncio
async def test_chatapp_no_team_pushes_select_screen() -> None:
    """ChatApp with no team_id pushes TeamSelectScreen on mount."""
    client = _mock_client(running=1, stopped=0, catalog=0)

    app = ChatApp(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, TeamSelectScreen)


@pytest.mark.asyncio
async def test_chatapp_with_team_skips_select_screen() -> None:
    """ChatApp with pre-set team_id skips TeamSelectScreen."""
    app = ChatApp(team_name="test", team_id="abc123", team_status="running")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, TeamSelectScreen)
