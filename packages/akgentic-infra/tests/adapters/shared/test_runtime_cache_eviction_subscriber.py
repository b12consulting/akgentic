"""Tests for RuntimeCacheEvictionSubscriber adapter.

Story 38.1: ``on_stop(team_id)`` evicts the stopping team's handle from the
worker's ``runtime_cache`` so eviction is path-independent (HTTP routes AND
the inactivity-timer auto-stop). The other three ``EventSubscriber`` hooks are
documented no-ops. ``remove`` is idempotent (double-evict is harmless).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import pytest
from akgentic.core.messages import Message
from akgentic.core.orchestrator import EventSubscriber
from akgentic.infra.adapters.community.local_runtime_cache import LocalRuntimeCache
from akgentic.infra.adapters.shared.runtime_cache_eviction_subscriber import (
    RuntimeCacheEvictionSubscriber,
)

if TYPE_CHECKING:
    from akgentic.infra.protocols.team_handle import TeamHandle

_TEAM_ID = uuid.uuid4()


class FakeRuntimeCache:
    """In-test fake RuntimeCache that records store/get/remove calls.

    Satisfies the ``RuntimeCache`` Protocol structurally. ``remove`` optionally
    raises to exercise the exception-swallow path in ``on_stop``.
    """

    def __init__(self, *, remove_raises: bool = False) -> None:
        self.removed: list[uuid.UUID] = []
        self.stored: list[uuid.UUID] = []
        self.got: list[uuid.UUID] = []
        self._remove_raises = remove_raises

    def store(self, team_id: uuid.UUID, handle: TeamHandle) -> None:  # noqa: ARG002
        self.stored.append(team_id)

    def get(self, team_id: uuid.UUID) -> TeamHandle | None:
        self.got.append(team_id)
        return None

    def remove(self, team_id: uuid.UUID) -> None:
        self.removed.append(team_id)
        if self._remove_raises:
            raise RuntimeError("remove failed")


class TestProtocolCompliance:
    """AC #2: RuntimeCacheEvictionSubscriber structurally satisfies EventSubscriber."""

    def test_satisfies_event_subscriber_protocol(self) -> None:
        subscriber: EventSubscriber = RuntimeCacheEvictionSubscriber(
            runtime_cache=FakeRuntimeCache()
        )
        assert callable(subscriber.set_restoring)
        assert callable(subscriber.on_stop_request)
        assert callable(subscriber.on_stop)
        assert callable(subscriber.on_message)


class TestOnStop:
    """AC #4, #5, #6: ``on_stop(team_id)`` evicts the stopping team."""

    def test_on_stop_removes_supplied_team_once(self) -> None:
        """AC #4: ``on_stop(team)`` calls ``remove(team)`` exactly once."""
        cache = FakeRuntimeCache()
        subscriber = RuntimeCacheEvictionSubscriber(runtime_cache=cache)
        team_id = uuid.uuid4()

        subscriber.on_stop(team_id)

        assert cache.removed == [team_id]

    def test_on_stop_twice_is_idempotent(self) -> None:
        """AC #5: two ``on_stop`` calls for the same team do not raise.

        Asserted against the real ``LocalRuntimeCache`` whose ``remove`` pops
        with a default, so the second remove is a genuine no-op.
        """
        cache = LocalRuntimeCache()
        subscriber = RuntimeCacheEvictionSubscriber(runtime_cache=cache)
        team_id = uuid.uuid4()

        subscriber.on_stop(team_id)
        subscriber.on_stop(team_id)

        assert cache.get(team_id) is None

    def test_on_stop_swallows_remove_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        """AC #6: a ``remove`` that raises is swallowed and logged at DEBUG."""
        cache = FakeRuntimeCache(remove_raises=True)
        subscriber = RuntimeCacheEvictionSubscriber(runtime_cache=cache)
        team_id = uuid.uuid4()

        with caplog.at_level(
            logging.DEBUG,
            logger="akgentic.infra.adapters.shared.runtime_cache_eviction_subscriber",
        ):
            result = subscriber.on_stop(team_id)

        assert result is None
        assert cache.removed == [team_id]
        assert len(caplog.records) >= 1


class TestNoOps:
    """AC #7: ``set_restoring``/``on_stop_request``/``on_message`` are no-ops."""

    def test_set_restoring_is_noop(self) -> None:
        cache = FakeRuntimeCache()
        subscriber = RuntimeCacheEvictionSubscriber(runtime_cache=cache)

        assert subscriber.set_restoring(_TEAM_ID, True) is None
        assert subscriber.set_restoring(_TEAM_ID, False) is None

        assert cache.removed == []
        assert cache.stored == []
        assert cache.got == []

    def test_on_stop_request_is_noop(self) -> None:
        cache = FakeRuntimeCache()
        subscriber = RuntimeCacheEvictionSubscriber(runtime_cache=cache)

        assert subscriber.on_stop_request(_TEAM_ID) is None

        assert cache.removed == []
        assert cache.stored == []
        assert cache.got == []

    def test_on_message_is_noop(self) -> None:
        cache = FakeRuntimeCache()
        subscriber = RuntimeCacheEvictionSubscriber(runtime_cache=cache)
        msg = Message(team_id=_TEAM_ID)

        assert subscriber.on_message(msg) is None

        assert cache.removed == []
        assert cache.stored == []
        assert cache.got == []
