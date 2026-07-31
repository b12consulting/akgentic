"""Tests for the per-team resource-ownership gates.

``require_team_access`` / ``require_workspace_access`` (ADR-034 §Layered authz,
ADR-035 Decision 8) are ``async`` and delegate the allow/deny rule to the wired
``TeamAccessPolicy`` while keeping the infra-owned team lookup + 404-over-403
no-existence-leak machinery. Direct unit calls pass ``policy=`` explicitly
(``Depends`` defaults are only resolved by FastAPI at request time).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from akgentic.infra.adapters.shared.owner_or_admin_policy import OwnerOrAdminPolicy
from akgentic.infra.protocols.authz import TeamAccessContext, TeamAccessPolicy
from akgentic.infra.server.auth import RequestUser
from akgentic.infra.server.routes._team_access import (
    require_team_access,
    require_workspace_access,
)


class _FakeProcess:
    """Minimal team-owner stand-in carrying just the ownership field."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


class _FakeTeamService:
    """Team-access seam stub returning a fixed Process (or None for missing)."""

    def __init__(self, process: _FakeProcess | None) -> None:
        self._process = process

    def get_team(self, team_id: uuid.UUID) -> _FakeProcess | None:
        return self._process


class _FixedPolicy:
    """Fake ``TeamAccessPolicy`` returning a fixed verdict and recording calls."""

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict
        self.calls: list[TeamAccessContext] = []

    async def is_allowed(self, *, ctx: TeamAccessContext, user: RequestUser) -> bool:
        self.calls.append(ctx)
        return self._verdict


class _RaisingPolicy:
    """Fake policy that fails the test if consulted (missing-team assertion)."""

    async def is_allowed(self, *, ctx: TeamAccessContext, user: RequestUser) -> bool:
        raise AssertionError("policy must not be consulted on the pass-through path")


async def _call(
    user: RequestUser, owner: str | None, policy: TeamAccessPolicy | None = None
) -> RequestUser:
    """Invoke require_team_access with a stubbed seam; returns the user on allow."""
    process = None if owner is None else _FakeProcess(owner)
    service = _FakeTeamService(process)
    resolved = OwnerOrAdminPolicy() if policy is None else policy
    return await require_team_access(
        team_id=uuid.uuid4(),
        user=user,
        service=service,  # type: ignore[arg-type]
        policy=resolved,
    )


async def _call_workspace(
    user: RequestUser,
    workspace_id: str | None,
    owner: str | None,
    policy: TeamAccessPolicy | None = None,
) -> RequestUser:
    """Invoke require_workspace_access with a stubbed seam."""
    process = None if owner is None else _FakeProcess(owner)
    service = _FakeTeamService(process)
    resolved = OwnerOrAdminPolicy() if policy is None else policy
    return await require_workspace_access(
        workspace_id=workspace_id,
        user=user,
        service=service,  # type: ignore[arg-type]
        policy=resolved,
    )


# --- require_team_access: default (owner-or-admin) policy ---------------------


async def test_owner_is_allowed() -> None:
    """The team owner is allowed and the principal is returned."""
    user = RequestUser(user_id="alice")
    assert await _call(user, owner="alice") is user


async def test_admin_on_non_owned_team_is_allowed() -> None:
    """An admin bypasses ownership on a team they do not own."""
    user = RequestUser(user_id="bob", roles=["admin"])
    assert await _call(user, owner="alice") is user


async def test_non_owner_non_admin_is_404() -> None:
    """A non-owner non-admin is rejected with 404 (no existence leak)."""
    user = RequestUser(user_id="mallory")
    with pytest.raises(HTTPException) as excinfo:
        await _call(user, owner="alice")
    assert excinfo.value.status_code == 404


async def test_missing_team_is_404() -> None:
    """A missing team is rejected with 404 (same shape as a non-owned team)."""
    user = RequestUser(user_id="alice")
    with pytest.raises(HTTPException) as excinfo:
        await _call(user, owner=None)
    assert excinfo.value.status_code == 404


async def test_missing_team_does_not_consult_policy() -> None:
    """AC #4: a missing team is 404 raised BEFORE the policy is awaited."""
    user = RequestUser(user_id="alice")
    with pytest.raises(HTTPException) as excinfo:
        await _call(user, owner=None, policy=_RaisingPolicy())
    assert excinfo.value.status_code == 404


# --- require_team_access: injected non-default policy (AC #8) -----------------


async def test_injected_true_policy_lets_non_owner_through() -> None:
    """AC #8: a fake returning True lets a non-owner pass (returns the user)."""
    user = RequestUser(user_id="mallory")
    policy = _FixedPolicy(True)
    assert await _call(user, owner="alice", policy=policy) is user
    assert len(policy.calls) == 1


async def test_injected_false_policy_gives_owner_404() -> None:
    """AC #8: a fake returning False makes even the owner get 404."""
    user = RequestUser(user_id="alice")
    policy = _FixedPolicy(False)
    with pytest.raises(HTTPException) as excinfo:
        await _call(user, owner="alice", policy=policy)
    assert excinfo.value.status_code == 404
    assert len(policy.calls) == 1


# --- require_workspace_access: pass-throughs (no policy call) -----------------


async def test_workspace_none_passes_through() -> None:
    """AC #5: an omitted workspace_id passes through without a policy call."""
    user = RequestUser(user_id="mallory")
    assert (
        await _call_workspace(user, workspace_id=None, owner=None, policy=_RaisingPolicy())
        is user
    )


async def test_workspace_non_uuid_passes_through() -> None:
    """AC #5: a non-UUID workspace_id (shared/agent segment) passes through."""
    user = RequestUser(user_id="mallory")
    assert (
        await _call_workspace(
            user, workspace_id="agent-segment", owner=None, policy=_RaisingPolicy()
        )
        is user
    )


async def test_workspace_uuid_naming_no_team_passes_through() -> None:
    """AC #5: a UUID that names no team passes through without a policy call."""
    user = RequestUser(user_id="mallory")
    assert (
        await _call_workspace(
            user, workspace_id=str(uuid.uuid4()), owner=None, policy=_RaisingPolicy()
        )
        is user
    )


# --- require_workspace_access: existing-team branch (AC #5, #8) ---------------


async def test_workspace_foreign_team_default_policy_is_404() -> None:
    """A foreign team's workspace_id is 404 under the default policy."""
    user = RequestUser(user_id="mallory")
    with pytest.raises(HTTPException) as excinfo:
        await _call_workspace(user, workspace_id=str(uuid.uuid4()), owner="alice")
    assert excinfo.value.status_code == 404


async def test_workspace_owner_default_policy_passes() -> None:
    """The owner accessing their own team's workspace passes under the default."""
    user = RequestUser(user_id="alice")
    assert await _call_workspace(user, workspace_id=str(uuid.uuid4()), owner="alice") is user


async def test_workspace_foreign_team_denied_is_404() -> None:
    """AC #8: an injected fake returning False on a foreign team → 404."""
    user = RequestUser(user_id="mallory")
    policy = _FixedPolicy(False)
    with pytest.raises(HTTPException) as excinfo:
        await _call_workspace(user, workspace_id=str(uuid.uuid4()), owner="alice", policy=policy)
    assert excinfo.value.status_code == 404
    assert len(policy.calls) == 1


async def test_workspace_foreign_team_allowed_passes() -> None:
    """AC #8: an injected fake returning True on a foreign team returns the user."""
    user = RequestUser(user_id="mallory")
    policy = _FixedPolicy(True)
    result = await _call_workspace(
        user, workspace_id=str(uuid.uuid4()), owner="alice", policy=policy
    )
    assert result is user
    assert len(policy.calls) == 1
