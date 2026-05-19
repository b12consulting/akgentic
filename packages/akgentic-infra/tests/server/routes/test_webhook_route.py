"""Tests for the webhook route — POST /webhook/{channel} with 3 routing flows."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.adapters.community.yaml_channel_registry import YamlChannelRegistry
from akgentic.infra.adapters.shared.channel_parser_registry import ChannelParserRegistry
from akgentic.infra.protocols.channels import ChannelMessage, JsonValue
from akgentic.infra.server.routes.webhook import router as webhook_router

# ---------------------------------------------------------------------------
# Stub classes satisfying protocols via structural subtyping
# ---------------------------------------------------------------------------


class StubParser:
    """Stub ChannelParser that returns a configurable ChannelMessage."""

    def __init__(
        self,
        channel: str = "test-channel",
        default_entry: str = "default-catalog",
    ) -> None:
        self._channel = channel
        self._default_entry = default_entry
        self._next_message: ChannelMessage | None = None

    @property
    def channel_name(self) -> str:
        return self._channel

    @property
    def default_catalog_entry(self) -> str:
        return self._default_entry

    def set_next_message(self, msg: ChannelMessage) -> None:
        """Configure the message that parse() will return."""
        self._next_message = msg

    async def parse(self, payload: dict[str, JsonValue]) -> ChannelMessage:
        if self._next_message is not None:
            return self._next_message
        return ChannelMessage(
            content=str(payload.get("text", "")),
            channel_user_id=str(payload.get("user", "unknown")),
        )


class StubIngestion:
    """Stub InteractionChannelIngestion that tracks calls."""

    def __init__(self) -> None:
        self.route_reply_calls: list[tuple[uuid.UUID, str, str | None]] = []
        self.initiate_team_calls: list[tuple[str, str, str]] = []
        self._next_team_id: uuid.UUID = uuid.uuid4()

    def set_next_team_id(self, team_id: uuid.UUID) -> None:
        self._next_team_id = team_id

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
        self.initiate_team_calls.append((content, channel_user_id, catalog_entry_id))
        return self._next_team_id


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _build_parser_registry(
    parser: StubParser,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> ChannelParserRegistry:
    """Build a ChannelParserRegistry with a pre-registered stub parser.

    Constructs via the public API with an empty config, then monkeypatches
    get_parser to return the stub. This avoids __new__ hacks and private
    attribute access.
    """
    registry = ChannelParserRegistry(channels_config={})

    original_get_parser = registry.get_parser

    def _patched_get_parser(channel_name: str) -> StubParser | None:
        if channel_name == parser.channel_name:
            return parser  # type: ignore[return-value]
        return original_get_parser(channel_name)

    if monkeypatch is not None:
        monkeypatch.setattr(registry, "get_parser", _patched_get_parser)
    else:
        registry.get_parser = _patched_get_parser  # type: ignore[assignment]

    return registry


def _build_app(
    parser: StubParser,
    ingestion: StubIngestion,
    channel_registry: YamlChannelRegistry,
) -> FastAPI:
    """Build a minimal FastAPI app with the webhook router wired."""
    app = FastAPI()
    parser_registry = _build_parser_registry(parser)
    app.state.channel_parser_registry = parser_registry
    app.state.channel_registry = channel_registry
    app.state.ingestion = ingestion
    app.include_router(webhook_router)
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWebhookReplyFlow:
    """AC #3: team_id in parsed message → route_reply."""

    def test_reply_flow_calls_route_reply(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        parser = StubParser()
        parser.set_next_message(
            ChannelMessage(
                content="reply msg",
                channel_user_id="user-1",
                team_id=team_id,
                message_id="msg-abc",
            )
        )
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post("/webhook/test-channel", json={"text": "hi"})

        assert resp.status_code == 204
        assert len(ingestion.route_reply_calls) == 1
        call = ingestion.route_reply_calls[0]
        assert call[0] == team_id
        assert call[1] == "reply msg"
        assert call[2] == "msg-abc"


class TestWebhookContinuationFlow:
    """AC #4: no team_id but registered team → route_reply."""

    async def test_continuation_flow_calls_route_reply(self, tmp_path: Path) -> None:
        existing_team_id = uuid.uuid4()
        parser = StubParser()
        parser.set_next_message(
            ChannelMessage(
                content="continuation msg",
                channel_user_id="user-2",
            )
        )
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        # Pre-register a team for this user
        await registry.register("test-channel", "user-2", existing_team_id)
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post("/webhook/test-channel", json={"text": "hi"})

        assert resp.status_code == 204
        assert len(ingestion.route_reply_calls) == 1
        call = ingestion.route_reply_calls[0]
        assert call[0] == existing_team_id
        assert call[1] == "continuation msg"


class TestWebhookInitiationFlow:
    """AC #5: no team_id and no existing team → initiate_team + register."""

    def test_initiation_flow_calls_initiate_team(self, tmp_path: Path) -> None:
        parser = StubParser(default_entry="my-catalog-entry")
        parser.set_next_message(
            ChannelMessage(
                content="new convo",
                channel_user_id="user-3",
            )
        )
        new_team_id = uuid.uuid4()
        ingestion = StubIngestion()
        ingestion.set_next_team_id(new_team_id)
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post("/webhook/test-channel", json={"text": "hi"})

        assert resp.status_code == 204
        assert len(ingestion.initiate_team_calls) == 1
        call = ingestion.initiate_team_calls[0]
        assert call[0] == "new convo"
        assert call[1] == "user-3"
        assert call[2] == "my-catalog-entry"

    async def test_initiation_registers_in_channel_registry(self, tmp_path: Path) -> None:
        parser = StubParser()
        parser.set_next_message(ChannelMessage(content="hello", channel_user_id="user-4"))
        new_team_id = uuid.uuid4()
        ingestion = StubIngestion()
        ingestion.set_next_team_id(new_team_id)
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        client.post("/webhook/test-channel", json={"text": "hi"})

        # Verify registration happened
        found = await registry.find_team("test-channel", "user-4")
        assert found == new_team_id


class TestWebhookUnknownChannel:
    """AC #2: unknown channel → 404."""

    def test_unknown_channel_returns_404(self, tmp_path: Path) -> None:
        parser = StubParser(channel="known-channel")
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post("/webhook/unknown-channel", json={"text": "hi"})

        assert resp.status_code == 404
        assert "Unknown channel" in resp.json()["detail"]


class TestWebhookStatusCode:
    """AC: all successful flows return 204 No Content."""

    def test_reply_returns_204(self, tmp_path: Path) -> None:
        parser = StubParser()
        parser.set_next_message(
            ChannelMessage(
                content="msg",
                channel_user_id="u",
                team_id=uuid.uuid4(),
            )
        )
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post("/webhook/test-channel", json={"text": "hi"})
        assert resp.status_code == 204

    def test_initiation_returns_204(self, tmp_path: Path) -> None:
        parser = StubParser()
        parser.set_next_message(ChannelMessage(content="msg", channel_user_id="u"))
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post("/webhook/test-channel", json={"text": "hi"})
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# AC #4: Form-data and unsupported content-type handling
# ---------------------------------------------------------------------------


class TestWebhookFormData:
    """AC #4: webhook handles application/x-www-form-urlencoded."""

    def test_form_data_payload_parsed(self, tmp_path: Path) -> None:
        parser = StubParser()
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post(
            "/webhook/test-channel",
            data={"text": "form hello", "user": "form-user"},
        )

        assert resp.status_code == 204
        assert len(ingestion.initiate_team_calls) == 1
        call = ingestion.initiate_team_calls[0]
        assert call[0] == "form hello"
        assert call[1] == "form-user"


class TestWebhookContentTypeEdgeCases:
    """AC #4: content-type edge cases."""

    def test_missing_content_type_returns_415(self, tmp_path: Path) -> None:
        """Request with no content-type header returns 415."""
        parser = StubParser()
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post(
            "/webhook/test-channel",
            content=b"some data",
            headers={"content-type": ""},
        )

        assert resp.status_code == 415

    def test_json_with_charset_param(self, tmp_path: Path) -> None:
        """application/json; charset=utf-8 is handled as JSON."""
        parser = StubParser()
        parser.set_next_message(
            ChannelMessage(
                content="charset msg",
                channel_user_id="u-charset",
                team_id=uuid.uuid4(),
            )
        )
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post(
            "/webhook/test-channel",
            json={"text": "hi"},
            headers={"content-type": "application/json; charset=utf-8"},
        )

        assert resp.status_code == 204
        assert len(ingestion.route_reply_calls) == 1


class TestWebhookUnsupportedContentType:
    """AC #4: unsupported content-type returns 415."""

    def test_unsupported_content_type_returns_415(self, tmp_path: Path) -> None:
        parser = StubParser()
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post(
            "/webhook/test-channel",
            content=b"<xml>data</xml>",
            headers={"content-type": "application/xml"},
        )

        assert resp.status_code == 415
        assert "Unsupported content type" in resp.json()["detail"]


class TestWebhookMalformedPayload:
    """Parser-raised ValueError should surface as 400, not 500."""

    def test_parser_value_error_returns_400(self, tmp_path: Path) -> None:
        class RaisingParser(StubParser):
            async def parse(self, payload: dict[str, JsonValue]) -> ChannelMessage:
                del payload
                raise ValueError("payload missing required field 'message'")

        parser = RaisingParser()
        ingestion = StubIngestion()
        registry = YamlChannelRegistry(tmp_path / "registry.yaml")
        client = TestClient(_build_app(parser, ingestion, registry))

        resp = client.post("/webhook/test-channel", json={"update_id": 1})

        assert resp.status_code == 400
        assert "payload missing required field 'message'" in resp.json()["detail"]
