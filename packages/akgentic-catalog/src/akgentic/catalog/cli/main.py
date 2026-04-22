"""Typer CLI application for the Akgentic catalog.

Provides the ``ak-catalog`` entry point with global options and subcommand
groups for managing catalog entries.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console

from akgentic.catalog.cli._output import OutputFormat

if TYPE_CHECKING:
    from akgentic.catalog.models.agent import AgentEntry
    from akgentic.catalog.models.team import TeamEntry, TeamMemberSpec
    from akgentic.catalog.models.template import TemplateEntry
    from akgentic.catalog.models.tool import ToolEntry
    from akgentic.catalog.services.agent_catalog import AgentCatalog
    from akgentic.catalog.services.team_catalog import TeamCatalog
    from akgentic.catalog.services.template_catalog import TemplateCatalog
    from akgentic.catalog.services.tool_catalog import ToolCatalog

__all__ = ["app"]

logger = logging.getLogger(__name__)
err_console = Console(stderr=True)


@dataclass
class GlobalState:
    """Shared state passed through Typer context."""

    catalog_dir: Path = field(default_factory=lambda: Path("catalog"))
    format: OutputFormat = OutputFormat.table
    backend: str = "yaml"
    mongo_uri: str | None = None
    mongo_db: str | None = None
    postgres_conn_string: str | None = None


app = typer.Typer(name="ak-catalog", help="Manage Akgentic catalog entries.")


@app.callback()
def main(
    ctx: typer.Context,
    catalog_dir: Path = typer.Option(
        Path("catalog"),
        "--catalog-dir",
        help="Root directory for catalog YAML files.",
    ),
    fmt: OutputFormat = typer.Option(
        OutputFormat.table,
        "--format",
        help="Output format: table, json, or yaml.",
    ),
    backend: str = typer.Option(
        "yaml",
        "--backend",
        help="Storage backend: yaml, mongodb, or postgres.",
    ),
    mongo_uri: str | None = typer.Option(
        None,
        "--mongo-uri",
        envvar="MONGO_URI",
        help="MongoDB connection URI (required when --backend=mongodb).",
    ),
    mongo_db: str | None = typer.Option(
        None,
        "--mongo-db",
        envvar="MONGO_DB",
        help="MongoDB database name (required when --backend=mongodb).",
    ),
    postgres_conn_string: str | None = typer.Option(
        None,
        "--postgres-conn-string",
        envvar="DB_CONN_STRING_PERSISTENCE",
        help="Postgres connection URL (required when --backend=postgres).",
    ),
) -> None:
    """Akgentic catalog CLI -- manage templates, tools, agents, and teams."""
    valid_backends = ("yaml", "mongodb", "postgres")
    if backend not in valid_backends:
        err_console.print(
            f"[red]Error:[/red] Invalid backend '{backend}'. "
            f"Must be one of: {', '.join(valid_backends)}"
        )
        logger.warning("Invalid backend value: %s", backend)
        raise typer.Exit(code=1)

    if backend == "mongodb":
        errors = _validate_mongodb_options(mongo_uri, mongo_db)
        if errors:
            for err in errors:
                err_console.print(f"[red]Error:[/red] {err}")
            logger.warning("MongoDB validation failed: %s", errors)
            raise typer.Exit(code=1)

    if backend == "postgres":
        errors = _validate_postgres_options(postgres_conn_string)
        if errors:
            for err in errors:
                err_console.print(f"[red]Error:[/red] {err}")
            logger.warning("Postgres validation failed: %s", errors)
            raise typer.Exit(code=1)

    ctx.ensure_object(dict)
    ctx.obj = GlobalState(
        catalog_dir=catalog_dir,
        format=fmt,
        backend=backend,
        mongo_uri=mongo_uri,
        mongo_db=mongo_db,
        postgres_conn_string=postgres_conn_string,
    )


def _validate_mongodb_options(
    mongo_uri: str | None,
    mongo_db: str | None,
) -> list[str]:
    """Validate MongoDB connection options and return error messages.

    Args:
        mongo_uri: MongoDB connection URI, or None if not provided.
        mongo_db: MongoDB database name, or None if not provided.

    Returns:
        List of error message strings (empty if valid).
    """
    errors: list[str] = []
    if not mongo_uri:
        errors.append("--mongo-uri (or MONGO_URI env var) is required when --backend=mongodb")
    elif not mongo_uri.startswith(("mongodb://", "mongodb+srv://")):
        errors.append("--mongo-uri must start with 'mongodb://' or 'mongodb+srv://'")
    if not mongo_db:
        errors.append("--mongo-db (or MONGO_DB env var) is required when --backend=mongodb")
    return errors


def _validate_postgres_options(
    postgres_conn_string: str | None,
) -> list[str]:
    """Validate Postgres connection options and return error messages.

    Args:
        postgres_conn_string: Postgres libpq connection URL, or None.

    Returns:
        List of error message strings (empty if valid).
    """
    errors: list[str] = []
    if not postgres_conn_string:
        errors.append(
            "--postgres-conn-string (or DB_CONN_STRING_PERSISTENCE env var) "
            "is required when --backend=postgres"
        )
    return errors


def _load_entries_module(file_path: Path) -> list[Any]:
    """Load a Python module and return its ``entries`` list.

    Args:
        file_path: Path to a ``.py`` file containing an ``entries`` variable.

    Returns:
        The list of catalog entries defined in the module.

    Raises:
        FileNotFoundError: If the file does not exist.
        ImportError: If the module cannot be loaded.
        ValueError: If the module lacks ``entries`` or it is not a list.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    spec = importlib.util.spec_from_file_location("_catalog_import", file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_catalog_import"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("_catalog_import", None)
    entries = getattr(module, "entries", None)
    if entries is None:
        raise ValueError(f"Module {file_path} does not define an 'entries' variable")
    if not isinstance(entries, list):
        raise ValueError(f"'entries' in {file_path} must be a list")
    if not entries:
        raise ValueError(f"'entries' in {file_path} is empty")
    return entries


def _classify_entries(
    entries: list[Any],
) -> tuple[list[TemplateEntry], list[ToolEntry], list[AgentEntry], list[TeamEntry]]:
    """Classify entries by type into four lists.

    Args:
        entries: Raw list of catalog entry objects.

    Returns:
        Tuple of (templates, tools, agents, teams).
    """
    from akgentic.catalog.models.agent import AgentEntry as _AgentEntry
    from akgentic.catalog.models.team import TeamEntry as _TeamEntry
    from akgentic.catalog.models.template import TemplateEntry as _TemplateEntry
    from akgentic.catalog.models.tool import ToolEntry as _ToolEntry

    templates: list[TemplateEntry] = []
    tools: list[ToolEntry] = []
    agents: list[AgentEntry] = []
    teams: list[TeamEntry] = []

    for entry in entries:
        if isinstance(entry, _TemplateEntry):
            templates.append(entry)
        elif isinstance(entry, _ToolEntry):
            tools.append(entry)
        elif isinstance(entry, _AgentEntry):
            agents.append(entry)
        elif isinstance(entry, _TeamEntry):
            teams.append(entry)
        else:
            err_console.print(
                f"[yellow]Warning:[/yellow] Skipping unknown entry type: {type(entry).__name__}"
            )

    return templates, tools, agents, teams


def _collect_member_ids(members: list[TeamMemberSpec]) -> set[str]:
    """Recursively collect all agent_ids from a members tree.

    Args:
        members: The member tree to traverse.

    Returns:
        Set of all agent_id values found in the tree.
    """
    ids: set[str] = set()
    for m in members:
        ids.add(m.agent_id)
        if m.members:
            ids |= _collect_member_ids(m.members)
    return ids


@app.command("import")
def import_cmd(
    ctx: typer.Context,
    python_file: Path = typer.Argument(help="Path to a Python file with an 'entries' list"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without persisting"),
) -> None:
    """Import catalog entries from a Python file."""
    from akgentic.catalog.cli._catalog import build_catalogs_from_state
    from akgentic.catalog.models.errors import CatalogValidationError

    state = get_state(ctx)
    template_catalog, tool_catalog, agent_catalog, team_catalog = build_catalogs_from_state(state)

    try:
        entries = _load_entries_module(python_file)
    except (FileNotFoundError, ImportError, ValueError) as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        logger.warning("Failed to load entries from %s: %s", python_file, exc)
        raise typer.Exit(code=1) from exc

    templates, tools, agents, teams = _classify_entries(entries)
    logger.info(
        "Classified entries: %s templates, %s tools, %s agents, %s teams",
        len(templates),
        len(tools),
        len(agents),
        len(teams),
    )

    if dry_run:
        _import_dry_run(
            templates,
            tools,
            agents,
            teams,
            template_catalog,
            tool_catalog,
            agent_catalog,
            team_catalog,
        )
        return

    errors: list[str] = []
    created = 0
    updated = 0

    # Resolution order: templates + tools first
    for tmpl in templates:
        try:
            if template_catalog.get(tmpl.id) is None:
                template_catalog.create(tmpl)
                err_console.print(f"[green]Created template:[/green] {tmpl.id}")
                created += 1
            else:
                template_catalog.update(tmpl.id, tmpl)
                err_console.print(f"[blue]Updated template:[/blue] {tmpl.id}")
                updated += 1
        except CatalogValidationError as exc:
            for err in exc.errors:
                errors.append(f"Template '{tmpl.id}': {err}")

    for tool in tools:
        try:
            if tool_catalog.get(tool.id) is None:
                tool_catalog.create(tool)
                err_console.print(f"[green]Created tool:[/green] {tool.id}")
                created += 1
            else:
                tool_catalog.update(tool.id, tool)
                err_console.print(f"[blue]Updated tool:[/blue] {tool.id}")
                updated += 1
        except CatalogValidationError as exc:
            for err in exc.errors:
                errors.append(f"Tool '{tool.id}': {err}")

    # Then agents
    batch_names = {a.card.config.name for a in agents if a.card.config.name}
    for agent in agents:
        try:
            if agent_catalog.get(agent.id) is None:
                agent_catalog.create(agent, pending_names=batch_names)
                err_console.print(f"[green]Created agent:[/green] {agent.id}")
                created += 1
            else:
                agent_catalog.update(agent.id, agent)
                err_console.print(f"[blue]Updated agent:[/blue] {agent.id}")
                updated += 1
        except (CatalogValidationError, ValueError) as exc:
            if isinstance(exc, CatalogValidationError):
                for err in exc.errors:
                    errors.append(f"Agent '{agent.id}': {err}")
            else:
                errors.append(f"Agent '{agent.id}': {exc}")

    # Finally teams
    for team in teams:
        try:
            if team_catalog.get(team.id) is None:
                team_catalog.create(team)
                err_console.print(f"[green]Created team:[/green] {team.id}")
                created += 1
            else:
                team_catalog.update(team.id, team)
                err_console.print(f"[blue]Updated team:[/blue] {team.id}")
                updated += 1
        except CatalogValidationError as exc:
            for err in exc.errors:
                errors.append(f"Team '{team.id}': {err}")

    # Summary
    err_console.print(f"\nImported: {created} created, {updated} updated, {len(errors)} errors")
    if errors:
        for err in errors:
            err_console.print(f"[red]  - {err}[/red]")
        raise typer.Exit(code=1)


def _import_dry_run(
    templates: list[TemplateEntry],
    tools: list[ToolEntry],
    agents: list[AgentEntry],
    teams: list[TeamEntry],
    template_catalog: TemplateCatalog,
    tool_catalog: ToolCatalog,
    agent_catalog: AgentCatalog,
    team_catalog: TeamCatalog,
) -> None:
    """Validate entries without persisting (dry-run mode).

    Args:
        templates: Template entries to validate.
        tools: Tool entries to validate.
        agents: Agent entries to validate.
        teams: Team entries to validate.
        template_catalog: Template catalog service.
        tool_catalog: Tool catalog service.
        agent_catalog: Agent catalog service for cross-validation.
        team_catalog: Team catalog service for cross-validation.
    """
    would_create = 0
    would_update = 0
    error_count = 0

    for tmpl in templates:
        if template_catalog.get(tmpl.id) is None:
            would_create += 1
        else:
            would_update += 1

    for tool in tools:
        if tool_catalog.get(tool.id) is None:
            would_create += 1
        else:
            would_update += 1

    batch_names = {a.card.config.name for a in agents if a.card.config.name}
    for agent in agents:
        existing = agent_catalog.get(agent.id)
        errs = agent_catalog.validate_create(agent, pending_names=batch_names)
        # Filter out "already exists" errors for entries that exist
        if existing is not None:
            errs = [e for e in errs if "already exists" not in e]
            would_update += 1
        else:
            filtered = [e for e in errs if "already exists" not in e]
            if not filtered:
                would_create += 1
            errs = filtered
        if errs:
            for err in errs:
                err_console.print(f"[red]  Agent '{agent.id}': {err}[/red]")
            error_count += len(errs)

    for team in teams:
        errs = team_catalog.validate_create(team)
        existing_team = team_catalog.get(team.id)
        if existing_team is not None:
            errs = [e for e in errs if "already exists" not in e]
            would_update += 1
        else:
            filtered = [e for e in errs if "already exists" not in e]
            if not filtered:
                would_create += 1
            errs = filtered
        if errs:
            for err in errs:
                err_console.print(f"[red]  Team '{team.id}': {err}[/red]")
            error_count += len(errs)

    err_console.print(
        f"\nDry run: {would_create} would be created, "
        f"{would_update} would be updated, {error_count} errors"
    )
    if error_count:
        raise typer.Exit(code=1)


@app.command("validate")
def validate_cmd(
    ctx: typer.Context,
    catalog: str | None = typer.Option(
        None, "--catalog", help="Validate specific catalog: templates, tools, agents, teams"
    ),
) -> None:
    """Validate catalog entries for cross-reference consistency."""
    from akgentic.catalog.cli._catalog import build_catalogs_from_state
    from akgentic.catalog.refs import _is_catalog_ref, _resolve_ref
    from akgentic.core.utils.deserializer import import_class

    valid_catalogs = ("templates", "tools", "agents", "teams")
    if catalog is not None and catalog not in valid_catalogs:
        err_console.print(
            f"[red]Error:[/red] Invalid catalog '{catalog}'. "
            f"Must be one of: {', '.join(valid_catalogs)}"
        )
        raise typer.Exit(code=1)

    state = get_state(ctx)
    template_catalog, tool_catalog, agent_catalog, team_catalog = build_catalogs_from_state(state)

    errors: list[str] = []
    template_count = 0
    tool_count = 0
    agent_count = 0
    team_count = 0

    # Pre-load ID sets to avoid N+1 per-entry get() calls
    tool_ids_set: set[str] = set()
    agent_ids_set: set[str] = set()

    # Templates
    if catalog is None or catalog == "templates":
        template_entries = template_catalog.list()
        template_count = len(template_entries)

    # Tools
    if catalog is None or catalog == "tools":
        tool_entries = tool_catalog.list()
        tool_count = len(tool_entries)
        tool_ids_set = {t.id for t in tool_entries}

    # Agents
    if catalog is None or catalog == "agents":
        agent_entries = agent_catalog.list()
        agent_count = len(agent_entries)
        agent_ids_set = {a.id for a in agent_entries}
        known_names = {a.card.config.name for a in agent_entries if a.card.config.name}

        # Load tool IDs if not already loaded (e.g. --catalog agents)
        if not tool_ids_set:
            tool_ids_set = {t.id for t in tool_catalog.list()}

        for agent in agent_entries:
            # Check tool_ids
            for tool_id in agent.tool_ids:
                if tool_id not in tool_ids_set:
                    errors.append(f"Agent '{agent.id}': tool '{tool_id}' not found")
            # Check @template reference
            config = agent.card.config
            if hasattr(config, "prompt"):
                prompt = config.prompt  # ADR-003: duck-type gate
                if _is_catalog_ref(prompt.template):
                    template_id = _resolve_ref(prompt.template)
                    tmpl = template_catalog.get(template_id)
                    if tmpl is None:
                        errors.append(f"Agent '{agent.id}': template '@{template_id}' not found")
                    else:
                        provided = set(prompt.params.keys())
                        expected = set(tmpl.placeholders)
                        missing = expected - provided
                        extra = provided - expected
                        if missing:
                            errors.append(
                                f"Agent '{agent.id}': missing params for "
                                f"'@{template_id}': {missing}"
                            )
                        if extra:
                            errors.append(
                                f"Agent '{agent.id}': extra params for '@{template_id}': {extra}"
                            )
            # Check routes_to
            for route_name in agent.card.routes_to:
                if route_name not in known_names:
                    errors.append(f"Agent '{agent.id}': route target '{route_name}' not found")

    # Teams
    if catalog is None or catalog == "teams":
        team_entries = team_catalog.list()
        team_count = len(team_entries)

        # Load agent IDs if not already loaded (e.g. --catalog teams)
        if not agent_ids_set:
            agent_ids_set = {a.id for a in agent_catalog.list()}

        for team in team_entries:
            all_member_ids = _collect_member_ids(team.members)
            # Check entry_point in members
            if team.entry_point not in all_member_ids:
                errors.append(f"Team '{team.id}': entry_point '{team.entry_point}' not in members")
            # Check all member agent_ids exist
            for member_id in all_member_ids:
                if member_id not in agent_ids_set:
                    errors.append(f"Team '{team.id}': member agent '{member_id}' not found")
            # Check agent_profiles
            for profile_id in team.agent_profiles:
                if profile_id not in agent_ids_set:
                    errors.append(f"Team '{team.id}': profile agent '{profile_id}' not found")
            # Check message_types
            for mt in team.message_types:
                try:
                    import_class(mt)
                except (ImportError, AttributeError, ValueError):
                    errors.append(f"Team '{team.id}': message type '{mt}' not resolvable")

    # Report
    if errors:
        for err in errors:
            err_console.print(f"[red]  {err}[/red]")
    err_console.print(
        f"\nValidated: {template_count} templates, {tool_count} tools, "
        f"{agent_count} agents, {team_count} teams. "
        f"Found {len(errors)} errors."
    )
    if errors:
        raise typer.Exit(code=1)


def get_state(ctx: typer.Context) -> GlobalState:
    """Retrieve the global state from the Typer context.

    Falls back to default ``GlobalState`` if the context has no object set.

    Args:
        ctx: The current Typer command context.

    Returns:
        The ``GlobalState`` instance stored in ``ctx.obj``, or a default.
    """
    state: GlobalState = ctx.obj
    if state is None:
        state = GlobalState()
    return state


def _register_subcommands() -> None:
    """Register all subcommand groups on the app.

    Called at module load time. Deferred to a function to keep imports
    after all module-level definitions, avoiding E402.
    """
    from akgentic.catalog.cli.agent_cmd import agent_app
    from akgentic.catalog.cli.team_cmd import team_app
    from akgentic.catalog.cli.template_cmd import template_app
    from akgentic.catalog.cli.tool_cmd import tool_app

    app.add_typer(template_app, name="template")
    app.add_typer(tool_app, name="tool")
    app.add_typer(agent_app, name="agent")
    app.add_typer(team_app, name="team")


_register_subcommands()
