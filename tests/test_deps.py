"""Tests for TierServices dependency injection container."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable
from typing import get_type_hints
from unittest.mock import MagicMock

import pytest
from akgentic.catalog.services import (
    AgentCatalog,
    TeamCatalog,
    TemplateCatalog,
    ToolCatalog,
)
from akgentic.team.models import AgentStateSnapshot, PersistedEvent, Process, TeamStatus
from akgentic.team.ports import EventStore

from akgentic.infra.protocols.auth import AuthStrategy
from akgentic.infra.protocols.channels import ChannelRegistry, InteractionChannelIngestion
from akgentic.infra.protocols.event_stream import EventStream
from akgentic.infra.protocols.placement import PlacementStrategy
from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.worker_handle import WorkerHandle
from akgentic.infra.server.deps import TierServices


class FakeEventStore:
    """Minimal EventStore-shaped class satisfying the protocol via structural subtyping.

    Does NOT inherit from EventStore -- validates that Pydantic accepts
    protocol-typed fields through structural subtyping when
    arbitrary_types_allowed=True and SkipValidation is used.
    """

    def save_event(self, event: PersistedEvent) -> None:
        """No-op stub."""

    def load_events(
        self, team_id: uuid.UUID, after_event_id: uuid.UUID | None = None
    ) -> list[PersistedEvent]:
        """Return empty list."""
        return []

    def save_team(self, process: Process) -> None:
        """No-op stub."""

    def load_team(self, team_id: uuid.UUID) -> Process | None:
        """Return None."""
        return None

    def delete_team(self, team_id: uuid.UUID) -> None:
        """No-op stub."""

    def save_agent_state(self, snapshot: AgentStateSnapshot) -> None:
        """No-op stub."""

    def list_teams(
        self, user_id: str | None = None, status: TeamStatus | None = None
    ) -> list[Process]:
        """Return empty list."""
        return []

    def get_max_sequence(self, team_id: uuid.UUID) -> int:
        """Return 0."""
        return 0

    def load_agent_states(self, team_id: uuid.UUID) -> list[AgentStateSnapshot]:
        """Return empty list."""
        return []


class TestTierServicesEventStoreProtocol:
    """AC6: TierServices accepts a MongoEventStore-shaped object via structural subtyping."""

    def test_tierservices_accepts_mongo_shaped_event_store(self) -> None:
        """TierServices construction succeeds with a fake EventStore implementation.

        The fake class does NOT inherit from EventStore -- it satisfies the
        protocol purely through structural subtyping, the same pattern used
        by MongoEventStore and YamlEventStore.
        """
        fake_store = FakeEventStore()

        services = TierServices(
            placement=MagicMock(spec=PlacementStrategy),
            worker_handle=MagicMock(spec=WorkerHandle),
            auth=MagicMock(spec=AuthStrategy),
            event_store=fake_store,
            runtime_cache=MagicMock(spec=RuntimeCache),
            event_stream=MagicMock(spec=EventStream),
            ingestion=MagicMock(spec=InteractionChannelIngestion),
            channel_registry=MagicMock(spec=ChannelRegistry),
            team_catalog=MagicMock(spec=TeamCatalog),
            agent_catalog=MagicMock(spec=AgentCatalog),
            tool_catalog=MagicMock(spec=ToolCatalog),
            template_catalog=MagicMock(spec=TemplateCatalog),
        )

        assert services.event_store is fake_store


def _parameter_shape(method: Callable[..., object]) -> list[tuple[str, str, object]]:
    """Return (name, kind, default) per parameter of ``method``, excluding ``self``.

    Annotations are deliberately left out: both modules use
    ``from __future__ import annotations``, so ``Signature`` carries them as
    bare strings and ``"str | None"`` would not compare equal to a resolved
    type. Type equality is asserted separately via ``get_type_hints``.
    """
    return [
        (param.name, param.kind.name, param.default)
        for param in inspect.signature(method).parameters.values()
        if param.name != "self"
    ]


_PROTOCOL_METHODS = sorted(
    name for name, member in vars(EventStore).items() if callable(member) and name[0] != "_"
)


class TestFakeEventStoreProtocolShape:
    """The fake's own surface, held against the EventStore protocol it claims to satisfy.

    Nothing else in this package can notice when it drifts: the protocol is not
    ``@runtime_checkable``, ``TierServices.event_store`` is ``SkipValidation``-typed
    so Pydantic validates nothing, and CI type-checks and lints ``src/`` only —
    the test tree is executed, never analysed. That gap let ``list_teams`` sit on
    a two-widenings-stale signature and ``load_events`` on a one-widening-stale
    one, both unnoticed. These assertions are the gate that was missing.
    """

    def test_list_teams_accepts_every_protocol_call_shape(self) -> None:
        """Every way the protocol allows ``list_teams`` to be called works on the fake."""
        store = FakeEventStore()

        assert store.list_teams() == []
        assert store.list_teams("u1") == []
        assert store.list_teams(user_id="u1") == []
        assert store.list_teams(status=TeamStatus.RUNNING) == []
        assert store.list_teams(user_id="u1", status=TeamStatus.RUNNING) == []

    @pytest.mark.parametrize("method_name", _PROTOCOL_METHODS)
    def test_method_signature_matches_the_protocol(self, method_name: str) -> None:
        """Parameter names, order, kinds, defaults and types match the port exactly.

        The call-shape test above cannot catch a swap of ``user_id`` and
        ``status`` — both are optional and both accept ``None``, so all five
        shapes still run — yet the order is load-bearing: it is what keeps
        existing positional callers of ``list_teams("u1")`` meaning what they
        say. Comparing against the live protocol rather than a transcribed
        signature is also what makes this test outlast the next widening.
        """
        fake_method = getattr(FakeEventStore, method_name)
        protocol_method = getattr(EventStore, method_name)

        assert _parameter_shape(fake_method) == _parameter_shape(protocol_method)
        assert get_type_hints(fake_method) == get_type_hints(protocol_method)

    def test_the_protocol_surface_was_actually_discovered(self) -> None:
        """The parametrized guard above is only real while the derivation finds methods.

        An empty parameter set is reported by pytest as SKIPPED, not as a
        failure, and the run still exits 0 — so the signature comparison could
        quietly stop covering anything and no gate would turn red. Break the
        derivation deliberately and this is the only test that notices.
        """
        assert _PROTOCOL_METHODS

    def test_the_fake_does_not_inherit_from_the_protocol(self) -> None:
        """Structural subtyping is what the fake exists to demonstrate.

        ``FakeEventStore``'s whole point is that a class which merely has the
        right shape is accepted for a protocol-typed field; making it a subclass
        would erase the thing under test while leaving every other assertion in
        this file green — the signature comparison included, since the overrides
        are unchanged. ``issubclass`` cannot be used here: ``EventStore`` is not
        ``@runtime_checkable``, so the check goes through the MRO instead.
        """
        assert EventStore not in FakeEventStore.__mro__
