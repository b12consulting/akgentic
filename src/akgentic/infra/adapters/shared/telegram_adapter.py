"""TelegramChannelAdapter — delivers outbound messages via the Telegram Bot API."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from akgentic.core.messages import SentMessage

logger = logging.getLogger(__name__)

_USER_ROLE = "UserProxy"


class TelegramChannelAdapter:
    """Delivers outbound agent messages to Telegram chats via the Bot API.

    Satisfies the ``InteractionChannelAdapter`` protocol via structural
    subtyping.

    ``matches()`` returns ``True`` when the ``SentMessage`` recipient has
    a role of ``"UserProxy"`` — indicating the message is destined for a
    human user. The architecture states: "checks if recipient is UserProxy
    and channel is configured."

    ``deliver()`` sends a synchronous POST to the Telegram ``sendMessage``
    endpoint. The ``chat_id`` is resolved from the recipient's ``name``
    attribute, which is expected to carry the channel user identifier
    (Telegram chat ID) for channel-initiated teams.

    Args:
        bot_token: Telegram Bot API token (from @BotFather).
        default_catalog_entry: Ignored by the adapter (used by parser only,
            but passed via shared ``ChannelConfig.config``).
    """

    def __init__(self, bot_token: str = "", **_kwargs: str) -> None:
        self._bot_token = bot_token
        self._client = httpx.Client(
            base_url=f"https://api.telegram.org/bot{bot_token}/",
            timeout=10.0,
        )

    def matches(self, msg: SentMessage) -> bool:
        """Check if this adapter should deliver the message.

        Returns True when the recipient's role is "UserProxy", indicating
        the message is headed to a human participant.

        Args:
            msg: The outbound message to check.

        Returns:
            True if the recipient is a UserProxy agent.
        """
        try:
            return msg.recipient.role == _USER_ROLE
        except Exception:  # noqa: BLE001
            return False

    def deliver(self, msg: SentMessage) -> None:
        """Deliver an outbound message to a Telegram chat.

        Posts to the Telegram ``sendMessage`` API. The ``chat_id`` is
        taken from the recipient's ``name`` field (the channel user ID
        set during team initiation).

        Logs errors without raising — delivery failures must not crash
        the caller.

        Args:
            msg: The message to deliver.
        """
        chat_id = msg.recipient.name
        text = getattr(msg.message, "content", None) or str(msg.message)
        logger.debug("Delivering message to Telegram chat %s", chat_id)

        try:
            response = self._client.post(
                "sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            if response.status_code != 200:
                logger.error(
                    "Telegram API error %d: %s",
                    response.status_code,
                    response.text,
                )
        except httpx.HTTPError:
            logger.exception("Failed to deliver message to Telegram chat %s", chat_id)

    def on_stop(self, team_id: uuid.UUID) -> None:
        """Clean up when a team stops.

        Args:
            team_id: The team being stopped.
        """
        logger.debug("TelegramAdapter stopped: team_id=%s", team_id)
        self._client.close()
