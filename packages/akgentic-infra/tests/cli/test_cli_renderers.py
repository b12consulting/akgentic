"""Tests for RichRenderer."""

from __future__ import annotations

import io

from rich.console import Console

from akgentic.infra.cli.renderers import RichRenderer


def _make_renderer() -> tuple[RichRenderer, io.StringIO]:
    """Create a RichRenderer with captured output."""
    buf = io.StringIO()
    console = Console(file=buf, width=120, highlight=False, no_color=True)
    renderer = RichRenderer(console=console)
    return renderer, buf


class TestRenderAgentMessage:
    def test_sender_and_content_appear(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_agent_message("bot", "Hello world")
        output = buf.getvalue()
        assert "bot" in output
        assert "Hello world" in output

    def test_markdown_rendering(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_agent_message("bot", "**bold text**")
        output = buf.getvalue()
        # Rich renders bold as terminal escape codes; the word should appear
        assert "bold text" in output

    def test_sender_with_brackets_escaped(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_agent_message("[special]", "hello")
        output = buf.getvalue()
        # Brackets in sender name should be escaped so Rich doesn't interpret them as markup
        assert "special" in output
        assert "hello" in output


class TestAgentColorAssignment:
    def test_same_agent_same_color(self) -> None:
        renderer, _ = _make_renderer()
        color1 = renderer._get_agent_color("agent-a")
        color2 = renderer._get_agent_color("agent-a")
        assert color1 == color2

    def test_different_agents_different_colors(self) -> None:
        renderer, _ = _make_renderer()
        color1 = renderer._get_agent_color("agent-a")
        color2 = renderer._get_agent_color("agent-b")
        assert color1 != color2

    def test_colors_wrap_after_palette_exhaustion(self) -> None:
        renderer, _ = _make_renderer()
        palette_size = len(RichRenderer._PALETTE)
        colors = []
        for i in range(palette_size + 2):
            colors.append(renderer._get_agent_color(f"agent-{i}"))
        # After exhausting palette, colors wrap around
        assert colors[0] == colors[palette_size]
        assert colors[1] == colors[palette_size + 1]


class TestRenderError:
    def test_error_prefix_and_content(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_error("something broke")
        output = buf.getvalue()
        assert "error" in output
        assert "something broke" in output


class TestRenderToolCall:
    def test_tool_name_and_input_in_output(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_tool_call("search", '{"query": "test"}', "result data")
        output = buf.getvalue()
        assert "search" in output
        assert "query" in output
        assert "result data" in output

    def test_json_input_syntax_highlighted(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_tool_call("search", '{"key": "value"}', None)
        output = buf.getvalue()
        # JSON should be pretty-printed (indented) via Syntax
        assert "key" in output
        assert "value" in output

    def test_tool_call_without_output(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_tool_call("search", "raw input", None)
        output = buf.getvalue()
        assert "search" in output
        assert "raw input" in output
        assert "Output" not in output

    def test_non_json_input(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_tool_call("run", "not json", "done")
        output = buf.getvalue()
        assert "not json" in output
        assert "done" in output


class TestRenderHumanInputRequest:
    def test_prompt_text_and_title(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_human_input_request("Please enter your name")
        output = buf.getvalue()
        assert "Human Input Required" in output
        assert "Please enter your name" in output


class TestRenderHistorySeparator:
    def test_separator_contains_history(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_history_separator()
        output = buf.getvalue()
        assert "history" in output


class TestRenderSystemMessage:
    def test_dim_text_appears(self) -> None:
        renderer, buf = _make_renderer()
        renderer.render_system_message("Connecting...")
        output = buf.getvalue()
        assert "Connecting..." in output
