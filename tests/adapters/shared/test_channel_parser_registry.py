"""Tests for ChannelParserRegistry — FQCN-based channel parser/adapter resolution."""

from __future__ import annotations

import uuid

import pytest

from akgentic.infra.adapters.shared.channel_parser_registry import (
    ChannelConfig,
    ChannelParserRegistry,
    import_class,
)
from akgentic.infra.protocols.channels import ChannelMessage, JsonValue

# --- Stub implementations for testing ---


class StubWhatsAppParser:
    """Test stub satisfying ChannelParser protocol."""

    @property
    def channel_name(self) -> str:
        return "whatsapp"

    @property
    def default_catalog_entry(self) -> str:
        return "default-whatsapp-team"

    async def parse(self, payload: dict[str, JsonValue]) -> ChannelMessage:
        return ChannelMessage(
            content=str(payload.get("text", "")),
            channel_user_id=str(payload.get("from", "")),
        )


class StubWhatsAppAdapter:
    """Test stub satisfying InteractionChannelAdapter protocol."""

    def matches(self, msg: object) -> bool:
        return False

    def deliver(self, msg: object) -> None:
        pass

    def on_stop(self, team_id: uuid.UUID) -> None:
        pass


class StubSlackParser:
    """Test stub for a second channel parser."""

    @property
    def channel_name(self) -> str:
        return "slack"

    @property
    def default_catalog_entry(self) -> str:
        return "default-slack-team"

    async def parse(self, payload: dict[str, JsonValue]) -> ChannelMessage:
        return ChannelMessage(
            content=str(payload.get("text", "")),
            channel_user_id=str(payload.get("user", "")),
        )


class StubSlackAdapter:
    """Test stub for a second channel adapter."""

    def matches(self, msg: object) -> bool:
        return False

    def deliver(self, msg: object) -> None:
        pass

    def on_stop(self, team_id: uuid.UUID) -> None:
        pass


# --- import_class tests ---


def test_import_class_resolves_known_class() -> None:
    """import_class() resolves a known class from its FQCN."""
    cls = import_class("akgentic.infra.protocols.channels.ChannelMessage")
    assert cls is ChannelMessage


def test_import_class_invalid_fqcn_no_dot() -> None:
    """import_class() raises ImportError for FQCN without a module path."""
    with pytest.raises(ImportError, match="Invalid FQCN"):
        import_class("NoDots")


def test_import_class_module_not_found() -> None:
    """import_class() raises ImportError for non-existent module."""
    with pytest.raises(ImportError, match="not found"):
        import_class("nonexistent.module.SomeClass")


def test_import_class_class_not_found() -> None:
    """import_class() raises ImportError for missing class in valid module."""
    with pytest.raises(ImportError, match="not found"):
        import_class("akgentic.infra.protocols.channels.NonExistentClass")


# --- ChannelParserRegistry tests ---


def _make_config() -> dict[str, ChannelConfig]:
    """Build a channels config pointing to test stubs in this module."""
    this_module = "tests.adapters.shared.test_channel_parser_registry"
    return {
        "whatsapp": ChannelConfig(
            parser_fqcn=f"{this_module}.StubWhatsAppParser",
            adapter_fqcn=f"{this_module}.StubWhatsAppAdapter",
        ),
        "slack": ChannelConfig(
            parser_fqcn=f"{this_module}.StubSlackParser",
            adapter_fqcn=f"{this_module}.StubSlackAdapter",
        ),
    }


def test_registry_loads_parsers_and_adapters() -> None:
    """ChannelParserRegistry resolves parsers and adapters from config."""
    registry = ChannelParserRegistry(_make_config())
    assert len(registry.channel_names()) == 2
    assert len(registry.get_adapters()) == 2


def test_get_parser_returns_correct_parser() -> None:
    """get_parser() returns the parser indexed by channel_name."""
    registry = ChannelParserRegistry(_make_config())
    parser = registry.get_parser("whatsapp")
    assert parser is not None
    assert parser.channel_name == "whatsapp"


def test_get_parser_returns_none_for_unknown() -> None:
    """get_parser() returns None for an unregistered channel name."""
    registry = ChannelParserRegistry(_make_config())
    assert registry.get_parser("telegram") is None


def test_get_adapters_returns_all_adapters() -> None:
    """get_adapters() returns all resolved adapter instances."""
    registry = ChannelParserRegistry(_make_config())
    adapters = registry.get_adapters()
    assert len(adapters) == 2
    # Verify they are distinct instances
    assert adapters[0] is not adapters[1]


def test_get_adapters_returns_copy() -> None:
    """get_adapters() returns a copy, not the internal list."""
    registry = ChannelParserRegistry(_make_config())
    adapters = registry.get_adapters()
    adapters.clear()
    assert len(registry.get_adapters()) == 2


def test_channel_names_returns_all_names() -> None:
    """channel_names() returns all registered channel names."""
    registry = ChannelParserRegistry(_make_config())
    names = registry.channel_names()
    assert sorted(names) == ["slack", "whatsapp"]


def test_registry_with_empty_config() -> None:
    """ChannelParserRegistry with empty config has no parsers or adapters."""
    registry = ChannelParserRegistry({})
    assert registry.channel_names() == []
    assert registry.get_adapters() == []


def test_registry_invalid_parser_fqcn() -> None:
    """ChannelParserRegistry raises ImportError for invalid parser FQCN."""
    config = {
        "bad": ChannelConfig(
            parser_fqcn="nonexistent.module.BadParser",
            adapter_fqcn="tests.adapters.shared.test_channel_parser_registry.StubWhatsAppAdapter",
        ),
    }
    with pytest.raises(ImportError):
        ChannelParserRegistry(config)


def test_registry_invalid_adapter_fqcn() -> None:
    """ChannelParserRegistry raises ImportError for invalid adapter FQCN."""
    config = {
        "bad": ChannelConfig(
            parser_fqcn="tests.adapters.shared.test_channel_parser_registry.StubWhatsAppParser",
            adapter_fqcn="nonexistent.module.BadAdapter",
        ),
    }
    with pytest.raises(ImportError):
        ChannelParserRegistry(config)


class _NotAParser:
    """Deliberately does NOT satisfy ChannelParser protocol."""

    pass


class _NotAnAdapter:
    """Deliberately does NOT satisfy InteractionChannelAdapter protocol."""

    pass


def test_registry_rejects_non_parser_protocol() -> None:
    """ChannelParserRegistry raises TypeError when class doesn't satisfy ChannelParser."""
    config = {
        "bad": ChannelConfig(
            parser_fqcn="tests.adapters.shared.test_channel_parser_registry._NotAParser",
            adapter_fqcn="tests.adapters.shared.test_channel_parser_registry.StubWhatsAppAdapter",
        ),
    }
    with pytest.raises(TypeError, match="ChannelParser"):
        ChannelParserRegistry(config)


def test_registry_rejects_non_adapter_protocol() -> None:
    """ChannelParserRegistry raises TypeError for non-InteractionChannelAdapter."""
    config = {
        "bad": ChannelConfig(
            parser_fqcn="tests.adapters.shared.test_channel_parser_registry.StubWhatsAppParser",
            adapter_fqcn="tests.adapters.shared.test_channel_parser_registry._NotAnAdapter",
        ),
    }
    with pytest.raises(TypeError, match="InteractionChannelAdapter"):
        ChannelParserRegistry(config)


# --- Config passthrough stubs ---


class StubConfigParser:
    """Parser stub that accepts config kwargs."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    @property
    def channel_name(self) -> str:
        return "configurable"

    @property
    def default_catalog_entry(self) -> str:
        return "default-configurable"

    async def parse(self, payload: dict[str, JsonValue]) -> ChannelMessage:
        return ChannelMessage(content="", channel_user_id="")


class StubConfigAdapter:
    """Adapter stub that accepts config kwargs."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def matches(self, msg: object) -> bool:
        return False

    def deliver(self, msg: object) -> None:
        pass

    def on_stop(self, team_id: uuid.UUID) -> None:
        pass


# --- Config passthrough tests ---


def test_channel_config_has_config_field() -> None:
    """ChannelConfig has a config dict field with empty dict default."""
    cfg = ChannelConfig(
        parser_fqcn="tests.adapters.shared.test_channel_parser_registry.StubWhatsAppParser",
        adapter_fqcn="tests.adapters.shared.test_channel_parser_registry.StubWhatsAppAdapter",
    )
    assert cfg.config == {}


def test_config_passthrough_to_constructors() -> None:
    """Config values are passed to parser and adapter constructors."""
    this_module = "tests.adapters.shared.test_channel_parser_registry"
    config = {
        "configurable": ChannelConfig(
            parser_fqcn=f"{this_module}.StubConfigParser",
            adapter_fqcn=f"{this_module}.StubConfigAdapter",
            config={"api_key": "test-secret-123"},
        ),
    }
    registry = ChannelParserRegistry(config)

    parser = registry.get_parser("configurable")
    assert parser is not None
    assert parser.api_key == "test-secret-123"  # type: ignore[attr-defined]

    adapters = registry.get_adapters()
    assert len(adapters) == 1
    assert adapters[0].api_key == "test-secret-123"  # type: ignore[attr-defined]


def test_empty_config_works_with_existing_stubs() -> None:
    """Existing stubs with no config params still work with empty config dict."""
    registry = ChannelParserRegistry(_make_config())
    assert len(registry.channel_names()) == 2
    assert len(registry.get_adapters()) == 2


def test_channel_config_is_pydantic_model() -> None:
    """ChannelConfig is a Pydantic model with correct fields."""
    from pydantic import BaseModel

    assert issubclass(ChannelConfig, BaseModel)
    fields = ChannelConfig.model_fields
    assert "parser_fqcn" in fields
    assert "adapter_fqcn" in fields
    assert "config" in fields


# ---------------------------------------------------------------------------
# Reclassified from integration/test_spec_channels.py — TestChannelConfigPassthrough
# Uses monkeypatch + direct construction; no real app needed.
# ---------------------------------------------------------------------------


class _StubParserWithConfig:
    """Parser stub that captures constructor config args."""

    received_config: dict[str, str]

    def __init__(self, **kwargs: str) -> None:
        _StubParserWithConfig.received_config = dict(kwargs)

    @property
    def channel_name(self) -> str:
        return "config-test-channel"

    @property
    def default_catalog_entry(self) -> str:
        return "test-team"

    async def parse(self, payload: dict[str, object]) -> None:
        pass  # pragma: no cover


class _StubAdapterWithConfig:
    """Adapter stub that captures constructor config args."""

    received_config: dict[str, str]

    def __init__(self, **kwargs: str) -> None:
        _StubAdapterWithConfig.received_config = dict(kwargs)

    def matches(self, msg: object) -> bool:
        return True  # pragma: no cover

    def deliver(self, msg: object) -> None:
        pass  # pragma: no cover

    def on_stop(self, team_id: uuid.UUID) -> None:
        pass  # pragma: no cover


class TestChannelConfigPassthrough:
    """Verify ChannelConfig.config values reach adapter/parser constructors."""

    def test_config_dict_reaches_constructors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configure channel with config dict, verify it reaches constructors."""
        monkeypatch.setattr(
            "akgentic.infra.adapters.shared.channel_parser_registry.import_class",
            lambda fqcn: (
                _StubParserWithConfig if "Parser" in fqcn else _StubAdapterWithConfig
            ),
        )

        config = {
            "test-chan": ChannelConfig(
                parser_fqcn="fake.module.Parser",
                adapter_fqcn="fake.module.Adapter",
                config={"key": "value", "api_token": "secret123"},
            )
        }

        ChannelParserRegistry(channels_config=config)

        assert _StubParserWithConfig.received_config == {
            "key": "value",
            "api_token": "secret123",
        }
        assert _StubAdapterWithConfig.received_config == {
            "key": "value",
            "api_token": "secret123",
        }
