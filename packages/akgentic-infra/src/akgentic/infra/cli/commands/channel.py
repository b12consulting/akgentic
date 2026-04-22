"""``ak channel reload`` — signal the enterprise server to reload channels.

Implements the operator surface described in ADR-022 §D5 / §D6 — a single
subcommand that issues ``POST /admin/channels/reload`` against the active
profile's base URL and renders the outcome as a one-line summary.

Design decisions
----------------

* **Thin wire over ``_state.client``:** no ``httpx`` import here, and no
  direct HTTP-client construction. All HTTP traffic goes through the
  profile-driven client wired by the Typer callback (Story 22.5); auto-auth,
  conditional bearer injection, and fail-loud 401 behaviour are inherited
  unchanged.
* **No discovery endpoint:** ADR-022 §D5 rejects a
  ``/.well-known/deployment-tier`` probe. The 404 response on
  ``POST /admin/channels/reload`` is the only community-vs-enterprise
  signal; we surface the exact operator-facing message from the AC #5
  contract and exit 1.
* **No retry loop:** ADR-022 §D7 makes re-issue the operator's job. One
  underlying HTTP call per invocation; the auto-auth retry-once at 401
  lives at the HTTP-client layer and is out of scope here.
* **Registration style:** explicit ``register(app)`` helper mirrors
  Story 22.4's ``login`` / ``logout`` and Story 23.2's ``catalog`` — no
  import-time side effects on the top-level app beyond the single
  ``register(app)`` call in :mod:`akgentic.infra.cli.main`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import typer

from akgentic.infra.cli.client import ApiError

if TYPE_CHECKING:
    from akgentic.infra.cli.main import _State


# Exact operator-facing message from AC #5 — detection is solely via the 404
# response code. Keep as a module-level constant so tests can reference the
# same string (character-for-character identity is required).
_COMMUNITY_MESSAGE = (
    "`ak channel reload` requires an enterprise deployment; "
    "the current profile points at a community server."
)


def _current_state() -> _State:
    """Return the live ``_state`` from :mod:`akgentic.infra.cli.main`.

    Imported lazily to avoid a circular import between :mod:`main` (which
    calls ``channel.register(app)`` at import time) and this module.
    """
    from akgentic.infra.cli import main as main_module  # noqa: PLC0415

    return main_module._state


def _format_reload_summary(body: dict[str, Any]) -> str:
    """Render the success-path one-line summary from the reload response.

    Returns ``"Reloaded N channel(s): <names>"`` when ``body["channels"]``
    is a non-empty list of strings; otherwise falls back to the generic
    ``"Reloaded channels."`` message. Defensive: any deviation from the
    expected shape (``body`` not a dict, ``channels`` not a list, mixed
    element types) returns the generic message rather than raising.
    """
    if not isinstance(body, dict):
        return "Reloaded channels."
    channels = body.get("channels")
    if not isinstance(channels, list) or not channels:
        return "Reloaded channels."
    if not all(isinstance(name, str) for name in channels):
        return "Reloaded channels."
    return f"Reloaded {len(channels)} channel(s): {', '.join(channels)}"


def register(app: typer.Typer) -> None:
    """Register the ``channel`` command group on ``app``.

    Called from :mod:`akgentic.infra.cli.main`; tests may also call
    ``register`` against their own Typer instance to isolate the command
    group (mirrors the Story 22.4 / 23.2 registration pattern).
    """
    channel_app = typer.Typer(name="channel", help="Manage enterprise channels.")

    @channel_app.command("reload")
    def reload_cmd() -> None:
        """Tell the running enterprise server to reload channel definitions."""
        state = _current_state()
        try:
            body = state.client.reload_channels()
        except ApiError as exc:
            if exc.status_code == 404:
                typer.echo(_COMMUNITY_MESSAGE, err=True)
                raise typer.Exit(code=1) from exc
            typer.echo(f"HTTP {exc.status_code}: {exc.detail}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(_format_reload_summary(body))

    app.add_typer(channel_app, name="channel")


__all__ = ["register"]
