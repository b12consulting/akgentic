"""``akgentic logout`` — clear the per-profile OIDC token cache.

Implements ADR-021 §Decision 2 — idempotent wipe of the per-profile token
cache. On an auth-enabled profile, removes the cache file (if present) and
emits a confirmation line. On a no-auth profile, emits a benign info line
to stdout and exits 0 — it is never an error to run ``logout`` against an
OSS profile.

Design decisions mirror :mod:`login`:

* ``register(app)`` helper invoked explicitly from
  :mod:`akgentic.infra.cli.main`.
* ``_CREDENTIALS_DIR_OVERRIDE`` and ``_CONFIG_PATH_OVERRIDE`` module-level
  test seams — monkeypatched in tests.
* Idempotency leans on :func:`delete_token_cache` from Story 21.3 — missing
  file is a no-op at the OS layer; this command distinguishes
  "cache existed, now removed" from "already logged out" purely for UX.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from akgentic.infra.cli.auth import delete_token_cache, load_token_cache
from akgentic.infra.cli.config import load_config, resolve_profile

_CREDENTIALS_DIR_OVERRIDE: Path | None = None
_CONFIG_PATH_OVERRIDE: Path | None = None


def _logout(profile_name_override: str | None = None) -> None:
    """Run the ``akgentic logout`` command body."""
    config = load_config(_CONFIG_PATH_OVERRIDE)
    profile_name, profile = resolve_profile(
        config,
        cli_profile=profile_name_override,
        env=os.environ,
    )

    if profile.auth is None:
        typer.echo(f"Profile {profile_name!r} has no auth configured; nothing to log out from.")
        return

    existing = load_token_cache(profile_name, credentials_dir=_CREDENTIALS_DIR_OVERRIDE)
    delete_token_cache(profile_name, credentials_dir=_CREDENTIALS_DIR_OVERRIDE)

    if existing is None:
        typer.echo(f"Already logged out of profile {profile_name!r}.")
    else:
        typer.echo(f"Logged out from profile {profile_name!r}.")


def register(app: typer.Typer) -> None:
    """Register the ``logout`` command on ``app``."""

    @app.command("logout")
    def logout(
        profile: str | None = typer.Option(
            None,
            "--profile",
            help="Profile name; overrides AKGENTIC_PROFILE and config default.",
        ),
    ) -> None:
        """Clear cached credentials for the active profile (idempotent)."""
        _logout(profile)


__all__ = ["register"]
