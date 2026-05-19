"""Tests for LocalRuntimeCache — community-tier RuntimeCache adapter."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from akgentic.team.models import Process, TeamStatus

from akgentic.infra.adapters.community.local_runtime_cache import LocalRuntimeCache
from akgentic.infra.protocols.runtime_cache import RuntimeCache


class TestLocalRuntimeCacheProtocolConformance:
    """AC2: LocalRuntimeCache satisfies the RuntimeCache protocol."""

    def test_isinstance_check(self) -> None:
        """isinstance(LocalRuntimeCache(), RuntimeCache) returns True."""
        cache = LocalRuntimeCache()
        assert isinstance(cache, RuntimeCache)


class TestLocalRuntimeCacheStartsEmpty:
    """AC2: cache starts empty."""

    def test_get_returns_none_for_any_id(self) -> None:
        """get() returns None for any team_id before store()."""
        cache = LocalRuntimeCache()
        assert cache.get(uuid.uuid4()) is None

    def test_get_returns_none_for_specific_id(self) -> None:
        """get() returns None for a specific UUID that was never stored."""
        cache = LocalRuntimeCache()
        team_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        assert cache.get(team_id) is None


class TestLocalRuntimeCacheStoreCycle:
    """AC2: store/get/remove lifecycle."""

    def test_store_then_get_returns_handle(self) -> None:
        """store(team_id, handle) followed by get(team_id) returns the handle."""
        cache = LocalRuntimeCache()
        team_id = uuid.uuid4()
        handle = MagicMock()
        cache.store(team_id, handle)
        assert cache.get(team_id) is handle

    def test_store_overwrites_previous(self) -> None:
        """Storing a new handle for an existing team_id overwrites the old one."""
        cache = LocalRuntimeCache()
        team_id = uuid.uuid4()
        handle1 = MagicMock()
        handle2 = MagicMock()
        cache.store(team_id, handle1)
        cache.store(team_id, handle2)
        assert cache.get(team_id) is handle2

    def test_remove_then_get_returns_none(self) -> None:
        """remove(team_id) followed by get(team_id) returns None."""
        cache = LocalRuntimeCache()
        team_id = uuid.uuid4()
        handle = MagicMock()
        cache.store(team_id, handle)
        cache.remove(team_id)
        assert cache.get(team_id) is None

    def test_remove_unknown_id_is_noop(self) -> None:
        """remove() for an unknown team_id does not raise."""
        cache = LocalRuntimeCache()
        cache.remove(uuid.uuid4())  # should not raise

    def test_multiple_teams_independent(self) -> None:
        """Multiple teams stored independently; removing one doesn't affect others."""
        cache = LocalRuntimeCache()
        id1, id2 = uuid.uuid4(), uuid.uuid4()
        h1, h2 = MagicMock(), MagicMock()
        cache.store(id1, h1)
        cache.store(id2, h2)
        cache.remove(id1)
        assert cache.get(id1) is None
        assert cache.get(id2) is h2


def _make_process(
    team_id: uuid.UUID, status: TeamStatus = TeamStatus.RUNNING
) -> Process:
    """Create a minimal Process fixture using model_construct to skip validation."""
    from datetime import UTC, datetime

    return Process.model_construct(
        team_id=team_id,
        team_card=MagicMock(),
        status=status,
        user_id="u1",
        user_email="",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


class TestLocalRuntimeCacheWarm:
    """warm() auto-restores running teams on startup."""

    def test_warm_restores_running_teams(self) -> None:
        """Running teams are stopped then resumed, handle stored in cache."""
        cache = LocalRuntimeCache()
        team_id = uuid.uuid4()
        handle = MagicMock()

        worker = MagicMock()
        worker.resume_team.return_value = handle

        event_store = MagicMock()
        event_store.list_teams.return_value = [_make_process(team_id, TeamStatus.RUNNING)]

        cache.warm(worker, event_store)

        worker.stop_team.assert_called_once_with(team_id)
        worker.resume_team.assert_called_once_with(team_id)
        assert cache.get(team_id) is handle

    def test_warm_skips_stopped_teams(self) -> None:
        """Stopped teams are not restored."""
        cache = LocalRuntimeCache()
        team_id = uuid.uuid4()

        worker = MagicMock()
        event_store = MagicMock()
        event_store.list_teams.return_value = [_make_process(team_id, TeamStatus.STOPPED)]

        cache.warm(worker, event_store)

        worker.stop_team.assert_not_called()
        worker.resume_team.assert_not_called()
        assert cache.get(team_id) is None

    def test_warm_skips_on_failure(self) -> None:
        """Failed restores are skipped, cache stays empty for that team."""
        cache = LocalRuntimeCache()
        team_id = uuid.uuid4()

        worker = MagicMock()
        worker.resume_team.side_effect = ValueError("broken")

        event_store = MagicMock()
        event_store.list_teams.return_value = [_make_process(team_id, TeamStatus.RUNNING)]

        cache.warm(worker, event_store)  # should not raise

        assert cache.get(team_id) is None

    def test_warm_no_running_teams(self) -> None:
        """No running teams → no calls to worker."""
        cache = LocalRuntimeCache()

        worker = MagicMock()
        event_store = MagicMock()
        event_store.list_teams.return_value = []

        cache.warm(worker, event_store)

        worker.stop_team.assert_not_called()
        worker.resume_team.assert_not_called()
