"""Tests for the CLI import guard."""

from __future__ import annotations

import builtins
import importlib
import sys
from unittest.mock import patch


def test_import_guard_raises_when_typer_missing() -> None:
    """Importing cli package without typer raises ImportError with helpful message."""
    # Remove the cli module from cache so the import guard runs fresh
    mods_to_remove = [key for key in sys.modules if key.startswith("akgentic.catalog.cli")]
    for mod in mods_to_remove:
        del sys.modules[mod]

    original_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "typer":
            raise ImportError("No module named 'typer'")
        return original_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=mock_import):
        try:
            importlib.import_module("akgentic.catalog.cli")
            msg = "Expected ImportError was not raised"
            raise AssertionError(msg)
        except ImportError as exc:
            assert "pip install akgentic-catalog[cli]" in str(exc)

    # Restore the module so other tests aren't affected
    importlib.import_module("akgentic.catalog.cli")


def test_import_guard_passes_when_typer_installed() -> None:
    """Importing cli package succeeds when typer is installed."""
    from akgentic.catalog.cli import app

    assert app is not None
