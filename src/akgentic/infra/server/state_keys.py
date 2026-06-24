"""Typed ``app.state`` key declarations for the server tier (ADR-030 §Decision 2).

Each module-level :class:`~akgentic.infra.utils.StateKey` constant pins one
server ``app.state`` slot's name, type, default, and required-ness. Declaring a
key *is* the registration — there is no central runtime-mutable registry. The
keys live in the package that writes the slot (``server/app.py``'s
``_store_state`` / ``_lifespan``) so the type contract and the producer stay
together and no import cycle is introduced.

The slot *names* are the exact attribute strings the current producers and
consumers use against ``app.state``, so a later consumer migration (Story 34.2)
targets byte-for-byte the same slot.
"""

from __future__ import annotations

from akgentic.infra.adapters.shared.channel_parser_registry import ChannelParserRegistry
from akgentic.infra.protocols.channels import ChannelRegistry, InteractionChannelIngestion
from akgentic.infra.server.deps import TierServices
from akgentic.infra.server.routes.frontend_adapter import FrontendAdapter
from akgentic.infra.server.routes.ws import ConnectionManager
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.settings import ServerSettings
from akgentic.infra.utils import StateKey

DRAINING: StateKey[bool] = StateKey("draining", default=False)

SERVICES: StateKey[TierServices] = StateKey("services", required=True)
TEAM_SERVICE: StateKey[TeamService] = StateKey("team_service", required=True)
SETTINGS: StateKey[ServerSettings] = StateKey("settings", required=True)
CONNECTION_MANAGER: StateKey[ConnectionManager] = StateKey("connection_manager", required=True)
CHANNEL_REGISTRY: StateKey[ChannelRegistry] = StateKey("channel_registry", required=True)
CHANNEL_PARSERS: StateKey[ChannelParserRegistry] = StateKey(
    "channel_parser_registry", required=True
)
INGESTION: StateKey[InteractionChannelIngestion] = StateKey("ingestion", required=True)

# Soft key — only set when a frontend adapter is configured.
FRONTEND_ADAPTER: StateKey[FrontendAdapter] = StateKey("frontend_adapter")
