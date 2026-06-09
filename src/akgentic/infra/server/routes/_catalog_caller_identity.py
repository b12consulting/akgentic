"""Per-request caller-identity wiring for the ``/admin/catalog/*`` mount.

Implements ADR-028 §Decision 7 (infra side) and ADR-023 §Decision 2/4/5: each
``/admin/catalog/*`` request runs inside the catalog's caller-identity scope
``Catalog.as_caller(request_user.user_id)``, where ``request_user`` is resolved
through the ADR-023 identity seam (``get_request_user``). Department/enterprise
override that seam via ``app.dependency_overrides`` so the real authenticated
``user_id`` flows through unchanged; community resolves the default
``"anonymous"`` and behaviour is byte-unchanged.

The identity is **server-derived** from the authenticated ``RequestUser`` —
never read from a spoofable inbound client header (ADR-023 §Alternative B). The
mechanism reuses the catalog's already-present ``Catalog.as_caller`` contextvar
affordance (``akgentic.catalog.catalog``); infra does NOT reimplement the
contextvar and the catalog never imports infra.

Wiring shape: a FastAPI generator dependency. Entering opens
``Catalog.as_caller(user_id)`` (which sets a ``ContextVar`` token); the
``finally`` of the generator guarantees the contextvar is reset on exit, so a
request resolving ``"gpiroux"`` cannot bleed its caller identity into a
subsequent request that resolves ``"anonymous"`` in the shared app
(ADR-028 §Decision 7 contextvar semantics).

The catalog *reading* this contextvar on write to stamp ``entry.user_id`` is
the companion catalog half (akgentic-catalog Epic 27 / Story 27.2).

Admin "see-all" reads (ADR-028 §Decision 9): an explicit, admin-only, opt-in
``?all=true`` query parameter makes infra run the read **unscoped** — it does
NOT enter ``Catalog.as_caller``, leaving the catalog's ``_caller_user_id``
contextvar ``None``, so the catalog's ``list``/``get`` skip the visibility
filter and return everything (the existing community ``caller is None ⇒ no
filter`` path, reused as the "see all" lever). The catalog never learns about
roles; the decision lives entirely here in infra. The switch is:

* **admin-only / opt-in** — honoured iff ``"admin" in user.roles`` AND
  ``all=true``. A non-admin passing ``all=true`` is treated exactly as
  ``all=false`` (scoped); the param is silently ineffective, never a 403, never
  a privilege grant.
* **reads only** — unscoping is gated on ``request.method == "GET"``. Writes
  (``PUT``/``DELETE``/``POST``, including ``POST .../search``) always run
  scoped regardless of ``?all=``, so ownership-stamping on the write path is
  unaffected (an unscoped write would stamp ``anonymous``). This means the
  ``POST .../search`` read stays scoped by design — see the dev note in
  Story 31.4 (search is "sufficient additional coverage", not
  mandatory-unscoped; the GET-only gate is the safe shape).
* **default unchanged** — ``all`` absent / ``all=false`` runs scoped for
  everyone (including admins) — byte-identical to before.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import Depends, Query, Request

from akgentic.catalog.catalog import Catalog
from akgentic.infra.server.auth import RequestUser, get_request_user

__all__ = ["scope_catalog_caller_identity"]

logger = logging.getLogger(__name__)

_ADMIN_ROLE = "admin"


async def scope_catalog_caller_identity(
    request: Request,
    user: RequestUser = Depends(get_request_user),
    all_: bool = Query(False, alias="all"),
) -> AsyncIterator[None]:
    """Scope the request to ``Catalog.as_caller(user.user_id)`` unless an admin opts in via ``all``.

    FastAPI resolves ``user`` through the ADR-023 seam (honouring each tier's
    ``dependency_overrides``), then — for the default case — enters the
    catalog's caller-identity context manager for the request body. The
    ``finally`` in ``Catalog.as_caller`` resets the contextvar token on exit,
    so the identity is scoped to a single request and never leaks across
    requests.

    Declaring ``all_`` here (router-level dependency) makes ``?all=`` an
    accepted query parameter on **every** ``/admin/catalog/*`` route without
    editing any catalog read handler (the catalog router lives in
    ``akgentic-catalog``). The Python name ``all_`` is aliased to the wire name
    ``all`` so the switch is literally ``?all=true`` while avoiding shadowing
    the ``all`` builtin.

    The "see all" predicate (ADR-028 §Decision 9)::

        unscoped = all_ and (admin in roles) and method == "GET"

    When ``unscoped`` is ``True`` the dependency yields **without** entering
    ``Catalog.as_caller`` — the contextvar stays ``None`` and the catalog
    returns everything (the community ``caller is None ⇒ no filter`` path).
    Otherwise the request runs scoped under ``Catalog.as_caller(user.user_id)``:

    * non-admin + ``all=true`` ⇒ scoped (owner+public only) — fail-safe, no 403;
    * admin + ``all=false``/absent ⇒ scoped — default, owner+public only;
    * any write (``PUT``/``DELETE``/``POST``, incl. ``.../search``) ⇒ scoped
      regardless of ``?all=`` — reads-only unscoping, so ownership-stamping on
      the write path is never affected;
    * community (anonymous) ⇒ never has the ``admin`` role ⇒ always scoped.

    No 403 is ever raised by ``all`` — the param is a visibility lever, not an
    authorization gate.

    This dependency is **async on purpose**: an async generator dependency is
    entered on the request's own asyncio task, so the ``ContextVar`` set by
    ``Catalog.as_caller`` lives in the same ``contextvars`` context that
    FastAPI snapshots when it dispatches the path operation — whether that
    handler is ``async`` (same task) or ``sync`` (run in a threadpool with a
    *copied* context). A sync generator dependency would instead set the
    contextvar in a throwaway threadpool thread whose context never reaches the
    handler, so the identity would be invisible.

    The caller ``user_id`` is taken from the authenticated ``RequestUser``
    (server-side); no inbound client header is consulted, so the spoof surface
    rejected by ADR-023 §Alternative B does not exist here.

    Yields:
        ``None`` — the dependency exists purely for its context-manager side
        effect of setting (or, when unscoped, deliberately NOT setting) the
        caller-identity contextvar.
    """
    unscoped = all_ and _ADMIN_ROLE in user.roles and request.method == "GET"
    if unscoped:
        # Admin opted into the see-all read: do NOT enter as_caller so the
        # catalog's visibility filter is bypassed (caller is None => no filter).
        # The mutation-log middleware only logs writes, so emit a dedicated
        # INFO line here to make the unscoping decision itself auditable.
        logger.info(
            "admin catalog unscoped read",
            extra={
                "user_id": user.user_id,
                "principal_id": user.user_id,
                "path": request.url.path,
                "unscoped": True,
            },
        )
        yield
        return
    with Catalog.as_caller(user.user_id):
        yield
