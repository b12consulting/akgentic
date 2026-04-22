"""Channel protocols — interaction channel abstractions for external communication."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import Field

from akgentic.core.utils.serializer import SerializableBaseModel

if TYPE_CHECKING:
    from akgentic.core.messages import SentMessage

# Recursive JSON-safe type for webhook payloads — replaces dict[str, Any].
JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class ChannelMessage(SerializableBaseModel):
    """Normalized message from an external interaction channel."""

    content: str = Field(description="Message content")
    channel_user_id: str = Field(description="Channel-specific user identifier")
    team_id: uuid.UUID | None = Field(default=None, description="Associated team ID")
    message_id: str | None = Field(default=None, description="Channel-specific message ID")


@runtime_checkable
class InteractionChannelAdapter(Protocol):
    """Delivers outbound messages to humans via an external channel.

    Implementations: WebAdapter, TwilioAdapter, SlackAdapter, etc.

    Threading constraint:
        ``deliver()`` runs inside a Pykka actor thread (called from
        ``InteractionChannelDispatcher.on_message``). Implementations
        must not block and must not perform unguarded async I/O.
        If async delivery is needed, use a thread-safe bridge (e.g.
        enqueue to a ``queue.Queue`` consumed by an asyncio task).
    """

    def matches(self, msg: SentMessage) -> bool:
        """Check if this adapter handles the given message.

        Args:
            msg: The outbound message to check.

        Returns:
            True if this adapter should deliver the message.
        """
        ...

    def deliver(self, msg: SentMessage) -> None:
        """Deliver an outbound message via this channel.

        Args:
            msg: The message to deliver.
        """
        ...

    def on_stop(self, team_id: uuid.UUID) -> None:
        """Clean up resources when a team stops.

        Args:
            team_id: The team being stopped.
        """
        ...


@runtime_checkable
class InteractionChannelIngestion(Protocol):
    """Routes inbound human replies from external channels to the correct team's UserProxy.

    Error contract:
        - ``route_reply()`` raises ``ValueError`` if ``team_id`` does not
          correspond to a running team.
        - ``initiate_team()`` raises ``EntryNotFoundError`` (from
          ``akgentic.catalog``) if ``catalog_entry_id`` is invalid.
        Callers (webhook routes) should catch and map to HTTP 404.
    """

    async def route_reply(
        self,
        team_id: uuid.UUID,
        content: str,
        original_message_id: str | None = None,
    ) -> None:
        """Route an inbound reply to an existing team.

        Args:
            team_id: Target team ID.
            content: Message content from the human.
            original_message_id: Optional ID of the message being replied to.

        Raises:
            ValueError: If team_id does not correspond to a running team.
        """
        ...

    async def initiate_team(
        self,
        content: str,
        channel_user_id: str,
        catalog_entry_id: str,
    ) -> uuid.UUID:
        """Create a new team and send the initial message.

        Args:
            content: Initial message content.
            channel_user_id: Channel-specific user identifier.
            catalog_entry_id: Catalog entry to use for team creation.

        Returns:
            The newly created team's ID.

        Raises:
            EntryNotFoundError: If catalog_entry_id is not found in catalog.
        """
        ...


@runtime_checkable
class ChannelParser(Protocol):
    """Parses channel-specific webhook payloads into a common ChannelMessage.

    Runs in FastAPI async context — uses async signatures.
    """

    @property
    def channel_name(self) -> str:
        """The channel name this parser handles (e.g. 'whatsapp', 'slack')."""
        ...

    @property
    def default_catalog_entry(self) -> str:
        """Default catalog entry ID to use when initiating a new team.

        Used when ``ChannelRegistry.find_team()`` returns ``None`` (no
        existing team for this channel user). The ingestion layer passes
        this value to ``initiate_team(catalog_entry_id=...)`` to create
        a new team from the channel's default template.
        """
        ...

    async def parse(self, payload: dict[str, JsonValue]) -> ChannelMessage:
        """Parse a raw webhook payload into a structured channel message.

        Args:
            payload: Raw webhook payload from the external channel.

        Returns:
            Parsed ChannelMessage with normalized fields.
        """
        ...


@runtime_checkable
class ChannelRegistry(Protocol):
    """Maps external channel users to active teams.

    Runs in FastAPI async context — uses async signatures.

    Implementations:

    - **Community** (``YamlChannelRegistry``): YAML file on disk.
    - **Department / Enterprise**: Redis or DB-backed registry for
      multi-instance deployments.
    """

    async def register(self, channel: str, channel_user_id: str, team_id: uuid.UUID) -> None:
        """Register a mapping from a channel user to a team.

        Args:
            channel: Channel name (e.g., "whatsapp", "slack").
            channel_user_id: Channel-specific user identifier.
            team_id: The team ID to associate.
        """
        ...

    async def find_team(self, channel: str, channel_user_id: str) -> uuid.UUID | None:
        """Find the team associated with a channel user.

        Args:
            channel: Channel name (e.g., "whatsapp", "slack").
            channel_user_id: Channel-specific user identifier.

        Returns:
            Team ID if a mapping exists, None otherwise.
        """
        ...

    async def deregister(self, channel: str, channel_user_id: str) -> None:
        """Remove the mapping for a channel user.

        Args:
            channel: Channel name (e.g., "whatsapp", "slack").
            channel_user_id: Channel-specific user identifier.
        """
        ...
