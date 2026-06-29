"""Tests for require_team_access — resource-ownership gate (ADR-034 §Layered authz, AC5)."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from akgentic.infra.server.auth import RequestUser
from akgentic.infra.server.routes._team_access import require_team_access


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


def _call(user: RequestUser, owner: str | None) -> RequestUser:
    """Invoke require_team_access with a stubbed seam; returns the user on allow."""
    process = None if owner is None else _FakeProcess(owner)
    service = _FakeTeamService(process)
    return require_team_access(team_id=uuid.uuid4(), user=user, service=service)  # type: ignore[arg-type]


def test_owner_is_allowed() -> None:
    """The team owner is allowed and the principal is returned."""
    user = RequestUser(user_id="alice")
    assert _call(user, owner="alice") is user


def test_admin_on_non_owned_team_is_allowed() -> None:
    """An admin bypasses ownership on a team they do not own."""
    user = RequestUser(user_id="bob", roles=["admin"])
    assert _call(user, owner="alice") is user


def test_non_owner_non_admin_is_404() -> None:
    """A non-owner non-admin is rejected with 404 (no existence leak)."""
    user = RequestUser(user_id="mallory")
    with pytest.raises(HTTPException) as excinfo:
        _call(user, owner="alice")
    assert excinfo.value.status_code == 404


def test_missing_team_is_404() -> None:
    """A missing team is rejected with 404 (same shape as a non-owned team)."""
    user = RequestUser(user_id="alice")
    with pytest.raises(HTTPException) as excinfo:
        _call(user, owner=None)
    assert excinfo.value.status_code == 404
