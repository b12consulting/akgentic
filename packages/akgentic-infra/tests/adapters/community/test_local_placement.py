"""Tests for LocalPlacement adapter."""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import MagicMock

from akgentic.infra.adapters.community.local_placement import LocalPlacement
from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.infra.protocols.placement import PlacementStrategy


def _make_adapter() -> LocalPlacement:
    """Create a LocalPlacement with mock dependencies."""
    team_manager = MagicMock()
    service_registry = MagicMock()
    return LocalPlacement(team_manager, service_registry)


class TestLocalPlacementProtocolCompliance:
    """AC5: LocalPlacement implements PlacementStrategy protocol."""

    def test_satisfies_placement_strategy_protocol(self) -> None:
        """LocalPlacement structurally satisfies PlacementStrategy."""
        adapter = _make_adapter()
        assert isinstance(adapter, PlacementStrategy)

    def test_has_create_team_method(self) -> None:
        """LocalPlacement exposes create_team with correct signature."""
        adapter = _make_adapter()
        assert callable(adapter.create_team)

    def test_create_team_signature_matches_protocol(self) -> None:
        """create_team has team_card and user_id parameters matching PlacementStrategy."""
        sig = inspect.signature(LocalPlacement.create_team)
        assert "team_card" in sig.parameters
        assert "user_id" in sig.parameters


class TestLocalPlacementBehavior:
    """AC5: LocalPlacement delegates to TeamManager and returns LocalTeamHandle."""

    def test_create_team_delegates_to_team_manager(self) -> None:
        """create_team calls TeamManager.create_team with correct args."""
        team_manager = MagicMock()
        service_registry = MagicMock()
        adapter = LocalPlacement(team_manager, service_registry)
        team_card = MagicMock()
        adapter.create_team(team_card, "user-1")
        team_manager.create_team.assert_called_once_with(team_card, "user-1")

    def test_create_team_returns_local_team_handle(self) -> None:
        """create_team wraps TeamManager result in LocalTeamHandle."""
        team_manager = MagicMock()
        service_registry = MagicMock()
        adapter = LocalPlacement(team_manager, service_registry)
        result = adapter.create_team(MagicMock(), "user-1")
        assert isinstance(result, LocalTeamHandle)

    def test_instance_id_is_stable(self) -> None:
        """instance_id does not change between calls."""
        adapter = _make_adapter()
        assert adapter.instance_id == adapter.instance_id

    def test_instance_id_is_uuid(self) -> None:
        """instance_id is a uuid.UUID."""
        adapter = _make_adapter()
        assert isinstance(adapter.instance_id, uuid.UUID)

    def test_different_instances_have_different_ids(self) -> None:
        """Two LocalPlacement instances have different instance_ids."""
        a = _make_adapter()
        b = _make_adapter()
        assert a.instance_id != b.instance_id
