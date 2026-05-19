"""``ak catalog <kind> <verb>`` — CRUD over v2 unified catalog entries.

Implements the operator surface described in ADR-023 §D4 — five verbs
(``list``, ``get``, ``create``, ``update``, ``delete``) registered uniformly
over the v2 catalog kinds (``team``, ``agent``, ``tool``, ``model``,
``prompt``). The module is a **thin HTTP wire** against the admin-catalog
routes mounted under ``/admin/catalog/`` by the v2 unified router (ADR-023
§D1): it ships request bytes to the server unchanged (server is the single
validation point) and renders server responses verbatim under ``--format
json|yaml`` or projects selected columns under ``--format table``.

Design decisions
----------------

* **Registration style:** explicit ``register(app)`` helper mirrors
  ``login`` / ``logout`` commands — test-friendly, no import-time side
  effects on the top-level app.
* **Kind-generic:** one ``_register_kind_commands`` helper is applied once
  per v2 kind. Adding a sixth kind is a one-line addition.
* **Namespace is explicit:** per-entry routes in the v2 router require
  ``?namespace=<ns>``; the CLI surfaces this as a ``--namespace`` option on
  the per-entry verbs. On ``list`` the namespace filter is optional — if
  omitted the server returns entries across namespaces.
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

# -- per-kind table column projection -----------------------------------------

# The ONLY per-kind asymmetry in this module — projects a v2 Entry dict down
# to a small set of columns for the table renderer. JSON / YAML output always
# emits the full entry.
_COLUMNS_BY_KIND: dict[str, list[str]] = {
    "team": ["id", "namespace", "description"],
    "agent": ["id", "namespace", "description"],
    "tool": ["id", "namespace", "description"],
    "model": ["id", "namespace", "description"],
    "prompt": ["id", "namespace", "description"],
}

_KINDS: tuple[str, ...] = ("team", "agent", "tool", "model", "prompt")


# -- helpers ------------------------------------------------------------------


def _content_type_for_extension(path: Path) -> str:
    """Map a file extension to the HTTP ``Content-Type`` header value.

    Raises :class:`typer.Exit` with the exact stderr message if the extension
    is unsupported. Strict: no silent guessing.
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
      ``application/yaml``.
    """
    if file_path is not None:
        return file_path.read_bytes(), _content_type_for_extension(file_path)
    body = sys.stdin.buffer.read()
    content_type = "application/yaml" if fmt == OutputFormat.yaml else "application/json"
    return body, content_type


def _to_dict_or_list(data: object) -> object:
    """Pre-convert typed Pydantic entries to dicts for the formatter.

    The ``format_output`` formatter projects by string key (``row.get(col)``),
    so typed ``Entry`` values must be converted to dicts first. This
    preserves the formatter's public contract without rewriting it.
    """
    if isinstance(data, BaseModel):
        return data.model_dump()
    if isinstance(data, list):
        return [item.model_dump() if isinstance(item, BaseModel) else item for item in data]
    return data


def _render_entry(data: object, kind_name: str, fmt: OutputFormat) -> None:
    """Render a single entry body or a list of entry bodies."""
    columns = _COLUMNS_BY_KIND[kind_name]
    typer.echo(format_output(_to_dict_or_list(data), fmt, columns))


def _current_state() -> _State:
    """Return the live ``_state`` from :mod:`akgentic.infra.cli.main`.

    Imported lazily to avoid the circular import between ``main`` (which
    calls ``catalog.register(app)`` at import time) and this module.
    """
    from akgentic.infra.cli import main as main_module  # noqa: PLC0415

    return main_module._state


def _call_api[T](thunk: Callable[[], T]) -> T:
    """Run ``thunk`` translating :class:`ApiError` into a Typer exit."""
    try:
        return thunk()
    except ApiError as exc:
        typer.echo(f"HTTP {exc.status_code}: {exc.detail}", err=True)
        raise typer.Exit(code=1) from exc


def _render_delete(kind_name: str, entry_id: str, fmt: OutputFormat) -> None:
    """Render the delete confirmation (table or structured)."""
    if fmt == OutputFormat.table:
        typer.echo(f"Deleted {kind_name} '{entry_id}'.")
        return
    typer.echo(
        format_output(
            {"kind": kind_name, "id": entry_id, "status": "deleted"},
            fmt,
        )
    )


# -- per-kind command registration --------------------------------------------


def _register_kind_commands(parent_app: typer.Typer, kind_name: str) -> None:
    """Register the five CRUD verbs for ``kind_name`` on ``parent_app``.

    Attaches a new ``typer.Typer(name=kind_name)`` subgroup carrying ``list``,
    ``get``, ``create``, ``update``, ``delete`` under ``parent_app``.
    """
    kind_app = typer.Typer(name=kind_name, help=f"Manage {kind_name} catalog entries")

    @kind_app.command("list")
    def list_cmd(
        namespace: Annotated[
            str | None,
            typer.Option("--namespace", help="Optional namespace filter forwarded as ?namespace="),
        ] = None,
    ) -> None:
        state = _current_state()
        data = _call_api(lambda: state.client.admin_catalog_list(kind_name, namespace=namespace))
        _render_entry(data, kind_name, state.fmt)

    @kind_app.command("get")
    def get_cmd(
        entry_id: str,
        namespace: Annotated[
            str, typer.Option("--namespace", help="Namespace the entry lives in (required).")
        ],
    ) -> None:
        state = _current_state()
        data = _call_api(
            lambda: state.client.admin_catalog_get(kind_name, entry_id, namespace=namespace)
        )
        _render_entry(data, kind_name, state.fmt)

    @kind_app.command("create")
    def create_cmd(
        file: Annotated[
            Path | None, typer.Option("--file", help="Path to request body (.yaml|.yml|.json).")
        ] = None,
    ) -> None:
        state = _current_state()
        body, content_type = _read_body(file, state.fmt)
        data = _call_api(
            lambda: state.client.admin_catalog_create(kind_name, body, content_type)
        )
        _render_entry(data, kind_name, state.fmt)

    @kind_app.command("update")
    def update_cmd(
        entry_id: str,
        namespace: Annotated[
            str, typer.Option("--namespace", help="Namespace the entry lives in (required).")
        ],
        file: Annotated[
            Path | None, typer.Option("--file", help="Path to request body (.yaml|.yml|.json).")
        ] = None,
    ) -> None:
        state = _current_state()
        body, content_type = _read_body(file, state.fmt)
        data = _call_api(
            lambda: state.client.admin_catalog_update(
                kind_name, entry_id, body, content_type, namespace=namespace
            )
        )
        _render_entry(data, kind_name, state.fmt)

    @kind_app.command("delete")
    def delete_cmd(
        entry_id: str,
        namespace: Annotated[
            str, typer.Option("--namespace", help="Namespace the entry lives in (required).")
        ],
    ) -> None:
        state = _current_state()
        _call_api(
            lambda: state.client.admin_catalog_delete(kind_name, entry_id, namespace=namespace)
        )
        _render_delete(kind_name, entry_id, state.fmt)

    parent_app.add_typer(kind_app, name=kind_name)


# -- public entry point -------------------------------------------------------


def _register_namespace_commands(catalog_app: typer.Typer) -> None:
    """Register namespace-bundle commands directly on the ``catalog`` group.

    These operate on a whole namespace as a single YAML bundle, unlike the
    per-kind CRUD verbs. Today: ``import``.
    """

    @catalog_app.command("import")
    def import_cmd(
        file: Annotated[
            Path | None,
            typer.Option(
                "--file",
                help="Path to a namespace-bundle YAML (.yaml|.yml). Reads stdin when omitted.",
            ),
        ] = None,
    ) -> None:
        """Import a namespace-bundle YAML as an atomic namespace replacement."""
        state = _current_state()
        if file is not None and file.suffix.lower() not in {".yaml", ".yml"}:
            typer.echo(
                f"Unsupported file extension {file.suffix.lstrip('.')!r}; "
                "namespace import requires .yaml or .yml.",
                err=True,
            )
            raise typer.Exit(code=1)
        body = file.read_bytes() if file is not None else sys.stdin.buffer.read()
        data = _call_api(lambda: state.client.admin_catalog_import_namespace(body))
        typer.echo(format_output(_to_dict_or_list(data), state.fmt, _COLUMNS_BY_KIND["team"]))


def register(app: typer.Typer) -> None:
    """Register the ``catalog`` command group on ``app``.

    Called from :mod:`akgentic.infra.cli.main`; tests may also call
    ``register`` against their own Typer instance to isolate the command
    group.
    """
    catalog_app = typer.Typer(
        name="catalog",
        help="Manage v2 catalog entries (team, agent, tool, model, prompt).",
    )
    _register_namespace_commands(catalog_app)
    for kind_name in _KINDS:
        _register_kind_commands(catalog_app, kind_name)
    app.add_typer(catalog_app, name="catalog")


__all__ = ["register"]
