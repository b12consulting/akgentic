"""CLI interface for team lifecycle management.

Provides the ``ak-team`` command for managing team instances from the
command line with list and inspect operations.

Requires the ``cli`` extra: ``pip install akgentic-team[cli]``.
"""

from __future__ import annotations

try:
    import typer as _typer  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "Typer is required. Install with: pip install akgentic-team[cli]"
    ) from exc

from akgentic.team.cli.main import app

__all__ = ["app"]
