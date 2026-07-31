"""Tests for port protocols: NullServiceRegistry conformance and behavior.

AC: 3, 5 — NullServiceRegistry satisfies ServiceRegistry via structural subtyping,
with all no-op implementations verified.
"""

from __future__ import annotations

import uuid

import pytest

from akgentic.team.ports import EventNotFoundError, NullServiceRegistry, ServiceRegistry


@pytest.fixture()
def registry() -> NullServiceRegistry:
    """Shared NullServiceRegistry instance for tests."""
    return NullServiceRegistry()


class TestEventNotFoundError:
    """Verify the exception's base class, which is load-bearing (ADR-21 §2)."""

    def test_is_a_lookup_error(self) -> None:
        """``EventNotFoundError`` is catchable as the standard "key not here" category."""
        assert issubclass(EventNotFoundError, LookupError)

    def test_is_not_a_value_error(self) -> None:
        """A stale cursor must not be swallowed by a corrupted-document handler.

        The YAML and Mongo backends both ``except ValueError`` to skip corrupt
        documents. If ``EventNotFoundError`` were a ``ValueError``, those
        handlers would silently absorb it and degrade the cursored read back
        into the full-log read the cursor exists to avoid.
        """
        assert not issubclass(EventNotFoundError, ValueError)

        with pytest.raises(EventNotFoundError):
            try:
                raise EventNotFoundError("stale cursor")
            except ValueError:  # pragma: no cover - must not catch
                pytest.fail("EventNotFoundError was caught by `except ValueError`")


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
