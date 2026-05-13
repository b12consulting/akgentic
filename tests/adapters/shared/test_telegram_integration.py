"""Integration tests for Telegram channel classes with existing infrastructure.

These tests validate that the Telegram classes plug into the existing
channel model correctly — specifically that ChannelParserRegistry can
dynamically load and instantiate them via FQCN (AC 7), and that the
parsed output flows correctly through the webhook route logic (AC 8,
unit-level validation without real LLM).
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.adapters.shared.channel_parser_registry import (
    ChannelConfig,
    ChannelParserRegistry,
)
from akgentic.infra.adapters.shared.telegram_parser import TelegramChannelParser
from akgentic.infra.protocols.channels import ChannelParser, InteractionChannelAdapter


# ---------------------------------------------------------------------------
# AC 7: ChannelParserRegistry resolves Telegram classes from FQCN config
# ---------------------------------------------------------------------------


class TestRegistryResolution:
    """AC 7: FQCN config → ChannelParserRegistry resolves both classes."""

    def test_resolves_telegram_parser(self) -> None:
        config = {
            "telegram": ChannelConfig(
                parser_fqcn="akgentic.infra.adapters.shared.telegram_parser.TelegramChannelParser",
                adapter_fqcn="akgentic.infra.adapters.shared.telegram_adapter.TelegramChannelAdapter",
                config={"bot_token": "test-token", "default_catalog_entry": "my-team"},
            ),
        }
        registry = ChannelParserRegistry(channels_config=config)

        parser = registry.get_parser("telegram")
        assert parser is not None
        assert isinstance(parser, ChannelParser)
        assert parser.channel_name == "telegram"
        assert parser.default_catalog_entry == "my-team"

    def test_resolves_telegram_adapter(self) -> None:
        config = {
            "telegram": ChannelConfig(
                parser_fqcn="akgentic.infra.adapters.shared.telegram_parser.TelegramChannelParser",
                adapter_fqcn="akgentic.infra.adapters.shared.telegram_adapter.TelegramChannelAdapter",
                config={"bot_token": "test-token", "default_catalog_entry": "default"},
            ),
        }
        registry = ChannelParserRegistry(channels_config=config)

        adapters = registry.get_adapters()
        assert len(adapters) == 1
        assert isinstance(adapters[0], InteractionChannelAdapter)

    def test_channel_names_includes_telegram(self) -> None:
        config = {
            "telegram": ChannelConfig(
                parser_fqcn="akgentic.infra.adapters.shared.telegram_parser.TelegramChannelParser",
                adapter_fqcn="akgentic.infra.adapters.shared.telegram_adapter.TelegramChannelAdapter",
                config={"bot_token": "test-token"},
            ),
        }
        registry = ChannelParserRegistry(channels_config=config)
        assert "telegram" in registry.channel_names()


# ---------------------------------------------------------------------------
# AC 8 (unit-level): Webhook route parses Telegram Update correctly
# ---------------------------------------------------------------------------


class _StubIngestion:
    """Captures ingestion calls for verification."""

    def __init__(self) -> None:
        self.route_reply_calls: list[tuple] = []
        self.initiate_team_calls: list[tuple] = []

    async def route_reply(
        self,
        team_id: uuid.UUID,
        content: str,
        original_message_id: str | None = None,
    ) -> None:
        self.route_reply_calls.append((team_id, content, original_message_id))

    async def initiate_team(
        self,
        content: str,
        channel_user_id: str,
        catalog_entry_id: str,
    ) -> uuid.UUID:
        new_id = uuid.uuid4()
        self.initiate_team_calls.append((content, channel_user_id, catalog_entry_id))
        return new_id


class _StubChannelRegistry:
    """Stub ChannelRegistry that returns None (no existing team)."""

    def __init__(self) -> None:
        self.registrations: list[tuple] = []

    async def register(
        self,
        channel: str,
        channel_user_id: str,
        team_id: uuid.UUID,
    ) -> None:
        self.registrations.append((channel, channel_user_id, team_id))

    async def find_team(
        self,
        channel: str,
        channel_user_id: str,
    ) -> uuid.UUID | None:
        return None

    async def deregister(self, channel: str, channel_user_id: str) -> None:
        pass


class TestWebhookWithTelegramParser:
    """AC 8 (unit-level): Telegram Update flows through webhook route."""

    def _make_app(self) -> tuple[FastAPI, _StubIngestion, _StubChannelRegistry]:
        from akgentic.infra.server.routes.webhook import router

        parser = TelegramChannelParser(
            bot_token="test-token",
            default_catalog_entry="test-team",
        )
        registry = ChannelParserRegistry(channels_config={})
        registry._parsers[parser.channel_name] = parser

        ingestion = _StubIngestion()
        channel_registry = _StubChannelRegistry()

        app = FastAPI()
        app.include_router(router)
        app.state.channel_parser_registry = registry
        app.state.ingestion = ingestion
        app.state.channel_registry = channel_registry

        return app, ingestion, channel_registry

    def test_telegram_update_triggers_initiation(self) -> None:
        """POST Telegram Update → parser → initiation flow."""
        app, ingestion, channel_registry = self._make_app()
        client = TestClient(app)

        payload = {
            "update_id": 123456789,
            "message": {
                "message_id": 42,
                "from": {"id": 111222333, "is_bot": False, "first_name": "Test"},
                "chat": {"id": 987654321, "type": "private"},
                "date": 1711800000,
                "text": "Hello bot!",
            },
        }
        resp = client.post("/webhook/telegram", json=payload)
        assert resp.status_code == 204

        assert len(ingestion.initiate_team_calls) == 1
        content, channel_user_id, catalog_entry = ingestion.initiate_team_calls[0]
        assert content == "Hello bot!"
        assert channel_user_id == "987654321"
        assert catalog_entry == "test-team"

        assert len(channel_registry.registrations) == 1
        channel, user_id, _team_id = channel_registry.registrations[0]
        assert channel == "telegram"
        assert user_id == "987654321"

    def test_unknown_channel_returns_404(self) -> None:
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/webhook/unknown", json={"text": "hello"})
        assert resp.status_code == 404

    def test_invalid_telegram_payload_returns_400(self) -> None:
        """Payload without 'message' key → parser raises ValueError,
        surfaced as HTTP 400 (client sent a malformed body — not a 5xx).
        """
        app, _, _ = self._make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/webhook/telegram", json={"update_id": 1})
        assert resp.status_code == 400
        assert "message" in resp.json()["detail"].lower()
