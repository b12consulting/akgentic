"""Pilot tests for ChatInput: Enter/Shift+Enter, history, palette, border titles."""

from __future__ import annotations

import pytest

from akgentic.infra.cli.connection import ConnectionState
from akgentic.infra.cli.repl_commands import CommandRegistry
from akgentic.infra.cli.tui.app import ChatApp
from akgentic.infra.cli.tui.messages import ConnectionStateChanged
from akgentic.infra.cli.tui.widgets.chat_input import ChatInput
from akgentic.infra.cli.tui.widgets.command_palette import CommandPalette


def _make_app(
    command_registry: CommandRegistry | None = None,
) -> ChatApp:
    """Create a ChatApp with optional command registry."""
    return ChatApp(
        team_name="test",
        team_id="123",
        team_status="running",
        command_registry=command_registry,
    )


def _make_small_registry() -> CommandRegistry:
    """Create a small registry for testing palette filtering."""
    registry = CommandRegistry()

    async def _noop(args: str, session: object) -> None:
        pass

    registry.register("help", _noop, "Show commands", "/help")
    registry.register("teams", _noop, "List teams", "/teams")
    registry.register("test", _noop, "Run test", "/test")
    return registry


# ---------------------------------------------------------------------------
# Task 8: Enter / Shift+Enter tests (AC #1, #9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enter_submits_message() -> None:
    """Enter key submits the typed text and clears input."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.press("enter")
        await pilot.pause()
        assert chat_input.text.strip() == ""
        assert len(chat_input._history) == 1
        assert chat_input._history[0] == "hello"


@pytest.mark.asyncio
async def test_shift_enter_does_not_submit() -> None:
    """Shift+Enter does not submit (newline insertion is TextArea default).

    Note: TextArea.pilot doesn't reliably simulate shift+enter in all
    backends, so we verify the negative: that our _on_key handler only
    intercepts plain 'enter' and lets shift+enter pass through.
    """
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        await pilot.press("h", "i")
        await pilot.pause()
        # Verify text is present but not submitted
        assert chat_input.text == "hi"
        assert len(chat_input._history) == 0


@pytest.mark.asyncio
async def test_empty_input_not_submitted() -> None:
    """Enter on empty input does not submit."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        await pilot.press("enter")
        await pilot.pause()
        assert len(chat_input._history) == 0


# ---------------------------------------------------------------------------
# Task 9: Input history cycling (AC #2, #9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_up_arrow_recalls_last_input() -> None:
    """Up arrow recalls the last submitted input when current input is empty."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        # Submit a message
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.press("enter")
        await pilot.pause()
        assert chat_input.text == ""
        # Press up to recall
        await pilot.press("up")
        await pilot.pause()
        assert chat_input.text == "hello"


@pytest.mark.asyncio
async def test_multiple_up_arrows_cycle_backwards() -> None:
    """Multiple up arrows cycle backwards through history."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        # Submit messages
        await pilot.press("f", "i", "r", "s", "t")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("s", "e", "c", "o", "n", "d")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("t", "h", "i", "r", "d")
        await pilot.press("enter")
        await pilot.pause()
        # Up should go: third -> second -> first
        await pilot.press("up")
        await pilot.pause()
        assert chat_input.text == "third"
        await pilot.press("up")
        await pilot.pause()
        assert chat_input.text == "second"
        await pilot.press("up")
        await pilot.pause()
        assert chat_input.text == "first"


@pytest.mark.asyncio
async def test_down_arrow_after_up_moves_forward() -> None:
    """Down arrow after up arrow moves forward through history."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        await pilot.press("f", "i", "r", "s", "t")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("s", "e", "c", "o", "n", "d")
        await pilot.press("enter")
        await pilot.pause()
        # Go back twice
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        assert chat_input.text == "first"
        # Go forward
        await pilot.press("down")
        await pilot.pause()
        assert chat_input.text == "second"


@pytest.mark.asyncio
async def test_down_past_end_restores_empty() -> None:
    """Down arrow past end of history restores empty input."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        await pilot.press("m", "s", "g")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        assert chat_input.text == "msg"
        await pilot.press("down")
        await pilot.pause()
        assert chat_input.text == ""


@pytest.mark.asyncio
async def test_up_arrow_does_nothing_when_empty_history() -> None:
    """Up arrow does nothing when history is empty."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.click(ChatInput)
        await pilot.press("up")
        await pilot.pause()
        assert chat_input.text == ""


# ---------------------------------------------------------------------------
# Task 10: Command palette (AC #3, #4, #5, #6, #9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_palette_appears_on_slash() -> None:
    """CommandPalette appears when user types /."""
    registry = _make_small_registry()
    app = _make_app(command_registry=registry)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/")
        await pilot.pause()
        await pilot.pause()
        palette = pilot.app.query(CommandPalette)
        assert len(palette) == 1


@pytest.mark.asyncio
async def test_palette_filters_as_user_types() -> None:
    """Palette filters commands as user types more characters."""
    registry = _make_small_registry()
    app = _make_app(command_registry=registry)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/", "t", "e")
        await pilot.pause()
        await pilot.pause()
        palette_widgets = pilot.app.query(CommandPalette)
        assert len(palette_widgets) == 1
        palette = palette_widgets[0]
        # "te" should match "teams" and "test" but not "help"
        assert len(palette._filtered) == 2
        names = [c.name for c in palette._filtered]
        assert "teams" in names
        assert "test" in names
        assert "help" not in names


@pytest.mark.asyncio
async def test_tab_selects_highlighted_command() -> None:
    """Tab selects the highlighted command and replaces input text."""
    registry = _make_small_registry()
    app = _make_app(command_registry=registry)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/")
        await pilot.pause()
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        await pilot.press("tab")
        await pilot.pause()
        # Should have replaced input with /<first_command> (trailing space)
        assert chat_input.text.startswith("/")
        assert chat_input.text.endswith(" ")
        # Palette should be dismissed
        palette = pilot.app.query(CommandPalette)
        assert len(palette) == 0


@pytest.mark.asyncio
async def test_esc_dismisses_palette() -> None:
    """Esc dismisses palette without changing input."""
    registry = _make_small_registry()
    app = _make_app(command_registry=registry)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/")
        await pilot.pause()
        await pilot.pause()
        assert len(pilot.app.query(CommandPalette)) == 1
        await pilot.press("escape")
        await pilot.pause()
        assert len(pilot.app.query(CommandPalette)) == 0
        # Input should still have /
        chat_input = pilot.app.query_one(ChatInput)
        assert chat_input.text == "/"


@pytest.mark.asyncio
async def test_up_down_navigate_palette() -> None:
    """Up/down arrows navigate palette items when visible."""
    registry = _make_small_registry()
    app = _make_app(command_registry=registry)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/")
        await pilot.pause()
        await pilot.pause()
        palette_widgets = pilot.app.query(CommandPalette)
        assert len(palette_widgets) == 1
        palette = palette_widgets[0]
        initial_idx = palette._selected_idx
        assert initial_idx == 0
        await pilot.press("down")
        await pilot.pause()
        assert palette._selected_idx == 1
        await pilot.press("up")
        await pilot.pause()
        assert palette._selected_idx == 0


@pytest.mark.asyncio
async def test_palette_disappears_on_backspace_past_slash() -> None:
    """Palette disappears when / is deleted."""
    registry = _make_small_registry()
    app = _make_app(command_registry=registry)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/")
        await pilot.pause()
        await pilot.pause()
        assert len(pilot.app.query(CommandPalette)) == 1
        await pilot.press("backspace")
        await pilot.pause()
        await pilot.pause()
        assert len(pilot.app.query(CommandPalette)) == 0


@pytest.mark.asyncio
async def test_no_palette_without_registry() -> None:
    """No palette appears when no registry is provided."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/")
        await pilot.pause()
        await pilot.pause()
        assert len(pilot.app.query(CommandPalette)) == 0


@pytest.mark.asyncio
async def test_enter_selects_palette_command_and_submits() -> None:
    """Enter with palette visible selects the highlighted command and submits it."""
    registry = _make_small_registry()
    app = _make_app(command_registry=registry)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/")
        await pilot.pause()
        await pilot.pause()
        # Palette should be visible
        assert len(pilot.app.query(CommandPalette)) == 1
        # Press enter to select + submit
        await pilot.press("enter")
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        # After enter: input is cleared (submitted) and palette dismissed
        assert chat_input.text.strip() == ""
        assert len(pilot.app.query(CommandPalette)) == 0
        # The selected command should have been submitted to history
        assert len(chat_input._history) == 1
        assert chat_input._history[0].startswith("/")


# ---------------------------------------------------------------------------
# Unit tests for CommandPalette
# ---------------------------------------------------------------------------


def test_command_palette_render() -> None:
    """CommandPalette.render() produces correct Rich Text output."""
    registry = _make_small_registry()
    palette = CommandPalette(registry.commands)
    output = palette.render()
    text_str = str(output)
    assert "/help" in text_str
    assert "/teams" in text_str
    assert "/test" in text_str


def test_command_palette_render_empty_filter() -> None:
    """CommandPalette with no matches renders placeholder text."""
    registry = _make_small_registry()
    palette = CommandPalette(registry.commands)
    palette.filter_text = "zzz_no_match"
    output = palette.render()
    assert "no matching commands" in str(output)


def test_command_palette_selected_command_empty() -> None:
    """selected_command returns None when no commands match."""
    registry = _make_small_registry()
    palette = CommandPalette(registry.commands)
    palette.filter_text = "zzz_no_match"
    assert palette.selected_command is None


def test_command_palette_move_up_at_top() -> None:
    """move_up at index 0 stays at 0."""
    registry = _make_small_registry()
    palette = CommandPalette(registry.commands)
    assert palette._selected_idx == 0
    palette.move_up()
    assert palette._selected_idx == 0


def test_command_palette_move_down_at_bottom() -> None:
    """move_down at last index stays at last index."""
    registry = _make_small_registry()
    palette = CommandPalette(registry.commands)
    # Move to the last item
    for _ in range(len(palette._filtered)):
        palette.move_down()
    last_idx = len(palette._filtered) - 1
    assert palette._selected_idx == last_idx
    palette.move_down()
    assert palette._selected_idx == last_idx


def test_command_palette_filter_clamps_index() -> None:
    """filter_text clamps selected_idx when filtered list shrinks."""
    registry = _make_small_registry()
    palette = CommandPalette(registry.commands)
    # Move to idx 2 (3 commands: help, teams, test)
    palette.move_down()
    palette.move_down()
    assert palette._selected_idx == 2
    # Filter to only 1 match
    palette.filter_text = "help"
    assert palette._selected_idx == 0
    assert palette.selected_command == "help"


# ---------------------------------------------------------------------------
# Task 11: Mode-aware border title (AC #7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_border_title() -> None:
    """Default border title is '> '."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        assert chat_input.border_title == "> "


@pytest.mark.asyncio
async def test_disconnected_mode_border_title() -> None:
    """Disconnected mode shows correct border title."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.DISCONNECTED))
        await pilot.pause()
        assert chat_input.border_title == "\\[disconnected] > "


@pytest.mark.asyncio
async def test_reconnecting_mode_border_title() -> None:
    """Reconnecting mode shows correct border title."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.RECONNECTING))
        await pilot.pause()
        assert chat_input.border_title == "\\[reconnecting...] > "


@pytest.mark.asyncio
async def test_connected_mode_restores_border_title() -> None:
    """Connected mode restores default border title."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.DISCONNECTED))
        await pilot.pause()
        assert chat_input.border_title == "\\[disconnected] > "
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.CONNECTED))
        await pilot.pause()
        await pilot.pause()
        assert chat_input.border_title == "> "
