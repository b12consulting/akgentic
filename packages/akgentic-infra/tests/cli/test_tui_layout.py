"""Pilot tests verifying the four-zone TUI layout."""

from __future__ import annotations

import pytest

from akgentic.infra.cli.tui.app import ChatApp
from akgentic.infra.cli.tui.widgets.chat_input import ChatInput
from akgentic.infra.cli.tui.widgets.hint_bar import HintBar
from akgentic.infra.cli.tui.widgets.status_header import StatusHeader


@pytest.mark.asyncio
async def test_chat_app_has_four_zones() -> None:
    """Verify all four layout zones are present."""
    app = ChatApp(team_name="test-team", team_id="abc123def456", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        header = pilot.app.query_one(StatusHeader)
        assert header is not None

        conversation = pilot.app.query_one("#conversation")
        assert conversation is not None

        chat_input = pilot.app.query_one(ChatInput)
        assert chat_input is not None

        hint_bar = pilot.app.query_one(HintBar)
        assert hint_bar is not None


@pytest.mark.asyncio
async def test_status_header_shows_team_info() -> None:
    """Verify StatusHeader displays team name and truncated ID."""
    app = ChatApp(team_name="research-team", team_id="abc123def456ab", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        header = pilot.app.query_one(StatusHeader)
        rendered = header.render()
        text = str(rendered)
        assert "research-team" in text
        assert "abc123def456a" in text  # truncated to 13 chars


@pytest.mark.asyncio
async def test_status_header_connection_states() -> None:
    """Verify StatusHeader renders different connection states."""
    app = ChatApp(team_name="test", team_id="123", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        header = pilot.app.query_one(StatusHeader)

        # Default is connected
        text = str(header.render())
        assert "connected" in text

        # Update to disconnected
        header.update_connection("disconnected")
        text = str(header.render())
        assert "disconnected" in text

        # Update to reconnecting
        header.update_connection("reconnecting")
        text = str(header.render())
        assert "reconnecting" in text


@pytest.mark.asyncio
async def test_status_header_paused_status() -> None:
    """Verify StatusHeader shows pause icon for non-running status."""
    app = ChatApp(team_name="test", team_id="123", team_status="paused")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        header = pilot.app.query_one(StatusHeader)
        text = str(header.render())
        assert "\u23f8" in text  # pause icon
        assert "paused" in text


@pytest.mark.asyncio
async def test_chat_input_submit() -> None:
    """Verify Enter clears input after submit."""
    app = ChatApp(team_name="test", team_id="123", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.press("enter")
        await pilot.pause()
        assert chat_input.text.strip() == ""


@pytest.mark.asyncio
async def test_chat_input_history() -> None:
    """Verify ChatInput stores submitted text in history."""
    app = ChatApp(team_name="test", team_id="123", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        assert len(chat_input._history) == 1
        assert chat_input._history[0] == "hi"


@pytest.mark.asyncio
async def test_welcome_message_present() -> None:
    """Verify conversation area has welcome placeholder."""
    app = ChatApp(team_name="test", team_id="123", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        welcome = pilot.app.query_one("#welcome")
        assert welcome is not None


@pytest.mark.asyncio
async def test_hint_bar_default_hints() -> None:
    """Verify HintBar shows default hints."""
    app = ChatApp(team_name="test", team_id="123", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        hint_bar = pilot.app.query_one(HintBar)
        text = str(hint_bar.render())
        assert "Enter: send" in text
        assert "@mention" in text


@pytest.mark.asyncio
async def test_hint_bar_update() -> None:
    """Verify HintBar can update its hints."""
    app = ChatApp(team_name="test", team_id="123", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        hint_bar = pilot.app.query_one(HintBar)
        hint_bar.update_hints("Custom hints")
        text = str(hint_bar.render())
        assert "Custom hints" in text


@pytest.mark.asyncio
async def test_status_header_update_team() -> None:
    """Verify StatusHeader.update_team changes displayed info."""
    app = ChatApp(team_name="old", team_id="000", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        header = pilot.app.query_one(StatusHeader)
        header.update_team("new-team", "abc123def456xyz", "paused")
        text = str(header.render())
        assert "new-team" in text
        assert "abc123def456x" in text  # truncated to 13
        assert "paused" in text


@pytest.mark.asyncio
async def test_chat_input_empty_does_not_submit() -> None:
    """Verify Enter on empty input does not add to history (no submission)."""
    app = ChatApp(team_name="test", team_id="123", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        assert len(chat_input._history) == 0
        # Press enter on empty input
        await pilot.click(ChatInput)
        await pilot.press("enter")
        await pilot.pause()
        # History should remain empty -- nothing submitted
        assert len(chat_input._history) == 0


@pytest.mark.asyncio
async def test_chat_input_submitted_message_content() -> None:
    """Verify Submitted message carries the correct text via history."""
    app = ChatApp(team_name="test", team_id="123", team_status="running")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        await pilot.press("t", "e", "s", "t")
        await pilot.press("enter")
        await pilot.pause()
        # History records the submitted text
        assert len(chat_input._history) == 1
        assert chat_input._history[0] == "test"
        # Input was cleared after submit
        assert chat_input.text.strip() == ""
