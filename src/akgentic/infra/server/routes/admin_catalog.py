"""Admin catalog router — generic CRUD over templates, tools, agents, teams.

Implements ADR-022 §D1 (five CRUD verbs per entity), §D2 (repository-exception
→ HTTP-status mapping delegated to the global handlers registered by
``akgentic.catalog.api._errors.add_exception_handlers``), and §D7 (structured
INFO-level log line on every mutation; reads stay silent at INFO).

One generic helper :func:`_register_entity_routes` is applied four times — once
per entity — with per-entity Pydantic entry models and per-entity ``?q=``
mappings. Adding a fifth entity is a one-line additional call.

`?q=<s>` per-entity mapping (mirrors AC #6):

* ``templates`` → ``TemplateQuery(placeholder=s)`` (exact placeholder-name match)
* ``tools`` → ``ToolQuery(name=s)``
* ``agents`` → ``AgentQuery(description=s)``
* ``teams`` → ``TeamQuery(name=s)``

The router relies entirely on middleware-level auth (ADR-022 §D3) — no
``Depends(AuthStrategy)`` is attached to any route, and no RBAC role is
introduced. The only auth contact is defensive, inside the mutation-log
emitter, where ``services.auth.authenticate(request)`` feeds the
``principal_id`` log field.
"""

import builtins
import json
import logging
from collections.abc import Callable
from typing import Generic, Protocol, TypeVar, cast

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from akgentic.catalog.models import (
    AgentEntry,
    AgentQuery,
    EntryNotFoundError,
    TeamEntry,
    TeamQuery,
    TemplateEntry,
    TemplateQuery,
    ToolEntry,
    ToolQuery,
)
from akgentic.catalog.services import (
    AgentCatalog,
    TeamCatalog,
    TemplateCatalog,
    ToolCatalog,
)

_list = builtins.list  # Alias: the protocol's list() method shadows the built-in.

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"])


# Legacy-style TypeVars required for contravariance on QueryT (PEP 695's
# class-header generic syntax does not yet support variance annotations in
# Python 3.12); this also keeps Generic[...] explicit for the Protocol.
EntryT = TypeVar("EntryT", bound=BaseModel)
QueryT = TypeVar("QueryT", bound=BaseModel, contravariant=True)


class _CatalogProto(Protocol, Generic[EntryT, QueryT]):  # noqa: UP046
    """Structural protocol shared by all four ``{Entity}Catalog`` services."""

    def list(self) -> _list[EntryT]: ...
    def get(self, entry_id: str, /) -> EntryT | None: ...
    def search(self, query: QueryT, /) -> _list[EntryT]: ...
    def create(self, entry: EntryT, /) -> str: ...
    def update(self, entry_id: str, entry: EntryT, /) -> None: ...
    def delete(self, entry_id: str, /) -> None: ...


# --- Per-entity dependency accessors (module-private) -------------------------


def _get_template_catalog(request: Request) -> TemplateCatalog:
    """Read the wired TemplateCatalog from ``app.state.services``."""
    return cast(TemplateCatalog, request.app.state.services.template_catalog)


def _get_tool_catalog(request: Request) -> ToolCatalog:
    """Read the wired ToolCatalog from ``app.state.services``."""
    return cast(ToolCatalog, request.app.state.services.tool_catalog)


def _get_agent_catalog(request: Request) -> AgentCatalog:
    """Read the wired AgentCatalog from ``app.state.services``."""
    return cast(AgentCatalog, request.app.state.services.agent_catalog)


def _get_team_catalog(request: Request) -> TeamCatalog:
    """Read the wired TeamCatalog from ``app.state.services``."""
    return cast(TeamCatalog, request.app.state.services.team_catalog)


# --- Logging helper -----------------------------------------------------------


def _emit_mutation_log(
    request: Request, *, entity_name: str, entity_id: str, operation: str
) -> None:
    """Emit one structured INFO log line per mutation (create/update/delete).

    The only field sourced from auth is ``principal_id``, resolved defensively
    via the already-wired ``services.auth`` — no ``Depends(AuthStrategy)``
    (ADR-022 §D3, middleware-level auth).
    """
    principal_id = request.app.state.services.auth.authenticate(request) or "anonymous"
    logger.info(
        "catalog admin mutation",
        extra={
            "principal_id": principal_id,
            "entity_type": entity_name,
            "entity_id": entity_id,
            "operation": operation,
        },
    )


# --- Request-body parsing (ADR-022 §D4: JSON + YAML single validation point) --

_YAML_CONTENT_TYPES = frozenset({"application/yaml", "application/x-yaml"})


async def _parse_entry_body(request: Request, entry_model: type[BaseModel]) -> BaseModel:
    """Parse the request body into ``entry_model`` based on ``Content-Type``.

    Accepts ``application/json`` (or missing header) and
    ``application/yaml`` / ``application/x-yaml``. Content-type parameters
    (``; charset=utf-8``) are tolerated — split on ``;``, strip, lower-case the
    head.

    Invalid YAML raises ``HTTPException(422, "invalid YAML body: ...")``.
    Unsupported content types raise ``HTTPException(415, ...)``. Pydantic
    ``ValidationError`` on ``entry_model.model_validate`` is mapped to a 422
    ``HTTPException`` (mirrors FastAPI's default body-binding behaviour now
    that the explicit body parameter has been replaced with raw-request
    dispatch).
    """
    raw_ct = request.headers.get("content-type", "application/json")
    content_type = raw_ct.split(";")[0].strip().lower()
    raw = await request.body()

    if content_type == "application/json" or content_type == "":
        parsed: object = json.loads(raw) if raw else {}
    elif content_type in _YAML_CONTENT_TYPES:
        try:
            parsed = yaml.safe_load(raw) if raw else {}
        except yaml.YAMLError as exc:
            raise HTTPException(status_code=422, detail=f"invalid YAML body: {exc}") from exc
    else:
        raise HTTPException(
            status_code=415,
            detail=(
                f"unsupported Content-Type: {content_type!r}; "
                "expected application/json or application/yaml"
            ),
        )

    try:
        return entry_model.model_validate(parsed)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


# --- Generic route registration ----------------------------------------------


def _register_entity_routes(  # noqa: UP047
    router: APIRouter,
    *,
    entity_name: str,
    get_catalog: Callable[[Request], _CatalogProto[EntryT, QueryT]],
    entry_model: type[EntryT],
    query_from_q: Callable[[str], QueryT],
) -> None:
    """Register the five CRUD verbs for one entity on ``router``.

    Generic over entity: delegates to the passed-in ``get_catalog`` dependency
    and to ``entry_model`` as request body / response type. Uses
    ``query_from_q`` to translate the optional ``?q=<s>`` query parameter into
    the entity's typed query model (per AC #6).

    Mutation routes never wrap the catalog call in try/except — they let
    ``CatalogValidationError`` (→ 409) and ``EntryNotFoundError`` (→ 404)
    propagate to the global handlers registered in
    ``akgentic.catalog.api._errors.add_exception_handlers``.

    Note on the one remaining ``type: ignore[valid-type]`` below: ``entry_model``
    is a runtime value of type ``type[EntryT]``. FastAPI reads it as a function
    annotation for request-body validation, which is legal Python but mypy
    cannot resolve a value-bound ``type[...]`` as a type expression. The ignore
    is structural to the generic-factory + FastAPI pattern, not a stand-in for
    a missing type.
    """
    singular = entity_name.rstrip("s").title()
    prefix = f"/{entity_name}"

    @router.get(prefix, response_model=_list[entry_model])  # type: ignore[valid-type]
    def _list_entries(
        q: str | None = None,
        catalog: _CatalogProto[EntryT, QueryT] = Depends(get_catalog),
    ) -> _list[EntryT]:
        if q is None:
            return catalog.list()
        return catalog.search(query_from_q(q))

    @router.get(prefix + "/{entry_id}", response_model=entry_model)
    def _get_entry(
        entry_id: str,
        catalog: _CatalogProto[EntryT, QueryT] = Depends(get_catalog),
    ) -> EntryT:
        result = catalog.get(entry_id)
        if result is None:
            raise EntryNotFoundError(f"{singular} id '{entry_id}' not found")
        return result

    @router.post(prefix, response_model=entry_model, status_code=201)
    async def _create_entry(
        request: Request,
        catalog: _CatalogProto[EntryT, QueryT] = Depends(get_catalog),
    ) -> EntryT:
        entry = cast(EntryT, await _parse_entry_body(request, entry_model))
        catalog.create(entry)
        entry_id = cast(str, getattr(entry, "id"))  # noqa: B009
        _emit_mutation_log(request, entity_name=entity_name, entity_id=entry_id, operation="create")
        return entry

    @router.put(prefix + "/{entry_id}", response_model=entry_model)
    async def _update_entry(
        entry_id: str,
        request: Request,
        catalog: _CatalogProto[EntryT, QueryT] = Depends(get_catalog),
    ) -> EntryT:
        entry = cast(EntryT, await _parse_entry_body(request, entry_model))
        catalog.update(entry_id, entry)
        _emit_mutation_log(request, entity_name=entity_name, entity_id=entry_id, operation="update")
        return entry

    @router.delete(prefix + "/{entry_id}", status_code=204)
    def _delete_entry(
        entry_id: str,
        request: Request,
        catalog: _CatalogProto[EntryT, QueryT] = Depends(get_catalog),
    ) -> None:
        catalog.delete(entry_id)
        _emit_mutation_log(request, entity_name=entity_name, entity_id=entry_id, operation="delete")


# --- Four registration calls — one per entity -------------------------------


_register_entity_routes(
    router,
    entity_name="templates",
    get_catalog=_get_template_catalog,
    entry_model=TemplateEntry,
    query_from_q=lambda s: TemplateQuery(placeholder=s),
)
_register_entity_routes(
    router,
    entity_name="tools",
    get_catalog=_get_tool_catalog,
    entry_model=ToolEntry,
    query_from_q=lambda s: ToolQuery(name=s),
)
_register_entity_routes(
    router,
    entity_name="agents",
    get_catalog=_get_agent_catalog,
    entry_model=AgentEntry,
    query_from_q=lambda s: AgentQuery(description=s),
)
_register_entity_routes(
    router,
    entity_name="teams",
    get_catalog=_get_team_catalog,
    entry_model=TeamEntry,
    query_from_q=lambda s: TeamQuery(name=s),
)


__all__ = ["router"]
