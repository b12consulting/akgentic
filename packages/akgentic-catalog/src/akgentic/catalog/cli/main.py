"""Typer CLI for the unified catalog — ``ak-catalog <kind> <verb>``.

Single flat CLI module exposing a Typer ``app`` mounted on the ``ak-catalog``
console-script entry point; every verb is a thin dispatcher into
:class:`akgentic.catalog.catalog.Catalog` — no business logic lives here.

The CLI supports three storage backends: YAML (default), MongoDB, and
PostgreSQL. The Postgres path honours ADR-011's wiring contract — the
``--postgres-conn-string`` flag is bound to the ``DB_CONN_STRING_PERSISTENCE``
environment variable via Typer's ``envvar=`` resolution so operators have
two channels with flag-wins semantics. Navigation-only reference.
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any, Literal

import typer
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from rich.console import Console
from rich.table import Table

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.models.entry import Entry, EntryKind
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import CloneRequest, EntryQuery
from akgentic.catalog.repositories.base import EntryRepository
from akgentic.catalog.resolver import enumerate_allowlisted_model_types, load_model_type
from akgentic.catalog.validation import EntryValidationIssue, NamespaceValidationReport

__all__ = ["app"]

_list = builtins.list  # Alias kept because ``list`` is a Typer verb name.

_stdout = Console()
err_console = Console(stderr=True)


# --------------------------------------------------------------------------- #
# CLI state
# --------------------------------------------------------------------------- #


class CliState(BaseModel):
    """Global CLI state stashed on ``ctx.obj`` by the root callback."""

    model_config = ConfigDict(arbitrary_types_allowed=False)

    backend: Literal["yaml", "mongo", "postgres"] = "yaml"
    root: Path = Path("./catalog")
    uri: str | None = None
    db: str | None = None
    postgres_conn_string: str | None = None
    output_format: Literal["table", "json", "yaml"] = "table"


# --------------------------------------------------------------------------- #
# Typer app + global callback
# --------------------------------------------------------------------------- #


app = typer.Typer(
    name="ak-catalog",
    help="Unified Akgentic catalog CLI (v2) — manage entries across kinds.",
    no_args_is_help=True,
)


def _validate_mongo_options(state: CliState) -> None:
    """Guard mongo-only options; raises ``typer.Exit(code=2)`` on failure."""
    if state.backend != "mongo":
        return
    if state.uri is None:
        err_console.print("--uri is required when --backend=mongo")
        raise typer.Exit(code=2)
    if state.db is None:
        err_console.print("--db is required when --backend=mongo")
        raise typer.Exit(code=2)
    if not (state.uri.startswith("mongodb://") or state.uri.startswith("mongodb+srv://")):
        err_console.print("--uri must start with 'mongodb://' or 'mongodb+srv://'")
        raise typer.Exit(code=2)


def _validate_postgres_options(state: CliState) -> None:
    """Guard postgres-only options; raises ``typer.Exit(code=2)`` on failure."""
    if state.backend != "postgres":
        return
    if state.postgres_conn_string is None:
        err_console.print(
            "--postgres-conn-string is required when --backend=postgres "
            "(or set DB_CONN_STRING_PERSISTENCE env var)"
        )
        raise typer.Exit(code=2)


@app.callback()
def _root(
    ctx: typer.Context,
    backend: str = typer.Option(
        "yaml",
        "--backend",
        help="Storage backend: yaml, mongo, or postgres.",
        case_sensitive=False,
    ),
    root: Path = typer.Option(
        Path("./catalog"),
        "--root",
        help="Root directory for YAML entries (only when --backend=yaml).",
    ),
    uri: str | None = typer.Option(
        None,
        "--uri",
        help="MongoDB connection URI (required when --backend=mongo).",
    ),
    db: str | None = typer.Option(
        None,
        "--db",
        help="MongoDB database name (required when --backend=mongo).",
    ),
    postgres_conn_string: str | None = typer.Option(
        None,
        "--postgres-conn-string",
        envvar="DB_CONN_STRING_PERSISTENCE",
        help=(
            "Postgres DSN (required when --backend=postgres). "
            "Falls back to DB_CONN_STRING_PERSISTENCE env var."
        ),
    ),
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table, json, or yaml.",
        case_sensitive=False,
    ),
) -> None:
    """Parse global options and stash a ``CliState`` on ``ctx.obj``."""
    if backend not in ("yaml", "mongo", "postgres"):
        err_console.print(f"Invalid backend '{backend}'. Must be 'yaml', 'mongo', or 'postgres'.")
        raise typer.Exit(code=2)
    if output_format not in ("table", "json", "yaml"):
        err_console.print(f"Invalid format '{output_format}'. Must be 'table', 'json', or 'yaml'.")
        raise typer.Exit(code=2)

    state = CliState(
        backend=backend,  # type: ignore[arg-type]
        root=root,
        uri=uri,
        db=db,
        postgres_conn_string=postgres_conn_string,
        output_format=output_format,  # type: ignore[arg-type]
    )
    _validate_mongo_options(state)
    _validate_postgres_options(state)
    ctx.obj = state


# --------------------------------------------------------------------------- #
# Backend wiring
# --------------------------------------------------------------------------- #


def _build_catalog(state: CliState) -> Catalog:
    """Construct a ``Catalog`` from the active ``CliState``.

    YAML: creates ``state.root`` if absent and wraps a ``YamlEntryRepository``.

    Mongo: lazy-imports the Mongo repository so YAML-only users never trip a
    missing ``pymongo``. A missing extra is re-surfaced as an "optional extra"
    usage error (exit 2) rather than a cryptic ``ImportError``.

    Postgres: lazy-imports :class:`PostgresEntryRepository`; a missing
    ``[postgres]`` extra is re-surfaced as an "optional extra" usage error
    (exit 2), mirroring the Mongo branch shape.
    """
    if state.backend == "yaml":
        from akgentic.catalog.repositories.yaml import YamlEntryRepository

        state.root.mkdir(parents=True, exist_ok=True)
        return Catalog(YamlEntryRepository(state.root))

    if state.backend == "mongo":
        # options pre-validated by the root callback.
        try:
            from akgentic.catalog.repositories.mongo import (
                MongoCatalogConfig,
                MongoEntryRepository,
            )
        except ImportError:
            err_console.print(
                "--backend=mongo requires the 'mongo' optional extra: "
                "pip install akgentic-catalog[mongo]"
            )
            raise typer.Exit(code=2) from None

        assert state.uri is not None  # guarded by _validate_mongo_options
        assert state.db is not None
        config = MongoCatalogConfig(connection_string=state.uri, database=state.db)
        client = config.create_client()
        collection = config.get_collection(client, config.catalog_entries_collection)
        return Catalog(MongoEntryRepository(collection))

    # state.backend == "postgres" — options pre-validated by the root callback.
    try:
        from akgentic.catalog.repositories.postgres import PostgresEntryRepository
    except ImportError:
        err_console.print(
            "--backend=postgres requires the 'postgres' optional extra: "
            "pip install akgentic-catalog[postgres]"
        )
        raise typer.Exit(code=2) from None

    assert state.postgres_conn_string is not None  # guarded by _validate_postgres_options
    return Catalog(PostgresEntryRepository(state.postgres_conn_string))


def _repo_from_ctx(ctx: typer.Context) -> Catalog:
    """Helper: pull ``CliState`` off the context and build a Catalog."""
    state = ctx.obj
    assert isinstance(state, CliState), "CliState missing from ctx.obj"
    return _build_catalog(state)


def _state_from_ctx(ctx: typer.Context) -> CliState:
    state = ctx.obj
    assert isinstance(state, CliState), "CliState missing from ctx.obj"
    return state


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _truncate(text: str, max_len: int = 60) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _render_entries(entries: _list[Entry], fmt: str) -> None:
    """Render a list of entries to stdout per ``fmt``."""
    if fmt == "json":
        _stdout.print(json.dumps([e.model_dump(mode="json") for e in entries], indent=2))
        return
    if fmt == "yaml":
        payload = [e.model_dump(mode="json") for e in entries]
        _stdout.print(yaml.safe_dump(payload, sort_keys=False).rstrip("\n"))
        return
    # table
    if not entries:
        _stdout.print("(no entries)")
        return
    table = Table(title="entries")
    for col in ("id", "namespace", "kind", "user_id", "description"):
        table.add_column(col)
    for e in entries:
        table.add_row(
            e.id,
            e.namespace,
            e.kind,
            e.user_id or "",
            _truncate(e.description or ""),
        )
    _stdout.print(table)


def _render_entry(entry: Entry, fmt: str) -> None:
    """Render a single entry to stdout per ``fmt``."""
    if fmt == "json":
        _stdout.print(json.dumps(entry.model_dump(mode="json"), indent=2))
        return
    if fmt == "yaml":
        _stdout.print(yaml.safe_dump(entry.model_dump(mode="json"), sort_keys=False).rstrip("\n"))
        return
    table = Table(title=f"entry {entry.id}", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    rows = (
        ("id", entry.id),
        ("kind", entry.kind),
        ("namespace", entry.namespace),
        ("user_id", entry.user_id or ""),
        ("model_type", entry.model_type),
        ("description", entry.description or ""),
    )
    for key, value in rows:
        table.add_row(key, value)
    _stdout.print(table)
    payload_json = json.dumps(entry.model_dump(mode="json")["payload"], indent=2)
    if len(payload_json) > 4096:
        kept = payload_json[:4096]
        suffix = len(payload_json) - 4096
        _stdout.print(f"{kept}… ({suffix} more chars)")
    else:
        _stdout.print(payload_json)


# --------------------------------------------------------------------------- #
# Verb helpers
# --------------------------------------------------------------------------- #


def _parse_tri_state(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    err_console.print(f"--user-id-set must be 'true' or 'false'; got {value!r}")
    raise typer.Exit(code=2)


def _list_impl(
    ctx: typer.Context,
    kind: EntryKind,
    namespace: str | None,
    user_id: str | None,
    user_id_set: str | None,
) -> None:
    tri = _parse_tri_state(user_id_set)
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    query = EntryQuery(
        kind=kind,
        namespace=namespace,
        user_id=user_id,
        user_id_set=tri,
    )
    entries = catalog.list(query)
    _render_entries(entries, state.output_format)


def _get_impl(ctx: typer.Context, kind: EntryKind, id: str, namespace: str) -> None:
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    try:
        entry = catalog.get(namespace, id)
    except EntryNotFoundError:
        err_console.print(f"Entry ({namespace}, {id}, {kind}) not found")
        raise typer.Exit(code=1) from None
    if entry.kind != kind:
        err_console.print(
            f"Entry at (namespace={namespace}, id={id}) has kind={entry.kind}, expected {kind}"
        )
        raise typer.Exit(code=1)
    _render_entry(entry, state.output_format)


def _load_entry_yaml(path: Path) -> Entry:
    if not path.is_file():
        err_console.print(f"file not found: {path}")
        raise typer.Exit(code=2)
    try:
        text = path.read_text()
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        err_console.print(f"YAML parse error: {exc}")
        raise typer.Exit(code=2) from None
    if not isinstance(data, dict):
        err_console.print(f"YAML file must contain a mapping; got {type(data).__name__}")
        raise typer.Exit(code=2)
    try:
        return Entry(**data)
    except ValidationError as exc:
        err_console.print(f"entry validation error: {exc}")
        raise typer.Exit(code=2) from None


def _create_impl(ctx: typer.Context, kind: EntryKind, yaml_file: Path) -> None:
    entry = _load_entry_yaml(yaml_file)
    if entry.kind != kind:
        err_console.print(f"entry kind={entry.kind} does not match sub-app kind={kind}")
        raise typer.Exit(code=2)
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    try:
        saved = catalog.create(entry)
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(f"validation error: {err}")
        raise typer.Exit(code=1) from None
    _render_entry(saved, state.output_format)


def _update_impl(ctx: typer.Context, kind: EntryKind, yaml_file: Path) -> None:
    entry = _load_entry_yaml(yaml_file)
    if entry.kind != kind:
        err_console.print(f"entry kind={entry.kind} does not match sub-app kind={kind}")
        raise typer.Exit(code=2)
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    try:
        saved = catalog.update(entry)
    except EntryNotFoundError as exc:
        err_console.print(str(exc))
        raise typer.Exit(code=1) from None
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(f"validation error: {err}")
        raise typer.Exit(code=1) from None
    _render_entry(saved, state.output_format)


def _delete_impl(ctx: typer.Context, kind: EntryKind, id: str, namespace: str) -> None:
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    try:
        catalog.delete(namespace, id)
    except EntryNotFoundError as exc:
        err_console.print(str(exc))
        raise typer.Exit(code=1) from None
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(str(err))
        _surface_referrers_if_any(catalog, namespace, id, state.output_format)
        raise typer.Exit(code=1) from None
    err_console.print(f"deleted {kind} {id} from namespace {namespace}")


def _surface_referrers_if_any(catalog: Catalog, namespace: str, id: str, fmt: str) -> None:
    """Render referrers of (namespace, id) if any — best-effort, no raise."""
    try:
        referrers = catalog.find_references(namespace, id)
    except Exception:  # pragma: no cover - defensive
        return
    if referrers:
        _render_entries(referrers, fmt)


def _search_impl(
    ctx: typer.Context,
    kind: EntryKind,
    namespace: str | None,
    user_id: str | None,
    user_id_set: str | None,
    description_contains: str | None,
    id: str | None,
) -> None:
    tri = _parse_tri_state(user_id_set)
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    query = EntryQuery(
        kind=kind,
        namespace=namespace,
        user_id=user_id,
        user_id_set=tri,
        description_contains=description_contains,
        id=id,
    )
    entries = catalog.list(query)
    _render_entries(entries, state.output_format)


def _not_implemented_repo_handler(exc: Exception) -> None:  # pragma: no cover
    """Safety net for NotImplementedError from degraded repositories."""
    err_console.print(f"repository does not support this operation: {exc}")


def _ensure_repo(_: EntryRepository) -> None:  # pragma: no cover - hook placeholder
    """Placeholder helper to keep EntryRepository imported for type use."""


# --------------------------------------------------------------------------- #
# Per-kind sub-apps — one Typer() per EntryKind, registered on app
# --------------------------------------------------------------------------- #


def _make_kind_app(kind: EntryKind) -> typer.Typer:
    """Build a Typer sub-app with the six verbs bound to ``kind``."""
    sub = typer.Typer(name=kind, help=f"Manage '{kind}' entries.", no_args_is_help=True)

    @sub.command("list")
    def _list_cmd(
        ctx: typer.Context,
        namespace: str | None = typer.Option(None, "--namespace"),
        user_id: str | None = typer.Option(None, "--user-id"),
        user_id_set: str | None = typer.Option(None, "--user-id-set"),
    ) -> None:
        """List entries of this kind matching the provided filters."""
        _list_impl(
            ctx,
            kind,
            namespace=namespace,
            user_id=user_id,
            user_id_set=user_id_set,
        )

    @sub.command("get")
    def _get_cmd(
        ctx: typer.Context,
        id: str = typer.Argument(..., help="Entry id."),
        namespace: str = typer.Option(..., "--namespace", help="Entry namespace."),
    ) -> None:
        """Fetch a single entry by ``(namespace, id)``."""
        _get_impl(ctx, kind, id, namespace)

    @sub.command("create")
    def _create_cmd(
        ctx: typer.Context,
        yaml_file: Path = typer.Argument(..., help="Path to single-entry YAML file."),
    ) -> None:
        """Create an entry from a single-entry YAML file."""
        _create_impl(ctx, kind, yaml_file)

    @sub.command("update")
    def _update_cmd(
        ctx: typer.Context,
        yaml_file: Path = typer.Argument(..., help="Path to single-entry YAML file."),
    ) -> None:
        """Update an existing entry from a single-entry YAML file."""
        _update_impl(ctx, kind, yaml_file)

    @sub.command("delete")
    def _delete_cmd(
        ctx: typer.Context,
        id: str = typer.Argument(..., help="Entry id."),
        namespace: str = typer.Option(..., "--namespace", help="Entry namespace."),
    ) -> None:
        """Delete an entry; surfaces inbound referrers on failure."""
        _delete_impl(ctx, kind, id, namespace)

    @sub.command("search")
    def _search_cmd(
        ctx: typer.Context,
        namespace: str | None = typer.Option(None, "--namespace"),
        user_id: str | None = typer.Option(None, "--user-id"),
        user_id_set: str | None = typer.Option(None, "--user-id-set"),
        description_contains: str | None = typer.Option(None, "--description-contains"),
        id: str | None = typer.Option(None, "--id"),
    ) -> None:
        """Structured search across every ``EntryQuery`` filter."""
        _search_impl(
            ctx,
            kind,
            namespace=namespace,
            user_id=user_id,
            user_id_set=user_id_set,
            description_contains=description_contains,
            id=id,
        )

    return sub


_ENTRY_KINDS: tuple[EntryKind, ...] = ("team", "agent", "tool", "model", "prompt", "meta")
# Register one sub-app per EntryKind. The literal order matches EntryKind.
for _kind in _ENTRY_KINDS:
    app.add_typer(_make_kind_app(_kind), name=_kind)


# --------------------------------------------------------------------------- #
# Generic resolved-model renderer (Story 17.2)
# --------------------------------------------------------------------------- #


def _dump_model_json(model: BaseModel) -> str:
    """Return ``model.model_dump(mode='json')`` as a pretty-printed JSON string."""
    return json.dumps(model.model_dump(mode="json"), indent=2)


def _render_model(model: BaseModel, fmt: str) -> None:
    """Render a resolved Pydantic ``BaseModel`` to stdout per ``fmt``.

    ``table`` → two-column key/value table (``type`` + truncated ``payload``).
    ``json`` → ``json.dumps(model.model_dump(mode='json'), indent=2)``.
    ``yaml`` → ``yaml.safe_dump(model.model_dump(mode='json'), sort_keys=False)``.

    Truncation (table only): JSON payload is trimmed to 4 KiB with a
    ``… (N more chars)`` suffix when longer — mirroring ``_render_entry``.
    """
    if fmt == "json":
        _stdout.print(_dump_model_json(model))
        return
    if fmt == "yaml":
        _stdout.print(yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False).rstrip("\n"))
        return
    type_path = f"{model.__class__.__module__}.{model.__class__.__name__}"
    payload_json = _dump_model_json(model)
    table = Table(title=f"model {type_path}", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("type", type_path)
    if len(payload_json) > 4096:
        kept = payload_json[:4096]
        suffix = len(payload_json) - 4096
        table.add_row("payload", f"{kept}… ({suffix} more chars)")
    else:
        table.add_row("payload", payload_json)
    _stdout.print(table)


# --------------------------------------------------------------------------- #
# Graph verbs (Story 17.2): clone / references / resolve / load-team
# --------------------------------------------------------------------------- #


@app.command("clone")
def _clone_cmd(
    ctx: typer.Context,
    src_namespace: str = typer.Option(..., "--src-namespace", help="Source namespace."),
    src_id: str = typer.Option(..., "--src-id", help="Source entry id."),
    dst_namespace: str = typer.Option(..., "--dst-namespace", help="Destination namespace."),
    dst_user_id: str = typer.Option(
        "anonymous",
        "--dst-user-id",
        help="Destination user_id; omit or pass 'anonymous' for community-tier deployments.",
    ),
) -> None:
    """Deep-copy an entry tree into ``--dst-namespace`` (ADR-007 ownership semantics).

    The destination owner defaults to the literal ``"anonymous"`` string —
    the community-tier convention. Department / enterprise tier deployments
    pass the authenticated caller's identifier explicitly.
    """
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    try:
        req = CloneRequest(
            src_namespace=src_namespace,
            src_id=src_id,
            dst_namespace=dst_namespace,
            dst_user_id=dst_user_id,
        )
    except ValidationError as exc:
        err_console.print(f"validation error: {exc}")
        raise typer.Exit(code=2) from None
    try:
        entry = catalog.clone(req.src_namespace, req.src_id, req.dst_namespace, req.dst_user_id)
    except EntryNotFoundError as exc:
        err_console.print(str(exc))
        raise typer.Exit(code=1) from None
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(f"validation error: {err}")
        raise typer.Exit(code=1) from None
    _render_entry(entry, state.output_format)


@app.command("references")
def _references_cmd(
    ctx: typer.Context,
    id: str = typer.Argument(..., help="Target entry id."),
    namespace: str = typer.Option(..., "--namespace", help="Namespace of the target entry."),
) -> None:
    """List entries in ``--namespace`` that reference ``<id>``.

    The service's ``find_references`` is tolerant: a missing target id
    yields an empty list, rendered as ``(no entries)`` / ``[]`` per format.
    """
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    try:
        entries = catalog.find_references(namespace, id)
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(f"validation error: {err}")
        raise typer.Exit(code=1) from None
    _render_entries(entries, state.output_format)


@app.command("resolve")
def _resolve_cmd(
    ctx: typer.Context,
    kind: EntryKind = typer.Argument(..., help="Entry kind (team/agent/tool/model/prompt)."),
    id: str = typer.Argument(..., help="Entry id."),
    namespace: str = typer.Option(..., "--namespace", help="Entry namespace."),
) -> None:
    """Resolve a single entry into its fully-populated runtime ``BaseModel``.

    The ``<kind>`` argument acts as a typo guard (same shape as ``get``):
    a stored entry whose kind disagrees exits with code 1 rather than
    silently resolving against the stored kind.
    """
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    try:
        entry = catalog.get(namespace, id)
    except EntryNotFoundError:
        err_console.print(f"Entry ({namespace}, {id}, {kind}) not found")
        raise typer.Exit(code=1) from None
    if entry.kind != kind:
        err_console.print(
            f"Entry at (namespace={namespace}, id={id}) has kind={entry.kind}, expected {kind}"
        )
        raise typer.Exit(code=1)
    try:
        model = catalog.resolve_by_id(namespace, id)
    except EntryNotFoundError as exc:
        err_console.print(str(exc))
        raise typer.Exit(code=1) from None
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(f"validation error: {err}")
        raise typer.Exit(code=1) from None
    _render_model(model, state.output_format)


@app.command("load-team")
def _load_team_cmd(
    ctx: typer.Context,
    namespace: str = typer.Option(..., "--namespace", help="Namespace to load the team from."),
) -> None:
    """Resolve the team entry in ``--namespace`` into a fully-populated ``TeamCard``."""
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    try:
        team = catalog.load_team(namespace)
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(f"validation error: {err}")
        raise typer.Exit(code=1) from None
    _render_model(team, state.output_format)


# --------------------------------------------------------------------------- #
# Schema-introspection verbs (Story 17.2)
# --------------------------------------------------------------------------- #


def _render_schema(schema: dict[str, Any], fmt: str) -> None:
    """Render a JSON Schema dict. ``table`` falls through to JSON rendering.

    Rationale: JSON Schema is inherently nested; a flat Rich table would be a
    worse UX than the raw schema, so the ``table`` format is documented to
    print pretty-printed JSON (same bytes as ``--format json``).
    """
    if fmt == "yaml":
        print(yaml.safe_dump(schema, sort_keys=False).rstrip("\n"))
        return
    print(json.dumps(schema, indent=2))


@app.command("schema")
def _schema_cmd(
    ctx: typer.Context,
    model_type: str = typer.Argument(..., help="Dotted path of an allowlisted BaseModel class."),
) -> None:
    """Print the JSON Schema for an allowlisted Pydantic model class.

    Delegates to :func:`akgentic.catalog.resolver.load_model_type`, which owns
    the ``akgentic.*`` allowlist gate. ``ImportError`` / ``AttributeError``
    raised by dynamic import are re-raised as ``CatalogValidationError`` for
    consistency with the REST ``GET /catalog/schema`` handler.

    ``--format table`` falls through to JSON rendering because JSON Schema is
    inherently nested; a rich Table would not add clarity.
    """
    state = _state_from_ctx(ctx)
    try:
        try:
            cls = load_model_type(model_type)
        except (ImportError, AttributeError) as exc:
            raise CatalogValidationError(
                [f"model_type '{model_type}' could not be imported: {exc}"]
            ) from exc
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(f"validation error: {err}")
        raise typer.Exit(code=1) from None
    schema = cls.model_json_schema()
    _render_schema(schema, state.output_format)


@app.command("model-types")
def _model_types_cmd(ctx: typer.Context) -> None:
    """List allowlisted Pydantic model classes currently imported in this process.

    Uses the shared reflection helper
    :func:`akgentic.catalog.resolver.enumerate_allowlisted_model_types` — the
    same helper consumed by the REST ``GET /catalog/model_types`` endpoint,
    so CLI and REST output for a given ``sys.modules`` snapshot agree.
    """
    state = _state_from_ctx(ctx)
    paths = enumerate_allowlisted_model_types()
    fmt = state.output_format
    if fmt == "json":
        _stdout.print(json.dumps(paths, indent=2))
        return
    if fmt == "yaml":
        _stdout.print(yaml.safe_dump(paths, sort_keys=False).rstrip("\n"))
        return
    if not paths:
        _stdout.print("(no model types imported)")
        return
    table = Table(title="Allowlisted model types")
    table.add_column("path")
    for path in paths:
        table.add_row(path)
    _stdout.print(table)


# --------------------------------------------------------------------------- #
# Namespace bundle verbs (Story 17.3): export / import
# --------------------------------------------------------------------------- #


def _require_non_empty_namespace(value: str) -> str:
    """Typer callback — reject empty-string ``--namespace`` at parse time."""
    if value == "":
        err_console.print("--namespace must be a non-empty string")
        raise typer.Exit(code=2)
    return value


def _require_non_empty_namespace_optional(value: str | None) -> str | None:
    """Typer callback — tolerates an omitted ``--namespace`` (passes ``None`` through)."""
    if value is None:
        return None
    return _require_non_empty_namespace(value)


def _render_global_errors(table: Table, errors: _list[str]) -> None:
    """Fill ``table`` with one row per global error, or a placeholder when empty."""
    if not errors:
        table.add_row("(no global errors)")
        return
    for err in errors:
        table.add_row(err)


def _render_entry_issues(table: Table, issues: _list[EntryValidationIssue]) -> None:
    """Fill ``table`` with one row per entry issue, or a placeholder when empty."""
    if not issues:
        table.add_row("(no entry issues)", "", "")
        return
    for issue in issues:
        table.add_row(issue.entry_id, issue.kind, "\n".join(issue.errors))


def _render_validation_report(report: NamespaceValidationReport, fmt: str) -> None:
    """Render a ``NamespaceValidationReport`` to stdout per ``fmt``.

    ``json`` / ``yaml`` use stdlib serialisers to avoid Rich line-wrapping on
    structured payloads. ``table`` renders a two-line header plus two Rich
    tables (global errors + entry issues).
    """
    if fmt == "json":
        print(json.dumps(report.model_dump(mode="json"), indent=2))
        return
    if fmt == "yaml":
        print(yaml.safe_dump(report.model_dump(mode="json"), sort_keys=False).rstrip("\n"))
        return
    _stdout.print(f"namespace: {report.namespace}")
    _stdout.print(f"ok: {report.ok}")
    global_table = Table(title="global errors")
    global_table.add_column("error")
    _render_global_errors(global_table, report.global_errors)
    _stdout.print(global_table)
    issues_table = Table(title="entry issues")
    for col in ("entry_id", "kind", "errors"):
        issues_table.add_column(col)
    _render_entry_issues(issues_table, report.entry_issues)
    _stdout.print(issues_table)


@app.command("export")
def _export_cmd(
    ctx: typer.Context,
    namespace: str = typer.Option(
        ...,
        "--namespace",
        help="Namespace to export as a single bundle YAML document.",
        callback=_require_non_empty_namespace,
    ),
) -> None:
    """Emit the given ``--namespace`` as a single bundle YAML document on stdout.

    The bundle is always emitted verbatim as the YAML produced by
    :meth:`Catalog.export_namespace_yaml` — ``--format`` is IGNORED here
    because the bundle is the canonical authored form and a JSON or table
    rendering would break the round-trip contract
    (``export > bundle.yaml; import bundle.yaml``).
    """
    catalog = _repo_from_ctx(ctx)
    try:
        yaml_text = catalog.export_namespace_yaml(namespace)
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(f"validation error: {err}")
        raise typer.Exit(code=1) from None
    # Byte-exact stdout write — no Rich wrapping, no trailing-newline
    # manipulation — preserves `export > bundle.yaml` round-trip fidelity.
    print(yaml_text, end="")


def _read_bundle_text(path: Path) -> str:
    """Load bundle YAML text from ``path``; map usage-level failures to exit 2."""
    if not path.is_file():
        err_console.print(f"file not found: {path}")
        raise typer.Exit(code=2)
    try:
        yaml_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        err_console.print(f"file is not valid UTF-8: {exc}")
        raise typer.Exit(code=2) from None
    # Fail-fast parse check — the Python object is discarded; the service
    # consumes the raw YAML text.
    try:
        yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        err_console.print(f"YAML parse error: {exc}")
        raise typer.Exit(code=2) from None
    return yaml_text


def _import_persistence_mode(catalog: Catalog, yaml_text: str) -> None:
    """Run ``catalog.import_namespace_yaml`` and report the outcome."""
    try:
        persisted = catalog.import_namespace_yaml(yaml_text)
    except CatalogValidationError as exc:
        for err in exc.errors:
            err_console.print(f"validation error: {err}")
        raise typer.Exit(code=1) from None
    ns = persisted[0].namespace
    err_console.print(f"imported {len(persisted)} entries into namespace {ns}")


def _emit_validation_failure(report: NamespaceValidationReport) -> None:
    """Print the canonical validation-failure summary line to stderr and exit 1.

    Shared between the bundle ``import --dry-run`` verb (Story 17.3) and the
    root-level ``validate`` verb (Story 17.4) so both paths emit an identical
    grep-able stderr line. The caller is responsible for rendering the full
    report to stdout before invoking this helper.
    """
    global_count = len(report.global_errors)
    entry_count = sum(1 for i in report.entry_issues if i.errors)
    err_console.print(
        f"validation failed: {global_count} global error(s), {entry_count} entry issue(s)"
    )
    raise typer.Exit(code=1)


def _import_dry_run_mode(catalog: Catalog, yaml_text: str, fmt: str) -> None:
    """Run ``catalog.validate_namespace_yaml``; render the report; derive exit code."""
    report = catalog.validate_namespace_yaml(yaml_text)
    _render_validation_report(report, fmt)
    if not report.ok:
        _emit_validation_failure(report)


@app.command("import")
def _import_cmd(
    ctx: typer.Context,
    bundle_file: Path = typer.Argument(
        ...,
        exists=False,
        help="Path to a bundle YAML file (document-level namespace + entries).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        help="Validate only; do not persist.",
    ),
) -> None:
    """Import a namespace bundle YAML file into the catalog.

    In persistence mode, delegates to :meth:`Catalog.import_namespace_yaml`
    (atomic replace of the namespace declared at document level). In
    ``--dry-run`` mode, delegates to :meth:`Catalog.validate_namespace_yaml`
    and renders the resulting ``NamespaceValidationReport`` per ``--format``
    — exit 0 iff ``report.ok`` is True.
    """
    yaml_text = _read_bundle_text(bundle_file)
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    if dry_run:
        _import_dry_run_mode(catalog, yaml_text, state.output_format)
        return
    _import_persistence_mode(catalog, yaml_text)


# --------------------------------------------------------------------------- #
# Namespace validation verb (Story 17.4): validate
# --------------------------------------------------------------------------- #


def _validate_persisted(catalog: Catalog, namespace: str, fmt: str) -> None:
    """Run ``catalog.validate_namespace`` and derive CLI output + exit code."""
    report = catalog.validate_namespace(namespace)
    _render_validation_report(report, fmt)
    if not report.ok:
        _emit_validation_failure(report)


def _validate_bundle(catalog: Catalog, bundle_file: Path, fmt: str) -> None:
    """Read a bundle file and dry-run-validate it via ``validate_namespace_yaml``."""
    yaml_text = _read_bundle_text(bundle_file)
    report = catalog.validate_namespace_yaml(yaml_text)
    _render_validation_report(report, fmt)
    if not report.ok:
        _emit_validation_failure(report)


@app.command("validate")
def _validate_cmd(
    ctx: typer.Context,
    bundle_file: Path | None = typer.Argument(
        None,
        exists=False,
        help="Path to a bundle YAML file (dry-run flavor).",
    ),
    namespace: str | None = typer.Option(
        None,
        "--namespace",
        help="Persisted namespace to validate.",
        callback=_require_non_empty_namespace_optional,
    ),
) -> None:
    """Validate a namespace — persisted state or dry-run bundle.

    Exactly one of ``--namespace <ns>`` or a positional ``<bundle-file>`` must
    be supplied. The verb delegates to :meth:`Catalog.validate_namespace` or
    :meth:`Catalog.validate_namespace_yaml` respectively; neither service
    method raises, so the exit code is derived from ``report.ok`` alone
    (``0`` when True, ``1`` when False). Usage errors (zero-or-both args,
    missing / non-UTF-8 / malformed-YAML bundle file) exit 2 with a stderr
    diagnostic.
    """
    if namespace is None and bundle_file is None:
        err_console.print("validate requires either --namespace <ns> or a bundle file path")
        raise typer.Exit(code=2)
    if namespace is not None and bundle_file is not None:
        err_console.print(
            "validate accepts either --namespace <ns> or a bundle file path, not both"
        )
        raise typer.Exit(code=2)
    catalog = _repo_from_ctx(ctx)
    state = _state_from_ctx(ctx)
    if namespace is not None:
        _validate_persisted(catalog, namespace, state.output_format)
        return
    assert bundle_file is not None  # exclusivity guard above
    _validate_bundle(catalog, bundle_file, state.output_format)


# --------------------------------------------------------------------------- #
# Helper — used by tests & silencing "unused import" warnings at module level.
# --------------------------------------------------------------------------- #


def _unused(_e: Any) -> None:  # pragma: no cover - trivial
    """Retain references to imports used only for typing."""


_unused(EntryRepository)
