"""Tests for port protocols: NullServiceRegistry conformance and behavior.

AC: 3, 5 — NullServiceRegistry satisfies ServiceRegistry via structural subtyping,
with all no-op implementations verified.
"""

from __future__ import annotations

import uuid

import pytest

from akgentic.team.ports import NullServiceRegistry, ServiceRegistry


@pytest.fixture()
def registry() -> NullServiceRegistry:
    """Shared NullServiceRegistry instance for tests."""
    return NullServiceRegistry()


class TestNullServiceRegistry:
    """Verify NullServiceRegistry satisfies ServiceRegistry protocol (AC: 3, 5)."""

    def test_satisfies_service_registry_protocol(self, registry: NullServiceRegistry) -> None:
        """NullServiceRegistry is recognized as a ServiceRegistry instance."""
        assert isinstance(registry, ServiceRegistry)

    def test_non_conforming_object_is_not_service_registry(self) -> None:
        """An object without the required methods is NOT a ServiceRegistry."""

        class NotARegistry:
            pass

        assert not isinstance(NotARegistry(), ServiceRegistry)

    def test_find_team_returns_none(self, registry: NullServiceRegistry) -> None:
        """find_team always returns None in single-process mode."""
        assert registry.find_team(uuid.uuid4()) is None

    def test_get_active_instances_returns_empty_list(self, registry: NullServiceRegistry) -> None:
        """get_active_instances always returns empty list in single-process mode."""
        assert registry.get_active_instances() == []

    def test_get_active_instances_returns_fresh_list(self, registry: NullServiceRegistry) -> None:
        """Each call returns a new list object, not shared mutable state."""
        first = registry.get_active_instances()
        second = registry.get_active_instances()
        assert first is not second

    def test_register_instance_is_noop(self, registry: NullServiceRegistry) -> None:
        """register_instance executes without error."""
        result = registry.register_instance(uuid.uuid4())
        assert result is None

    def test_deregister_instance_is_noop(self, registry: NullServiceRegistry) -> None:
        """deregister_instance executes without error."""
        result = registry.deregister_instance(uuid.uuid4())
        assert result is None

    def test_register_team_is_noop(self, registry: NullServiceRegistry) -> None:
        """register_team executes without error."""
        result = registry.register_team(uuid.uuid4(), uuid.uuid4())
        assert result is None

    def test_deregister_team_is_noop(self, registry: NullServiceRegistry) -> None:
        """deregister_team executes without error."""
        result = registry.deregister_team(uuid.uuid4(), uuid.uuid4())
        assert result is None
