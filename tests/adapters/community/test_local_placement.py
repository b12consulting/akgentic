"""Tests for LocalPlacement adapter."""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import MagicMock

import pytest
from akgentic.infra.adapters.community.local_placement import LocalPlacement
from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.infra.protocols.placement import (
    PlacementError,
    PlacementStrategy,
)


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
        """create_team has all parameters matching PlacementStrategy."""
        sig = inspect.signature(LocalPlacement.create_team)
        assert "team_card" in sig.parameters
        assert "user_id" in sig.parameters
        assert "user_email" in sig.parameters
        assert "team_id" in sig.parameters
        assert "catalog_namespace" in sig.parameters
        assert sig.parameters["catalog_namespace"].default is None


class TestLocalPlacementBehavior:
    """AC5: LocalPlacement delegates to TeamManager and returns LocalTeamHandle."""

    def test_create_team_delegates_to_team_manager(self) -> None:
        """create_team calls TeamManager.create_team forwarding all args."""
        team_manager = MagicMock()
        service_registry = MagicMock()
        adapter = LocalPlacement(team_manager, service_registry)
        team_card = MagicMock()
        adapter.create_team(team_card, "user-1")
        team_manager.create_team.assert_called_once_with(
            team_card, "user-1", user_email="", team_id=None, catalog_namespace=None
        )

    def test_create_team_forwards_catalog_namespace(self) -> None:
        """create_team forwards catalog_namespace to TeamManager.create_team."""
        team_manager = MagicMock()
        service_registry = MagicMock()
        adapter = LocalPlacement(team_manager, service_registry)
        team_card = MagicMock()
        adapter.create_team(team_card, "user-1", catalog_namespace="ns-abc")
        team_manager.create_team.assert_called_once_with(
            team_card, "user-1", user_email="", team_id=None, catalog_namespace="ns-abc"
        )

    def test_create_team_forwards_user_email_and_team_id(self) -> None:
        """create_team forwards caller-supplied user_email and team_id verbatim."""
        team_manager = MagicMock()
        service_registry = MagicMock()
        adapter = LocalPlacement(team_manager, service_registry)
        team_card = MagicMock()
        explicit_id = uuid.uuid4()
        adapter.create_team(team_card, "user-1", user_email="user@example.com", team_id=explicit_id)
        team_manager.create_team.assert_called_once_with(
            team_card,
            "user-1",
            user_email="user@example.com",
            team_id=explicit_id,
            catalog_namespace=None,
        )

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


class TestLocalPlacementCreateFailure:
    """AC12: a TeamManager.create_team failure surfaces as a PlacementError."""

    def test_create_team_failure_raises_placement_error(self) -> None:
        """A delegate exception is wrapped in PlacementError (a ServerError)."""
        team_manager = MagicMock()
        team_manager.create_team.side_effect = RuntimeError("boom")
        adapter = LocalPlacement(team_manager, MagicMock())
        with pytest.raises(PlacementError) as exc_info:
            adapter.create_team(MagicMock(), "user-1")
        # Wrapped, not re-raised verbatim: carries the placement HTTP mapping.
        assert exc_info.value.status_code == 503
        assert exc_info.value.__cause__ is not None

    def test_create_team_passes_through_placement_error(self) -> None:
        """An already-typed PlacementError propagates unchanged (not re-wrapped)."""
        original = PlacementError("already typed")
        team_manager = MagicMock()
        team_manager.create_team.side_effect = original
        adapter = LocalPlacement(team_manager, MagicMock())
        with pytest.raises(PlacementError) as exc_info:
            adapter.create_team(MagicMock(), "user-1")
        assert exc_info.value is original
