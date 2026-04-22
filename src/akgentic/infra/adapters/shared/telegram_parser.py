"""TelegramChannelParser — parses Telegram Bot API webhook Updates into ChannelMessage."""

from __future__ import annotations

import logging

from akgentic.infra.protocols.channels import ChannelMessage, JsonValue

logger = logging.getLogger(__name__)


class TelegramChannelParser:
    """Parses inbound Telegram webhook Update payloads into normalized ChannelMessage.

    Satisfies the ``ChannelParser`` protocol via structural subtyping.

    Telegram sends Updates as JSON POST to the configured webhook URL.
    This parser extracts the text message content, chat ID (used as the
    channel user identifier), and message ID from the Update payload.

    Only text messages (``message.text``) are supported. Non-text updates
    (edited messages, channel posts, photos, etc.) raise ``ValueError``.

    Args:
        bot_token: Telegram Bot API token (unused by parser, but passed via
            ``ChannelConfig.config`` shared with the adapter).
        default_catalog_entry: Catalog entry ID for initiating new teams.
    """

    def __init__(self, default_catalog_entry: str = "default", **_kwargs: str) -> None:
        self._default_catalog_entry = default_catalog_entry

    @property
    def channel_name(self) -> str:
        """The channel name this parser handles."""
        return "telegram"

    @property
    def default_catalog_entry(self) -> str:
        """Default catalog entry ID for new team initiation."""
        return self._default_catalog_entry

    async def parse(self, payload: dict[str, JsonValue]) -> ChannelMessage:
        """Parse a Telegram Update payload into a ChannelMessage.

        Args:
            payload: Raw Telegram Update JSON. Expected structure::

                {
                    "update_id": 123456,
                    "message": {
                        "message_id": 42,
                        "chat": {"id": 987654321},
                        "text": "Hello"
                    }
                }

        Returns:
            Parsed ChannelMessage with content, chat ID, and message ID.

        Raises:
            ValueError: If the payload does not contain a text message.
        """
        message = payload.get("message")
        if not isinstance(message, dict):
            msg = "Telegram Update does not contain a 'message' field"
            raise ValueError(msg)

        text = message.get("text")
        if not isinstance(text, str):
            msg = "Telegram message does not contain a 'text' field"
            raise ValueError(msg)

        chat = message.get("chat")
        if not isinstance(chat, dict):
            msg = "Telegram message does not contain a 'chat' field"
            raise ValueError(msg)

        chat_id = chat.get("id")
        if chat_id is None:
            msg = "Telegram chat does not contain an 'id' field"
            raise ValueError(msg)

        message_id = message.get("message_id")

        logger.debug("Parsing Telegram update: chat_id=%s", chat_id)
        return ChannelMessage(
            content=text,
            channel_user_id=str(chat_id),
            message_id=str(message_id) if message_id is not None else None,
        )
