"""Pilot tests for conversation widgets and AgentColorRegistry."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from akgentic.infra.cli.tui.app import ChatApp
from akgentic.infra.cli.tui.colors import AgentColorRegistry
from akgentic.infra.cli.tui.widgets.agent_message import AgentMessage
from akgentic.infra.cli.tui.widgets.error import ErrorWidget
from akgentic.infra.cli.tui.widgets.human_input import HumanInputPrompt
from akgentic.infra.cli.tui.widgets.system_message import HistorySeparator, SystemMessage
from akgentic.infra.cli.tui.widgets.tool_call import ToolCallWidget


def _render_to_str(renderable: object) -> str:
    """Render a Rich renderable to plain text via Console."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, no_color=True)
    console.print(renderable)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# AgentColorRegistry tests (no TUI needed)
# ---------------------------------------------------------------------------


class TestAgentColorRegistry:
    def test_assigns_first_color(self) -> None:
        reg = AgentColorRegistry()
        assert reg.get("Alice") == "cyan"

    def test_round_robin_assignment(self) -> None:
        reg = AgentColorRegistry()
        expected = AgentColorRegistry._PALETTE
        for i, name in enumerate(["a", "b", "c", "d", "e", "f"]):
            assert reg.get(name) == expected[i]

    def test_consistent_color_for_same_agent(self) -> None:
        reg = AgentColorRegistry()
        first = reg.get("Agent1")
        second = reg.get("Agent1")
        assert first == second

    def test_wraps_around_palette(self) -> None:
        reg = AgentColorRegistry()
        for i in range(6):
            reg.get(f"agent-{i}")
        # 7th agent wraps to first color
        assert reg.get("agent-6") == "cyan"

    def test_reset_clears_state(self) -> None:
        reg = AgentColorRegistry()
        reg.get("Alpha")
        reg.get("Beta")
        reg.reset()
        assert reg.get("Gamma") == "cyan"

    def test_palette_matches_rich_renderer(self) -> None:
        assert AgentColorRegistry._PALETTE == [
            "cyan", "green", "magenta", "yellow", "blue", "red"
        ]


# ---------------------------------------------------------------------------
# Widget pilot tests
# ---------------------------------------------------------------------------


def _make_app() -> ChatApp:
    return ChatApp(team_name="test", team_id="123", team_status="running")


@pytest.mark.asyncio
async def test_agent_message_renders_sender_and_content() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        msg = AgentMessage(sender="Manager", content="Hello world", color="cyan")
        await conv.mount(msg)
        rendered = _render_to_str(msg.render())
        assert "Manager" in rendered
        assert "Hello world" in rendered


@pytest.mark.asyncio
async def test_agent_message_sender_format() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        msg = AgentMessage(sender="Analyst", content="data ready", color="green")
        await conv.mount(msg)
        rendered = _render_to_str(msg.render())
        assert "[@Analyst]" in rendered


@pytest.mark.asyncio
async def test_tool_call_collapsed_shows_name() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        tool = ToolCallWidget("search", '{"query": "test"}', None)
        await conv.mount(tool)
        rendered = _render_to_str(tool.render())
        assert "search" in rendered
        assert "Tool:" in rendered


@pytest.mark.asyncio
async def test_tool_call_expand_on_click() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        tool = ToolCallWidget("search", '{"query": "test"}', None)
        await conv.mount(tool)
        assert tool.collapsed is True
        tool.on_click()
        assert tool.collapsed is False


@pytest.mark.asyncio
async def test_tool_call_expanded_shows_input() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        tool = ToolCallWidget("calc", '{"x": 1}', "result: 42")
        await conv.mount(tool)
        tool.collapsed = False
        await pilot.pause()
        rendered = _render_to_str(tool._build_expanded())
        assert "calc" in rendered
        assert "result: 42" in rendered


@pytest.mark.asyncio
async def test_tool_call_invalid_json_input() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        tool = ToolCallWidget("run", "not json", None)
        await conv.mount(tool)
        tool.collapsed = False
        await pilot.pause()
        rendered = _render_to_str(tool._build_expanded())
        assert "not json" in rendered


@pytest.mark.asyncio
async def test_human_input_prompt_renders() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        prompt = HumanInputPrompt(prompt_text="Enter your name")
        await conv.mount(prompt)
        rendered = _render_to_str(prompt.render())
        assert "Enter your name" in rendered
        assert "Human Input Required" in rendered


@pytest.mark.asyncio
async def test_error_widget_renders() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        err = ErrorWidget(content="Something failed")
        await conv.mount(err)
        rendered = _render_to_str(err.render())
        assert "error" in rendered
        assert "Something failed" in rendered


@pytest.mark.asyncio
async def test_system_message_renders() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        sys_msg = SystemMessage(content="Connected to team")
        await conv.mount(sys_msg)
        rendered = _render_to_str(sys_msg.render())
        assert "Connected to team" in rendered


@pytest.mark.asyncio
async def test_history_separator_renders() -> None:
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        sep = HistorySeparator()
        await conv.mount(sep)
        rendered = _render_to_str(sep.render())
        assert "history" in rendered
