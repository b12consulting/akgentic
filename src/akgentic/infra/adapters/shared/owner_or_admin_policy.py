"""OwnerOrAdminPolicy — the default per-team authorization rule (ADR-035 Decision 8).

The base-bundle default :class:`~akgentic.infra.protocols.authz.TeamAccessPolicy`
shared by all deployment tiers via ``TierServices.team_access_policy``'s
``default_factory``. Placed in ``adapters/shared`` (not ``adapters/community``)
because it is the base default for *every* tier, not community-specific —
mirroring the ``AuthStrategy`` protocol / ``NoAuth`` community-default split.
"""

from __future__ import annotations

from akgentic.infra.protocols.authz import TeamAccessContext
from akgentic.infra.server.auth import RequestUser

_ADMIN_ROLE = "admin"


class OwnerOrAdminPolicy:
    """The default owner-or-admin :class:`TeamAccessPolicy`.

    Reproduces the historical ``require_team_access`` rule byte-identically:
    allow iff the caller owns the team or holds the ``admin`` role. Constructible
    with no arguments so it can serve as ``TierServices.team_access_policy``'s
    ``default_factory``.
    """

    async def is_allowed(self, *, ctx: TeamAccessContext, user: RequestUser) -> bool:
        """Allow iff the caller owns the team or holds the ``admin`` role."""
        return ctx.owner_user_id == user.user_id or _ADMIN_ROLE in user.roles
