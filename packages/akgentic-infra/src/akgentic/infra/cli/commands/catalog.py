"""``ak catalog <entity> <verb>`` — CRUD over catalog entries.

Implements the operator surface described in ADR-022 §D4 / §D6 — five verbs
(``list``, ``get``, ``create``, ``update``, ``delete``) registered uniformly
over the four catalog entities (``templates``, ``tools``, ``agents``,
``teams``). The module is a **thin HTTP wire** against the admin-catalog
routes shipped by Story 23.1: it ships request bytes to the server unchanged
(server is the single validation point) and renders server responses verbatim
under ``--format json|yaml`` or projects selected columns under ``--format
table``.

Design decisions
----------------

* **Registration style:** explicit ``register(app)`` helper mirrors Story
  22.4's ``login`` / ``logout`` commands — test-friendly, no import-time
  side effects on the top-level app.
* **Generic over entity:** one ``_register_entity_commands`` helper is
  applied four times (one line per entity). Adding a fifth entity is a
  one-line addition. The only per-entity asymmetry lives in
  ``_COLUMNS_BY_ENTITY`` below.
* **``_state.client`` access:** the module imports
  :mod:`akgentic.infra.cli.main` lazily inside each command body. This
  avoids a circular import (``main`` imports ``catalog`` at module scope)
  and matches the ``ConnectionManager``-lazy-import pattern in
  ``main.py::chat``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import BaseModel

from akgentic.infra.cli.client import ApiError
from akgentic.infra.cli.formatters import OutputFormat, format_output

if TYPE_CHECKING:
    from akgentic.infra.cli.main import _State

# -- per-entity table column projection ---------------------------------------

# The ONLY per-entity asymmetry in this module. See AC #5.
_COLUMNS_BY_ENTITY: dict[str, list[str]] = {
    "templates": ["id", "placeholder"],
    "tools": ["id", "name"],
    "agents": ["id", "description"],
    "teams": ["id", "name", "description"],
}

_ENTITIES: tuple[str, ...] = ("templates", "tools", "agents", "teams")


# -- helpers ------------------------------------------------------------------


def _content_type_for_extension(path: Path) -> str:
    """Map a file extension to the HTTP ``Content-Type`` header value.

    Raises :class:`typer.Exit` with the exact stderr message from AC #4 if
    the extension is unsupported. Strict: no silent guessing.
    """
    ext = path.suffix.lower().lstrip(".")
    if ext in {"yaml", "yml"}:
        return "application/yaml"
    if ext == "json":
        return "application/json"
    typer.echo(
        f"Unsupported file extension {ext!r}; use .yaml, .yml, or .json.",
        err=True,
    )
    raise typer.Exit(code=1)


def _read_body(
    file_path: Path | None,
    fmt: OutputFormat,
) -> tuple[bytes, str]:
    """Load request bytes + ``Content-Type`` from ``--file`` or stdin.

    * ``--file`` present → bytes read from the file, content-type inferred
      from the extension (see :func:`_content_type_for_extension`).
    * ``--file`` absent → bytes read from stdin. Content type defaults to
      ``application/json``; ``--format yaml`` promotes it to
      ``application/yaml``. ``--format json`` is a no-op.
    """
    if file_path is not None:
        return file_path.read_bytes(), _content_type_for_extension(file_path)
    body = sys.stdin.buffer.read()
    content_type = "application/yaml" if fmt == OutputFormat.yaml else "application/json"
    return body, content_type


def _to_dict_or_list(data: object) -> object:
    """Pre-convert typed Pydantic entries to dicts for the formatter.

    The ``format_output`` formatter projects by string key (``row.get(col)``),
    so typed ``CatalogEntry`` values must be converted to dicts first. This
    preserves the formatter's public contract without rewriting it.
    """
    if isinstance(data, BaseModel):
        return data.model_dump()
    if isinstance(data, list):
        return [item.model_dump() if isinstance(item, BaseModel) else item for item in data]
    return data


def _render_entity(data: object, entity_name: str, fmt: OutputFormat) -> None:
    """Render a single entity body or a list of entity bodies."""
    columns = _COLUMNS_BY_ENTITY[entity_name]
    typer.echo(format_output(_to_dict_or_list(data), fmt, columns))


def _current_state() -> _State:
    """Return the live ``_state`` from :mod:`akgentic.infra.cli.main`.

    Imported lazily to avoid the circular import between ``main`` (which
    calls ``catalog.register(app)`` at import time) and this module.
    """
    from akgentic.infra.cli import main as main_module  # noqa: PLC0415

    return main_module._state


def _call_api[T](thunk: Callable[[], T]) -> T:
    """Run ``thunk`` translating :class:`ApiError` into a Typer exit.

    All five verbs share the identical ``try/except ApiError`` block required
    by AC #9 — this helper collapses them into one site so the per-verb
    functions stay well under the 50-line ceiling and free of duplication.
    """
    try:
        return thunk()
    except ApiError as exc:
        typer.echo(f"HTTP {exc.status_code}: {exc.detail}", err=True)
        raise typer.Exit(code=1) from exc


def _render_delete(entity_name: str, entry_id: str, fmt: OutputFormat) -> None:
    """Render the delete confirmation (table or structured)."""
    if fmt == OutputFormat.table:
        singular = entity_name.rstrip("s")
        typer.echo(f"Deleted {singular} '{entry_id}'.")
        return
    typer.echo(
        format_output(
            {"entity": entity_name, "id": entry_id, "status": "deleted"},
            fmt,
        )
    )


# -- per-entity command registration ------------------------------------------


def _register_entity_commands(parent_app: typer.Typer, entity_name: str) -> None:
    """Register the five CRUD verbs for ``entity_name`` on ``parent_app``.

    Public contract: attaches a new ``typer.Typer(name=entity_name)`` subgroup
    carrying ``list``, ``get``, ``create``, ``update``, ``delete`` under
    ``parent_app``. Exactly one subgroup is added per call; removing a call
    eliminates exactly that entity's subgroup (see AC #11 registration test).
    """
    entity_app = typer.Typer(name=entity_name, help=f"Manage {entity_name}")

    @entity_app.command("list")
    def list_cmd(
        q: Annotated[
            str | None, typer.Option("--q", help="Optional search query forwarded as ?q=")
        ] = None,
    ) -> None:
        state = _current_state()
        data = _call_api(lambda: state.client.admin_catalog_list(entity_name, q))
        _render_entity(data, entity_name, state.fmt)

    @entity_app.command("get")
    def get_cmd(entry_id: str) -> None:
        state = _current_state()
        data = _call_api(lambda: state.client.admin_catalog_get(entity_name, entry_id))
        _render_entity(data, entity_name, state.fmt)

    @entity_app.command("create")
    def create_cmd(
        file: Annotated[
            Path | None, typer.Option("--file", help="Path to request body (.yaml|.yml|.json).")
        ] = None,
    ) -> None:
        state = _current_state()
        body, content_type = _read_body(file, state.fmt)
        data = _call_api(lambda: state.client.admin_catalog_create(entity_name, body, content_type))
        _render_entity(data, entity_name, state.fmt)

    @entity_app.command("update")
    def update_cmd(
        entry_id: str,
        file: Annotated[
            Path | None, typer.Option("--file", help="Path to request body (.yaml|.yml|.json).")
        ] = None,
    ) -> None:
        state = _current_state()
        body, content_type = _read_body(file, state.fmt)
        data = _call_api(
            lambda: state.client.admin_catalog_update(entity_name, entry_id, body, content_type)
        )
        _render_entity(data, entity_name, state.fmt)

    @entity_app.command("delete")
    def delete_cmd(entry_id: str) -> None:
        state = _current_state()
        _call_api(lambda: state.client.admin_catalog_delete(entity_name, entry_id))
        _render_delete(entity_name, entry_id, state.fmt)

    parent_app.add_typer(entity_app, name=entity_name)


# -- public entry point -------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Register the ``catalog`` command group on ``app``.

    Called from :mod:`akgentic.infra.cli.main`; tests may also call
    ``register`` against their own Typer instance to isolate the command
    group.
    """
    catalog_app = typer.Typer(
        name="catalog",
        help="Manage catalog entries (templates, tools, agents, teams).",
    )
    for entity_name in _ENTITIES:
        _register_entity_commands(catalog_app, entity_name)
    app.add_typer(catalog_app, name="catalog")


__all__ = ["register"]
