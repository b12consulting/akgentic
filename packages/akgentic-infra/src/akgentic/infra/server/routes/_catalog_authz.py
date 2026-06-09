"""Resource-level owner-or-admin authorization gate for catalog mutations.

Implements ADR-028 §Decision 1: a single FastAPI dependency,
``require_namespace_owner_or_admin``, attached per-route to the catalog
modify + delete routes at the shared ``/admin/catalog/*`` mount. Every
deployment tier inherits the same policy from one place; the catalog never
learns about roles (ADR-013 stays role-agnostic).

The rule is resource-level, not static-role::

    allow iff caller.user_id == namespace.owner_user_id OR "admin" in caller.roles

Identity comes from the ADR-023 ``RequestUser`` seam (``get_request_user``)
exactly as each tier already resolves it — this module adds no new auth path
and never branches on ``auth_method``. ``"admin"`` is the literal role string
from the existing three-role model (``admin``/``operator``/``viewer``).

The namespace owner is resolved the same way the catalog anchors ownership
(see ``Catalog._check_ownership``): the ``kind="team"`` entry's ``user_id`` if
a team entry exists in the namespace, else the ``kind="meta"`` (``_meta``)
entry's ``user_id``; neither → unresolvable. An unresolvable owner fails
**closed** — a non-admin caller gets 403 — while an admin still bypasses
because the admin branch returns before any owner lookup.
"""

from __future__ import annotations

import logging

import yaml
from fastapi import Depends, HTTPException, Request

from akgentic.infra.server.auth import RequestUser, get_request_user

logger = logging.getLogger(__name__)

__all__ = ["require_import_owner_or_admin", "require_namespace_owner_or_admin"]


def require_namespace_owner_or_admin(
    namespace: str,
    request: Request,
    user: RequestUser = Depends(get_request_user),
) -> None:
    """Authorize a catalog mutation: caller must own ``namespace`` or be admin.

    FastAPI binds the ``namespace`` parameter from the route **path** *or*
    the **query** by name, so the same dependency covers both shapes — the
    two ``/namespace/{namespace}/...`` routes (path) and the two
    ``/{kind}/{id}`` routes (``namespace`` query).

    Args:
        namespace: The target namespace (path or query, bound by name).
        request: The inbound request — used to reach the wired catalog via
            ``request.app.state.services.catalog``.
        user: The authenticated principal resolved through the ADR-023 seam.

    Raises:
        HTTPException: 403 when the caller is neither an admin nor the
            resolved namespace owner (including the fail-closed case where
            the owner is unresolvable).
    """
    if "admin" in user.roles:
        return
    owner = _resolve_namespace_owner(request, namespace)
    if owner is None or owner != user.user_id:
        logger.info(
            "owner-or-admin gate denied mutation",
            extra={"namespace": namespace, "user_id": user.user_id, "owner": owner},
        )
        raise HTTPException(status_code=403, detail="not authorized to modify this namespace")


async def require_import_owner_or_admin(
    request: Request,
    user: RequestUser = Depends(get_request_user),
) -> None:
    """Authorize a body-carried catalog import: overwrite needs owner-or-admin.

    Implements ADR-028 §Decision 8 — the scoped exception to the route gate's
    "no body-peeking" rule (ADR-028 §Decision 3). ``POST /namespace/import`` is
    the one mutating catalog route whose target ``namespace`` lives in the YAML
    **body**, not the path/query, so ``require_namespace_owner_or_admin`` (which
    binds ``namespace`` from path/query and never reads a body) cannot see it.
    This dependency reads the body, extracts the bundle root ``namespace``, and
    applies the **same** owner-or-admin predicate via the **same**
    ``_resolve_namespace_owner`` the route gate uses — so create-new (no owner
    yet ⇒ allow) is distinguished from overwrite-existing (gate).

    Predicate (in this exact order):

    1. ``"admin" in user.roles`` ⇒ allow (return before any owner lookup).
    2. else resolve the owner; ``owner is None`` (namespace does not exist yet
       ⇒ **create**) ⇒ allow — the catalog's stamp-on-write records the caller
       as the new owner.
    3. else ``owner == user.user_id`` (caller owns the existing namespace ⇒
       **overwrite-own**) ⇒ allow.
    4. else ⇒ 403 with the same detail the route gate uses.

    **Body-read safety.** ``await request.body()`` is safe here: Starlette
    caches the request body on first read (``Request.body`` stores ``_body``),
    so the catalog's ``import_namespace`` handler — which reads ``body: bytes``
    — still receives the full body after this dependency has read it. No
    double-consume, no empty-body starvation.

    **Fail-open-to-handler.** When the body is not UTF-8, not parseable YAML, or
    carries no extractable root ``namespace``, this dependency does NOT 500 and
    does NOT 403 — it returns (allow) and defers to the catalog handler's own
    documented ``400``/``422`` validation. "No namespace extractable" is treated
    as create (no existing owner to protect), keeping parse-error handling in
    exactly one place (the handler). (ADR-028 §Decision 8 default.)

    Identity comes from the ADR-023 ``RequestUser`` seam (``get_request_user``)
    — never an inbound client header, never ``auth_method`` branching, and roles
    are read only from ``RequestUser.roles``, consistent with
    ``require_namespace_owner_or_admin``.

    Args:
        request: The inbound request — used both to read the body and to reach
            the wired catalog via ``request.app.state.services.catalog``.
        user: The authenticated principal resolved through the ADR-023 seam.

    Raises:
        HTTPException: 403 when a namespace IS extractable, the caller is not an
            admin, and the caller does not own the (already-existing) namespace.
    """
    if "admin" in user.roles:
        return
    namespace = await _extract_bundle_namespace(request)
    if namespace is None:
        # No namespace extractable ⇒ treat as create ⇒ allow; let the catalog
        # handler apply its own 400/422 for a malformed bundle (fail-open).
        return
    owner = _resolve_namespace_owner(request, namespace)
    if owner is None or owner == user.user_id:
        # owner is None ⇒ create (new namespace); owner == caller ⇒ overwrite-own.
        return
    logger.info(
        "owner-or-admin import gate denied overwrite",
        extra={"namespace": namespace, "user_id": user.user_id, "owner": owner},
    )
    raise HTTPException(status_code=403, detail="not authorized to modify this namespace")


async def _extract_bundle_namespace(request: Request) -> str | None:
    """Extract the bundle root ``namespace`` from the import request body.

    Mirrors the catalog ``import_namespace`` handler's parse shape (UTF-8 decode
    + ``yaml.safe_load``) so this dependency never imposes stricter parsing than
    the handler. The bundle is a single YAML document whose top-level keys
    include ``namespace`` (see ``akgentic.catalog.serialization.dump_namespace``
    / ``load_namespace``), so the root namespace is ``parsed["namespace"]`` when
    ``parsed`` is a mapping carrying a non-empty string under that key.

    Returns ``None`` — "no namespace extractable", the fail-open-to-handler
    signal — on any of: a body that is not valid UTF-8, a body that is not
    parseable YAML, a parsed document that is not a mapping, or a mapping with
    no usable (non-empty string) ``namespace`` key. The dependency never 500s on
    a bad body; it defers to the handler's own validation.

    Args:
        request: The inbound request whose (Starlette-cached) body is read.

    Returns:
        The bundle root ``namespace`` string, or ``None`` when none is
        extractable.
    """
    raw = await request.body()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    namespace = parsed.get("namespace")
    if isinstance(namespace, str) and namespace != "":
        return namespace
    return None


def _resolve_namespace_owner(request: Request, namespace: str) -> str | None:
    """Return the namespace owner's ``user_id`` via the team→meta anchor rule.

    Reads the already-wired catalog instance from
    ``request.app.state.services.catalog`` (the same object infra injects via
    ``set_catalog(services.catalog)`` in ``create_app``) and applies the
    ownership anchor identical to ``Catalog._check_ownership``: a
    ``kind="team"`` entry's ``user_id`` if one exists in the namespace, else
    the ``kind="meta"`` entry's ``user_id``. Returns ``None`` when neither
    anchor exists (owner unresolvable).

    The lookup uses the **unfiltered** ``Catalog.list_by_namespace`` (a
    repository pass-through that does NOT apply the ADR-009 §D2 visibility
    filter) and selects the anchor by **kind** in-process. This matters because
    both gates run *inside* the per-request ``Catalog.as_caller(caller)`` scope
    (Story 31.2 router-level dependency): a visibility-filtered ``Catalog.list``
    would hide another user's private namespace from the caller, making a
    foreign owner resolve to ``None``. An authorization gate must see the
    **true** owner regardless of the caller's tenant-visibility scope —
    otherwise the import gate's "owner is None ⇒ create ⇒ allow" branch would
    mis-classify an existing foreign namespace as a fresh create. The route gate
    (``require_namespace_owner_or_admin``) is unaffected in outcome: when the
    owner is foreign it now resolves to that foreign id (``!= caller`` ⇒ 403)
    instead of ``None`` (also ⇒ 403); when truly absent it still resolves to
    ``None``.

    Args:
        request: The inbound request carrying ``app.state.services.catalog``.
        namespace: The namespace whose owner is being resolved.

    Returns:
        The anchor entry's ``user_id``, or ``None`` if the namespace has
        neither a team nor a meta entry.
    """
    catalog = request.app.state.services.catalog
    entries = catalog.list_by_namespace(namespace)
    teams = [e for e in entries if e.kind == "team"]
    if teams:
        return str(teams[0].user_id)
    metas = [e for e in entries if e.kind == "meta"]
    if metas:
        return str(metas[0].user_id)
    return None
