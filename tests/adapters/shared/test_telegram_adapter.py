"""Tests for TelegramChannelAdapter."""

from __future__ import annotations

import json
import uuid

import httpx

from akgentic.core.actor_address_impl import ActorAddressProxy
from akgentic.core.messages.orchestrator import SentMessage

from akgentic.infra.adapters.shared.telegram_adapter import TelegramChannelAdapter


# ---------------------------------------------------------------------------
# Helpers (following test_channel_dispatcher.py patterns)
# ---------------------------------------------------------------------------


def _make_addr(role: str = "UserProxy", name: str = "987654321") -> ActorAddressProxy:
    return ActorAddressProxy(
        {
            "__actor_address__": True,
            "__actor_type__": "akgentic.core.actor_address_impl.ActorAddressProxy",
            "agent_id": str(uuid.uuid4()),
            "name": name,
            "role": role,
            "team_id": str(uuid.uuid4()),
            "squad_id": str(uuid.uuid4()),
            "user_message": False,
        }
    )


def _make_sent_message(
    role: str = "UserProxy",
    name: str = "987654321",
    content: str = "Hello from the agent!",
) -> SentMessage:
    recipient = _make_addr(role=role, name=name)
    sender = _make_addr(role="assistant", name="agent-1")
    from akgentic.core.messages.message import UserMessage

    inner = UserMessage(content=content, sender=sender)
    return SentMessage(message=inner, recipient=recipient, sender=sender)


# ---------------------------------------------------------------------------
# Mock transport for httpx
# ---------------------------------------------------------------------------


class _CaptureTransport(httpx.BaseTransport):
    """Captures requests and returns configurable responses."""

    def __init__(self, status_code: int = 200, body: dict | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._status_code = status_code
        self._body = body or {"ok": True, "result": {}}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            status_code=self._status_code,
            json=self._body,
        )


def _make_adapter(
    transport: httpx.BaseTransport | None = None,
) -> TelegramChannelAdapter:
    """Create adapter with optional mock transport."""
    adapter = TelegramChannelAdapter(bot_token="test-token-123")
    if transport is not None:
        adapter._client = httpx.Client(
            base_url="https://api.telegram.org/bottest-token-123/",
            transport=transport,
        )
    return adapter


# ---------------------------------------------------------------------------
# AC 3: matches() returns True for UserProxy
# ---------------------------------------------------------------------------


class TestMatchesUserProxy:
    """AC 3: SentMessage targeting UserProxy → matches() returns True."""

    def test_user_proxy_matches(self) -> None:
        adapter = _make_adapter()
        msg = _make_sent_message(role="UserProxy")
        assert adapter.matches(msg) is True


# ---------------------------------------------------------------------------
# AC 4: matches() returns False for non-UserProxy
# ---------------------------------------------------------------------------


class TestMatchesNonUserProxy:
    """AC 4: SentMessage targeting non-UserProxy → matches() returns False."""

    def test_agent_role_does_not_match(self) -> None:
        adapter = _make_adapter()
        msg = _make_sent_message(role="assistant")
        assert adapter.matches(msg) is False

    def test_tester_role_does_not_match(self) -> None:
        adapter = _make_adapter()
        msg = _make_sent_message(role="tester")
        assert adapter.matches(msg) is False


# ---------------------------------------------------------------------------
# AC 5: deliver() POSTs to Telegram API
# ---------------------------------------------------------------------------


class TestDeliver:
    """AC 5: deliver() sends correct POST to Telegram sendMessage."""

    def test_posts_to_send_message(self) -> None:
        transport = _CaptureTransport()
        adapter = _make_adapter(transport=transport)
        msg = _make_sent_message(name="987654321", content="Test reply")

        adapter.deliver(msg)

        assert len(transport.requests) == 1
        req = transport.requests[0]
        assert str(req.url).endswith("/sendMessage")
        body = json.loads(req.content)
        assert body["chat_id"] == "987654321"
        assert body["text"] == "Test reply"


# ---------------------------------------------------------------------------
# AC 6: deliver() handles errors without raising
# ---------------------------------------------------------------------------


class TestDeliverError:
    """AC 6: Telegram API error → logged, no exception raised."""

    def test_api_error_does_not_raise(self) -> None:
        transport = _CaptureTransport(
            status_code=400,
            body={"ok": False, "description": "Bad Request: chat not found"},
        )
        adapter = _make_adapter(transport=transport)
        msg = _make_sent_message()

        # Should not raise
        adapter.deliver(msg)


# ---------------------------------------------------------------------------
# on_stop() cleanup
# ---------------------------------------------------------------------------


class TestOnStop:
    """on_stop() closes the httpx client without error."""

    def test_on_stop_closes_client(self) -> None:
        adapter = _make_adapter()
        adapter.on_stop(uuid.uuid4())  # Should not raise
