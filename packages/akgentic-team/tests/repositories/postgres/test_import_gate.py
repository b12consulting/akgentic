"""Import-gate tests for the Postgres backend (AC #8, #9).

Mirrors the Mongo gate test layout — simulates missing ``nagra`` via
``sys.modules`` patching so the real installed copy isn't disturbed.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest


class TestLazyImportGate:
    """AC #8/#9: postgres import gate raises on missing nagra; top-level team unaffected."""

    def test_postgres_import_raises_when_nagra_unavailable(self) -> None:
        """Simulating missing nagra triggers ImportError with install instructions."""
        mod_name = "akgentic.team.repositories.postgres"
        saved: dict[str, object] = {}
        for key in list(sys.modules):
            if key.startswith(mod_name):
                saved[key] = sys.modules.pop(key)
        nagra_saved: dict[str, object] = {}
        for key in list(sys.modules):
            if key.startswith("nagra"):
                nagra_saved[key] = sys.modules.pop(key)

        try:
            with patch.dict(sys.modules, {"nagra": None}):
                with pytest.raises(ImportError) as excinfo:
                    importlib.import_module(mod_name)
                message = str(excinfo.value)
                assert "nagra is required" in message
                assert "akgentic-team[postgres]" in message
        finally:
            sys.modules.update(saved)
            sys.modules.update(nagra_saved)

    def test_top_level_team_import_unaffected_when_nagra_absent(self) -> None:
        """Top-level ``akgentic.team`` and ``akgentic.team.ports`` must still import."""
        mod_name = "akgentic.team"
        ports_name = "akgentic.team.ports"
        postgres_name = "akgentic.team.repositories.postgres"

        saved: dict[str, object] = {}
        for key in list(sys.modules):
            if key == mod_name or key.startswith(f"{mod_name}."):
                saved[key] = sys.modules.pop(key)
        nagra_saved: dict[str, object] = {}
        for key in list(sys.modules):
            if key.startswith("nagra"):
                nagra_saved[key] = sys.modules.pop(key)

        try:
            with patch.dict(sys.modules, {"nagra": None}):
                importlib.import_module(mod_name)
                importlib.import_module(ports_name)
                # The postgres subpackage must still raise under the gate.
                with pytest.raises(ImportError):
                    importlib.import_module(postgres_name)
        finally:
            for key in list(sys.modules):
                if key == mod_name or key.startswith(f"{mod_name}."):
                    del sys.modules[key]
            sys.modules.update(saved)
            sys.modules.update(nagra_saved)

    def test_postgres_import_exports_expected_symbols_when_nagra_available(self) -> None:
        """When nagra is present the postgres subpackage re-exports the public API."""
        pytest.importorskip("nagra")

        from akgentic.team.repositories.postgres import (
            NagraEventStore,
            _ensure_schema_loaded,
            init_db,
        )

        assert callable(_ensure_schema_loaded)
        assert callable(init_db)
        assert NagraEventStore is not None
