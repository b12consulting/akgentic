"""Request-user identity seam for tier-agnostic server routes (ADR-023)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RequestUser(BaseModel):
    """The authenticated principal for a server request, tier-agnostic.

    Deployment tiers adapt their own auth identity into this shape.
    Community supplies the anonymous default. Routes never see a
    tier-specific identity type.
    """

    user_id: str
    email: str = ""
    roles: list[str] = Field(default_factory=list)


def get_request_user() -> RequestUser:
    """Resolve the authenticated principal for the current request.

    Default (community tier): an anonymous principal. Department and
    enterprise OVERRIDE this dependency via ``app.dependency_overrides``
    so the same routes resolve a real authenticated identity.
    """
    return RequestUser(user_id="anonymous")
