"""Tests for EventRouter -- typed event routing and dispatch."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

from akgentic.core.messages.message import Message

from akgentic.infra.cli.event_router import EventRouter
from akgentic.infra.cli.tui.colors import AgentColorRegistry
from akgentic.infra.cli.tui.widgets.agent_message import AgentMessage
from akgentic.infra.cli.tui.widgets.error import ErrorWidget
from akgentic.infra.cli.tui.widgets.human_input import HumanInputPrompt
from akgentic.infra.cli.tui.widgets.tool_call import ToolCallWidget
from tests.fixtures.events import (
    build_error_message,
    build_event_message,
    build_sent_message,
    build_start_message,
    build_tool_call_event,
    build_tool_return_event,
)

from .conftest import captured_renderer as _captured_renderer


def _make_router(
    renderer: Any = None,
    on_human_input: Any = None,
) -> tuple[EventRouter, Any]:
    """Build an EventRouter with a captured renderer."""
    if renderer is None:
        renderer, buf = _captured_renderer()
    else:
        buf = None
    router = EventRouter(renderer, on_human_input=on_human_input)
    return router, buf


# ---------------------------------------------------------------------------
# route() tests — all accept typed Message instances
# ---------------------------------------------------------------------------


class TestRouteSentMessage:
    def test_valid_sent_message_renders(self) -> None:
        renderer, buf = _captured_renderer()
        router = EventRouter(renderer)
        event = build_sent_message(content="hello")
        result = router.route(event)
        assert result is True
        out = buf.getvalue()
        assert "sender" in out
        assert "hello" in out

    def test_sent_message_empty_content_returns_false(self) -> None:
        renderer, buf = _captured_renderer()
        router = EventRouter(renderer)
        event = build_sent_message(content="")
        result = router.route(event)
        assert result is False

    def test_sent_message_sender_name_extracted(self) -> None:
        renderer, buf = _captured_renderer()
        router = EventRouter(renderer)
        event = build_sent_message(content="from typed sender")
        result = router.route(event)
        assert result is True
        out = buf.getvalue()
        assert "sender" in out
        assert "from typed sender" in out


class TestRouteErrorMessage:
    def test_valid_error_message_renders(self) -> None:
        renderer, buf = _captured_renderer()
        router = EventRouter(renderer)
        event = build_error_message(exception_value="something broke")
        result = router.route(event)
        assert result is True
        out = buf.getvalue()
        assert "error" in out
        assert "something broke" in out


class TestRouteToolCall:
    def test_tool_call_renders(self) -> None:
        renderer, buf = _captured_renderer()
        router = EventRouter(renderer)
        event = build_event_message(event=build_tool_call_event(tool_name="search"))
        result = router.route(event)
        assert result is True
        out = buf.getvalue()
        assert "search" in out


class TestRouteHumanInput:
    def test_human_input_invokes_callback(self) -> None:
        renderer, buf = _captured_renderer()
        callback = MagicMock()
        router = EventRouter(renderer, on_human_input=callback)

        @dataclass
        class FakeHumanInput:
            prompt: str

        event = build_event_message(event=FakeHumanInput(prompt="Enter your name"))
        result = router.route(event)
        assert result is True
        out = buf.getvalue()
        assert "Human Input Required" in out
        assert "Enter your name" in out
        callback.assert_called_once()

    def test_human_input_renders_without_callback(self) -> None:
        renderer, buf = _captured_renderer()
        router = EventRouter(renderer, on_human_input=None)

        @dataclass
        class FakeHumanInput:
            prompt: str

        event = build_event_message(event=FakeHumanInput(prompt="Please provide input"))
        result = router.route(event)
        assert result is True
        out = buf.getvalue()
        assert "Human Input Required" in out


class TestRouteUnknownModel:
    def test_unknown_model_returns_false(self) -> None:
        renderer, buf = _captured_renderer()
        router = EventRouter(renderer)
        event = build_start_message()
        result = router.route(event)
        assert result is False


class TestRouteMalformedEvents:
    def test_base_message_returns_false(self) -> None:
        renderer, buf = _captured_renderer()
        logger = logging.getLogger("test.event_router")
        router = EventRouter(renderer, logger=logger)
        event = Message()
        result = router.route(event)
        assert result is False


class TestRouteToolReturn:
    def test_tool_return_not_rendered(self) -> None:
        renderer, buf = _captured_renderer()
        router = EventRouter(renderer)
        event = build_event_message(event=build_tool_return_event(tool_name="search"))
        result = router.route(event)
        assert result is False


class TestNotifyHumanInput:
    def test_extracts_id_and_sender(self) -> None:
        renderer, buf = _captured_renderer()
        callback = MagicMock()
        router = EventRouter(renderer, on_human_input=callback)

        @dataclass
        class FakeHumanInput:
            prompt: str

        from akgentic.core.actor_address_impl import ActorAddressProxy

        from tests.fixtures.events import _make_address_dict

        sender = ActorAddressProxy(_make_address_dict(name="BotX"))
        event = build_event_message(
            event=FakeHumanInput(prompt="question?"),
            sender=sender,
        )
        router.route(event)
        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[1] == "BotX"


class TestToolCallArgFormats:
    def test_string_arguments_rendered(self) -> None:
        renderer, buf = _captured_renderer()
        router = EventRouter(renderer)
        event = build_event_message(
            event=build_tool_call_event(
                tool_name="search",
                arguments='{"query": "test", "limit": 10}',
            )
        )
        result = router.route(event)
        assert result is True
        out = buf.getvalue()
        assert "search" in out


# ---------------------------------------------------------------------------
# to_widget() tests — all accept typed Message instances
# ---------------------------------------------------------------------------


def _make_widget_router() -> tuple[EventRouter, AgentColorRegistry]:
    """Build an EventRouter and AgentColorRegistry for to_widget tests."""
    renderer, _ = _captured_renderer()
    router = EventRouter(renderer)
    registry = AgentColorRegistry()
    return router, registry


class TestToWidgetSentMessage:
    def test_returns_agent_message(self) -> None:
        router, registry = _make_widget_router()
        event = build_sent_message(content="hello")
        widget = router.to_widget(event, registry)
        assert isinstance(widget, AgentMessage)

    def test_empty_content_returns_none(self) -> None:
        router, registry = _make_widget_router()
        event = build_sent_message(content="")
        widget = router.to_widget(event, registry)
        assert widget is None

    def test_uses_color_registry(self) -> None:
        router, registry = _make_widget_router()
        event = build_sent_message(content="hi")
        router.to_widget(event, registry)
        assert "sender" in registry._map

    def test_recipient_extracted(self) -> None:
        """AC #1/#4: recipient is extracted and passed to AgentMessage."""
        router, registry = _make_widget_router()
        event = build_sent_message(content="hello")
        widget = router.to_widget(event, registry)
        assert isinstance(widget, AgentMessage)
        assert widget._recipient == "recipient"

    def test_recipient_name_extracted(self) -> None:
        """SentMessage.recipient.name is extracted and passed to AgentMessage."""
        router, registry = _make_widget_router()
        event = build_sent_message(content="with recipient")
        widget = router.to_widget(event, registry)
        assert isinstance(widget, AgentMessage)
        # default recipient is _make_proxy(name="recipient")
        assert widget._recipient == "recipient"

    def test_human_sender_produces_widget(self) -> None:
        """AC #5: @Human SentMessage events are not filtered."""
        from tests.fixtures.events import _make_proxy

        router, registry = _make_widget_router()
        sender = _make_proxy(name="@Human")
        recipient = _make_proxy(name="@Developer")
        event = build_sent_message(
            content="directed message", sender=sender, recipient=recipient
        )
        widget = router.to_widget(event, registry)
        assert isinstance(widget, AgentMessage)
        assert widget._sender == "@Human"
        assert widget._recipient == "@Developer"


class TestToWidgetErrorMessage:
    def test_returns_error_widget(self) -> None:
        router, registry = _make_widget_router()
        event = build_error_message(exception_value="boom")
        widget = router.to_widget(event, registry)
        assert isinstance(widget, ErrorWidget)


class TestToWidgetToolCall:
    def test_returns_tool_call_widget(self) -> None:
        router, registry = _make_widget_router()
        event = build_event_message(event=build_tool_call_event(tool_name="search"))
        widget = router.to_widget(event, registry)
        assert isinstance(widget, ToolCallWidget)

    def test_tool_call_with_string_args(self) -> None:
        router, registry = _make_widget_router()
        event = build_event_message(
            event=build_tool_call_event(tool_name="calc", arguments='{"x": 1}')
        )
        widget = router.to_widget(event, registry)
        assert isinstance(widget, ToolCallWidget)

    def test_tool_return_event_does_not_produce_widget(self) -> None:
        """AC #6: ToolReturnEvent must NOT produce a ToolCallWidget."""
        router, registry = _make_widget_router()
        event = build_event_message(event=build_tool_return_event(tool_name="search"))
        widget = router.to_widget(event, registry)
        assert widget is None

    def test_tool_return_event_not_routed(self) -> None:
        """AC #6: ToolReturnEvent must NOT be rendered via route() either."""
        renderer, _buf = _captured_renderer()
        router = EventRouter(renderer)
        event = build_event_message(event=build_tool_return_event(tool_name="search"))
        result = router.route(event)
        assert result is False


class TestToWidgetHumanInput:
    def test_returns_human_input_prompt(self) -> None:
        router, registry = _make_widget_router()

        @dataclass
        class FakeHumanInput:
            prompt: str

        event = build_event_message(event=FakeHumanInput(prompt="Enter name"))
        widget = router.to_widget(event, registry)
        assert isinstance(widget, HumanInputPrompt)

    def test_request_input_model(self) -> None:
        router, registry = _make_widget_router()

        @dataclass
        class FakeRequestInput:
            prompt: str

        event = build_event_message(event=FakeRequestInput(prompt="provide data"))
        widget = router.to_widget(event, registry)
        assert isinstance(widget, HumanInputPrompt)


class TestToWidgetUnknown:
    def test_unknown_model_returns_none(self) -> None:
        router, registry = _make_widget_router()
        event = build_start_message()
        widget = router.to_widget(event, registry)
        assert widget is None


class TestToWidgetMalformed:
    def test_base_message_returns_none(self) -> None:
        router, registry = _make_widget_router()
        event = Message()
        widget = router.to_widget(event, registry)
        assert widget is None
