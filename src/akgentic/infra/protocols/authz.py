"""Tier-agnostic per-team authorization contract (ADR-035 Decision 8).

Authorization (may this principal access this team?) is kept separate from
authentication (who is this principal?, ``protocols/auth.py`` ``AuthStrategy``).
:class:`TeamAccessPolicy` is a **sibling** of ``AuthStrategy``, not a member of
it. Infra owns the team lookup and the load-bearing 404-over-403
no-existence-leak machinery; only the allow/deny *rule* is pluggable. A policy
consumes the neutral :class:`TeamAccessContext` — never the team ``Process`` —
so a tier or library policy never has to import ``akgentic.team.models``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from akgentic.infra.server.auth import RequestUser


class TeamAccessContext(BaseModel):
    """Neutral team-access decision context handed to a :class:`TeamAccessPolicy`.

    Carries only the two fields the decision rule needs — the target
    ``team_id`` and the team owner's ``owner_user_id`` — so a tier or library
    policy consumes this DTO rather than the team ``Process``. It round-trips
    through Pydantic (construct, ``model_dump``, re-validate) unchanged.
    """

    team_id: uuid.UUID
    owner_user_id: str


@runtime_checkable
class TeamAccessPolicy(Protocol):
    """Tier-agnostic per-team authorization contract (ADR-035 Decision 8).

    The single member is ``async`` so a tier policy may consult an external
    membership / RBAC store. The community default
    (``adapters/shared/owner_or_admin_policy.py``) reproduces the historical
    owner-or-admin rule; a tier substitutes its own without re-implementing the
    infra-owned team lookup or the 404-over-403 gate.
    """

    async def is_allowed(self, *, ctx: TeamAccessContext, user: RequestUser) -> bool:
        """Return ``True`` iff ``user`` may access the team described by ``ctx``."""
        ...
