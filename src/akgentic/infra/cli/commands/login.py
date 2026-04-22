"""``akgentic login`` — warm the per-profile OIDC token cache.

Implements ADR-021 §Decision 1 / §Decision 2 — on an auth-enabled profile,
runs the device-code flow and persists the token cache; on a no-auth
profile, exits non-zero with an operator-actionable message and does NOT
contact the device-code endpoint or touch the filesystem.

Design decisions (documented per Story 21.4 "Decisions the Dev agent must
make"):

* **Registration style:** ``register(app)`` helper invoked explicitly from
  :mod:`akgentic.infra.cli.main` — explicit is test-friendly and keeps
  command registration a single-source-of-truth concern.
* **``credentials_dir`` test seam:** module-level constant
  ``_CREDENTIALS_DIR_OVERRIDE`` that tests monkeypatch. Keeps the Typer
  command signature clean (no hidden kwargs) and matches the Story 21.3
  "global-feeling but test-overridable filesystem root" pattern.
* **``config_path`` test seam:** module-level constant
  ``_CONFIG_PATH_OVERRIDE`` that tests monkeypatch. Production defers to
  :func:`load_config`'s ``~/.akgentic/config.yaml`` default.
* **User-code hook:** stderr writer (``typer.echo(..., err=True)``) — both
  ``sys.stderr.write`` and ``typer.echo(err=True)`` route to stderr; the
  latter integrates cleanly with Typer's output conventions.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import typer

from akgentic.infra.cli.auth import DeviceAuthorizationResponse, OidcTokenProvider
from akgentic.infra.cli.config import load_config, resolve_profile

# Test seams — monkeypatched to tmp_path in tests. Production leaves ``None``
# and the underlying ``load_config`` / cache helpers fall back to their own
# ``~/.akgentic/`` defaults.
_CREDENTIALS_DIR_OVERRIDE: Path | None = None
_CONFIG_PATH_OVERRIDE: Path | None = None
# Shared httpx.Client used by :class:`OidcTokenProvider` for discovery /
# device-auth / token calls. ``None`` in production — the provider then
# constructs its own default client. Tests wire an ``httpx.MockTransport``-
# backed client here so no real network I/O happens.
_HTTP_CLIENT_OVERRIDE: httpx.Client | None = None
# Sleep hook used by the poll loop. ``None`` in production → ``time.sleep``.
# Tests set to a no-op or a recorder to avoid real waits.
_SLEEP_OVERRIDE: Callable[[float], None] | None = None


def _emit_user_code(auth: DeviceAuthorizationResponse) -> None:
    """Write device-code instructions to stderr (AC #1, AC #7).

    Stdout stays clean so scripts piping ``akgentic login`` see only the
    final confirmation line.
    """
    message = (
        f"To authenticate, visit: {auth.verification_uri}\nAnd enter the code: {auth.user_code}\n"
    )
    if auth.verification_uri_complete:
        message += f"Or open directly: {auth.verification_uri_complete}\n"
    sys.stderr.write(message)
    sys.stderr.flush()


def _login(profile_name_override: str | None = None) -> None:
    """Run the ``akgentic login`` command body.

    Resolves the active profile using the documented precedence (``--profile``
    > ``AKGENTIC_PROFILE`` env var > config default > sole profile). On an
    auth-enabled profile, runs the OIDC device-code flow and persists the
    cache. On a no-auth profile, exits non-zero with an operator message
    and does NOT run device-code or write to the filesystem.
    """
    config = load_config(_CONFIG_PATH_OVERRIDE)
    profile_name, profile = resolve_profile(
        config,
        cli_profile=profile_name_override,
        env=os.environ,
    )

    if profile.auth is None:
        typer.echo(
            f"Profile {profile_name!r} has no auth configured; nothing to log in to. "
            f"Add an 'auth:' block to the profile in ~/.akgentic/config.yaml.",
            err=True,
        )
        raise typer.Exit(code=1)

    provider = OidcTokenProvider(
        profile,
        profile_name,
        http_client=_HTTP_CLIENT_OVERRIDE,
        sleep=_SLEEP_OVERRIDE,
        credentials_dir=_CREDENTIALS_DIR_OVERRIDE,
    )
    try:
        provider.run_device_code_flow(on_user_code=_emit_user_code)
    finally:
        # When tests inject ``_HTTP_CLIENT_OVERRIDE`` we do NOT own the
        # client; :meth:`OidcTokenProvider.close` respects that via its
        # internal ``_owns_client`` flag.
        provider.close()

    typer.echo(f"Logged in to profile {profile_name!r}.")


def register(app: typer.Typer) -> None:
    """Register the ``login`` command on ``app``.

    Called from :mod:`akgentic.infra.cli.main` at import time; tests can
    also call ``register`` against their own Typer instance to isolate the
    command.
    """

    @app.command("login")
    def login(
        profile: str | None = typer.Option(
            None,
            "--profile",
            help="Profile name; overrides AKGENTIC_PROFILE and config default.",
        ),
    ) -> None:
        """Authenticate with the active profile (OIDC device-code flow)."""
        _login(profile)


__all__ = ["register"]
