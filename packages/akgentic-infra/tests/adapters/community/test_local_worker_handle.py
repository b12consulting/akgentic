"""Tests for LocalWorkerHandle adapter."""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import MagicMock

from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.infra.adapters.community.local_worker_handle import LocalWorkerHandle
from akgentic.infra.protocols.worker_handle import WorkerHandle


def _make_adapter() -> tuple[LocalWorkerHandle, MagicMock, MagicMock]:
    """Create a LocalWorkerHandle with mock dependencies, return adapter, team_manager, actor_system."""
    team_manager = MagicMock()
    service_registry = MagicMock()
    actor_system = MagicMock()
    return (
        LocalWorkerHandle(team_manager, service_registry, actor_system),
        team_manager,
        actor_system,
    )


class TestLocalWorkerHandleProtocolCompliance:
    """AC4: LocalWorkerHandle implements WorkerHandle protocol."""

    def test_satisfies_worker_handle_protocol(self) -> None:
        """LocalWorkerHandle structurally satisfies WorkerHandle."""
        adapter, _, _ = _make_adapter()
        assert isinstance(adapter, WorkerHandle)

    def test_has_all_protocol_methods(self) -> None:
        """LocalWorkerHandle exposes all 5 WorkerHandle methods."""
        adapter, _, _ = _make_adapter()
        for method in ("stop_team", "delete_team", "resume_team", "get_team", "stop_all"):
            assert callable(getattr(adapter, method))

    def test_stop_team_signature(self) -> None:
        """stop_team has team_id parameter."""
        sig = inspect.signature(LocalWorkerHandle.stop_team)
        assert "team_id" in sig.parameters

    def test_delete_team_signature(self) -> None:
        """delete_team has team_id parameter."""
        sig = inspect.signature(LocalWorkerHandle.delete_team)
        assert "team_id" in sig.parameters

    def test_resume_team_signature(self) -> None:
        """resume_team has team_id parameter."""
        sig = inspect.signature(LocalWorkerHandle.resume_team)
        assert "team_id" in sig.parameters

    def test_get_team_signature(self) -> None:
        """get_team has team_id parameter."""
        sig = inspect.signature(LocalWorkerHandle.get_team)
        assert "team_id" in sig.parameters

    def test_stop_all_signature(self) -> None:
        """stop_all takes no parameters (besides self)."""
        sig = inspect.signature(LocalWorkerHandle.stop_all)
        params = [p for p in sig.parameters if p != "self"]
        assert params == []


class TestLocalWorkerHandleBehavior:
    """AC4: LocalWorkerHandle delegates to TeamManager correctly."""

    def test_stop_team_delegates_to_team_manager(self) -> None:
        """stop_team calls TeamManager.stop_team with correct team_id."""
        adapter, tm, _ = _make_adapter()
        tid = uuid.uuid4()
        adapter.stop_team(tid)
        tm.stop_team.assert_called_once_with(tid)

    def test_delete_team_delegates_to_team_manager(self) -> None:
        """delete_team calls TeamManager.delete_team with correct team_id."""
        adapter, tm, _ = _make_adapter()
        tid = uuid.uuid4()
        adapter.delete_team(tid)
        tm.delete_team.assert_called_once_with(tid)

    def test_resume_team_delegates_to_team_manager(self) -> None:
        """resume_team calls TeamManager.resume_team with correct team_id."""
        adapter, tm, _ = _make_adapter()
        tid = uuid.uuid4()
        adapter.resume_team(tid)
        tm.resume_team.assert_called_once_with(tid)

    def test_resume_team_returns_local_team_handle(self) -> None:
        """resume_team wraps TeamManager result in LocalTeamHandle."""
        adapter, _, _ = _make_adapter()
        result = adapter.resume_team(uuid.uuid4())
        assert isinstance(result, LocalTeamHandle)

    def test_get_team_delegates_to_team_manager(self) -> None:
        """get_team calls TeamManager.get_team with correct team_id."""
        adapter, tm, _ = _make_adapter()
        tid = uuid.uuid4()
        adapter.get_team(tid)
        tm.get_team.assert_called_once_with(tid)

    def test_get_team_returns_team_manager_result(self) -> None:
        """get_team returns whatever TeamManager.get_team returns."""
        adapter, tm, _ = _make_adapter()
        sentinel = MagicMock()
        tm.get_team.return_value = sentinel
        result = adapter.get_team(uuid.uuid4())
        assert result is sentinel

    def test_get_team_returns_none_when_not_found(self) -> None:
        """get_team returns None when TeamManager returns None."""
        adapter, tm, _ = _make_adapter()
        tm.get_team.return_value = None
        result = adapter.get_team(uuid.uuid4())
        assert result is None


class TestStopAll:
    """ADR-015 Decision 2: stop_all() calls actor_system.shutdown() directly.

    Per ADR-015, stop_all() skips per-team graceful teardown and calls
    actor_system.shutdown() directly. Teams keep RUNNING status in the
    event store for resume on next server start.
    """

    def test_stop_all_does_not_call_stop_team(self) -> None:
        """stop_all() does NOT call team_manager.stop_team() (simplified path, AC5)."""
        adapter, tm, _actor_system = _make_adapter()
        adapter.stop_all()
        tm.stop_team.assert_not_called()

    def test_stop_all_calls_shutdown_with_no_arguments(self) -> None:
        """stop_all() calls ActorSystem.shutdown() with no explicit timeout."""
        adapter, _tm, actor_system = _make_adapter()
        adapter.stop_all()
        actor_system.shutdown.assert_called_once_with()
