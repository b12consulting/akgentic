"""CLI interface for the Akgentic catalog.

Provides the ``ak-catalog`` command for managing unified catalog ``Entry``
rows from the command line.

Requires the ``cli`` extra: ``pip install akgentic-catalog[cli]``.
"""

from __future__ import annotations

try:
    import typer as _typer  # noqa: F401
except ImportError as exc:
    raise ImportError("Typer is required. Install with: pip install akgentic-catalog[cli]") from exc

from akgentic.catalog.cli.main import app

__all__ = ["app"]
