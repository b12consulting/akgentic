"""Story 28.1 AC #1, #2, #3: ``akgentic.infra.worker`` public surface contract.

Post-Epic 28, the surviving exports are exactly four symbols. The dead worker
shell (``create_worker_app``, ``WorkerLifecycle``, the ``services/`` package)
is gone — this test guards against regression if a contributor reintroduces it.
"""

from __future__ import annotations

import importlib

import pytest

import akgentic.infra.worker as worker_pkg


class TestWorkerPublicSurface:
    """The four-symbol contract on ``akgentic.infra.worker``."""

    def test_canonical_four_symbol_import_succeeds(self) -> None:
        """``WorkerSettings``, ``WorkerServices``, ``teams_router``, ``readiness_router`` import."""
        from akgentic.infra.worker import (  # noqa: PLC0415
            WorkerServices,
            WorkerSettings,
            readiness_router,
            teams_router,
        )

        assert WorkerSettings is not None
        assert WorkerServices is not None
        assert teams_router is not None
        assert readiness_router is not None

    def test_all_equals_sorted_four_symbol_list(self) -> None:
        """``__all__`` is exactly the four surviving symbols (sorted comparison)."""
        assert sorted(worker_pkg.__all__) == [
            "WorkerServices",
            "WorkerSettings",
            "readiness_router",
            "teams_router",
        ]

    def test_worker_lifecycle_is_not_an_attribute(self) -> None:
        """``WorkerLifecycle`` is gone — no attribute on ``akgentic.infra.worker``."""
        assert not hasattr(worker_pkg, "WorkerLifecycle")

    def test_create_worker_app_is_not_an_attribute(self) -> None:
        """``create_worker_app`` is gone — no attribute on ``akgentic.infra.worker``."""
        assert not hasattr(worker_pkg, "create_worker_app")

    def test_app_submodule_does_not_exist(self) -> None:
        """``akgentic.infra.worker.app`` is gone — the file was deleted."""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("akgentic.infra.worker.app")

    def test_services_subpackage_does_not_exist(self) -> None:
        """``akgentic.infra.worker.services`` is gone — the package was deleted."""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("akgentic.infra.worker.services")
