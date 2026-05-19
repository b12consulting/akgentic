"""Tests for TelegramChannelParser."""

from __future__ import annotations

import pytest

from akgentic.infra.adapters.shared.telegram_parser import TelegramChannelParser

# ---------------------------------------------------------------------------
# Sample Telegram Update payloads
# ---------------------------------------------------------------------------

VALID_TEXT_UPDATE: dict = {
    "update_id": 123456789,
    "message": {
        "message_id": 42,
        "from": {"id": 111222333, "is_bot": False, "first_name": "Geoff"},
        "chat": {"id": 987654321, "type": "private"},
        "date": 1711800000,
        "text": "Hello, bot!",
    },
}

EDITED_MESSAGE_UPDATE: dict = {
    "update_id": 123456790,
    "edited_message": {
        "message_id": 42,
        "from": {"id": 111222333, "is_bot": False, "first_name": "Geoff"},
        "chat": {"id": 987654321, "type": "private"},
        "date": 1711800000,
        "edit_date": 1711800060,
        "text": "Hello, bot! (edited)",
    },
}

PHOTO_MESSAGE_UPDATE: dict = {
    "update_id": 123456791,
    "message": {
        "message_id": 43,
        "from": {"id": 111222333, "is_bot": False, "first_name": "Geoff"},
        "chat": {"id": 987654321, "type": "private"},
        "date": 1711800000,
        "photo": [{"file_id": "abc123", "width": 100, "height": 100}],
    },
}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestChannelName:
    """channel_name property returns 'telegram'."""

    def test_channel_name(self) -> None:
        parser = TelegramChannelParser()
        assert parser.channel_name == "telegram"


class TestDefaultCatalogEntry:
    """default_catalog_entry returns the configured value."""

    def test_default_value(self) -> None:
        parser = TelegramChannelParser()
        assert parser.default_catalog_entry == "default"

    def test_custom_value(self) -> None:
        parser = TelegramChannelParser(default_catalog_entry="my-team")
        assert parser.default_catalog_entry == "my-team"


# ---------------------------------------------------------------------------
# parse() — happy path
# ---------------------------------------------------------------------------


class TestParseValidTextMessage:
    """AC 1: Valid Telegram text Update → correct ChannelMessage."""

    @pytest.mark.asyncio
    async def test_extracts_content(self) -> None:
        parser = TelegramChannelParser()
        result = await parser.parse(VALID_TEXT_UPDATE)
        assert result.content == "Hello, bot!"

    @pytest.mark.asyncio
    async def test_extracts_channel_user_id(self) -> None:
        parser = TelegramChannelParser()
        result = await parser.parse(VALID_TEXT_UPDATE)
        assert result.channel_user_id == "987654321"

    @pytest.mark.asyncio
    async def test_extracts_message_id(self) -> None:
        parser = TelegramChannelParser()
        result = await parser.parse(VALID_TEXT_UPDATE)
        assert result.message_id == "42"

    @pytest.mark.asyncio
    async def test_team_id_is_none(self) -> None:
        parser = TelegramChannelParser()
        result = await parser.parse(VALID_TEXT_UPDATE)
        assert result.team_id is None


# ---------------------------------------------------------------------------
# parse() — error cases
# ---------------------------------------------------------------------------


class TestParseNoMessage:
    """AC 2: Update with no 'message' key raises ValueError."""

    @pytest.mark.asyncio
    async def test_edited_message_raises(self) -> None:
        parser = TelegramChannelParser()
        with pytest.raises(ValueError, match="does not contain a 'message' field"):
            await parser.parse(EDITED_MESSAGE_UPDATE)

    @pytest.mark.asyncio
    async def test_empty_payload_raises(self) -> None:
        parser = TelegramChannelParser()
        with pytest.raises(ValueError, match="does not contain a 'message' field"):
            await parser.parse({})


class TestParseNoText:
    """AC 2: Message without 'text' key raises ValueError."""

    @pytest.mark.asyncio
    async def test_photo_only_raises(self) -> None:
        parser = TelegramChannelParser()
        with pytest.raises(ValueError, match="does not contain a 'text' field"):
            await parser.parse(PHOTO_MESSAGE_UPDATE)


class TestParseMissingChat:
    """Message without 'chat' raises ValueError."""

    @pytest.mark.asyncio
    async def test_no_chat_raises(self) -> None:
        parser = TelegramChannelParser()
        payload: dict = {
            "update_id": 1,
            "message": {"message_id": 1, "text": "hello"},
        }
        with pytest.raises(ValueError, match="does not contain a 'chat' field"):
            await parser.parse(payload)
