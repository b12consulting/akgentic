"""Validate public API exports from akgentic.team."""

from __future__ import annotations

import importlib

import akgentic.team


def test_version_is_exported() -> None:
    """__version__ is exported and is a string."""
    assert hasattr(akgentic.team, "__version__")
    assert isinstance(akgentic.team.__version__, str)


def test_all_is_a_list() -> None:
    """__all__ is a list."""
    assert hasattr(akgentic.team, "__all__")
    assert isinstance(akgentic.team.__all__, list)


def test_all_entries_are_importable() -> None:
    """Every name in __all__ is importable from akgentic.team."""
    for name in akgentic.team.__all__:
        assert hasattr(akgentic.team, name), f"{name} listed in __all__ but not importable"


def test_version_in_all() -> None:
    """__version__ is listed in __all__."""
    assert "__version__" in akgentic.team.__all__


def test_module_is_importable() -> None:
    """akgentic.team is importable as a module."""
    mod = importlib.import_module("akgentic.team")
    assert mod is not None
