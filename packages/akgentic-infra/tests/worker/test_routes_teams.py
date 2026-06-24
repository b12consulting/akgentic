"""Shared worker team-route tests — create-team populates the runtime cache.

These tests pin ADR-001 Story 1: the shared ``create_team`` handler must wrap
the new runtime in a ``LocalTeamHandle`` and ``runtime_cache.store`` it before
returning, so that every other route on the same router — all of which resolve
the live handle via ``runtime_cache.get`` — can reach the freshly created team.
Without the store, a follow-up ``POST /teams/{id}/message`` 404s on a cache miss.

The handlers are exercised directly (the existing worker-route test style) with
a real ``LocalRuntimeCache`` and lightweight stubs for the team manager and
worker handle, so the assertion is on the genuine cache interaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from akgentic.team.models import Process, TeamCard, TeamStatus

from akgentic.infra.adapters.community.local_runtime_cache import LocalRuntimeCache
from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.infra.server.models import SendMessageRequest
from akgentic.infra.worker.routes.teams import (
    WorkerCreateTeamRequest,
    create_team,
    delete_team,
    send_message,
    stop_team,
)

_TEAM_CARD_PAYLOAD = {
    "name": "Test Team",
    "description": "worker-route test team",
    "entry_point": {
        "card": {
            "role": "Human",
            "description": "Human user interface",
            "skills": [],
            "agent_class": "akgentic.core.agent.Akgent",
            "config": {"name": "@Human", "role": "Human"},
            "routes_to": ["@Manager"],
        },
        "headcount": 1,
        "members": [],
    },
    "members": [
        {
            "card": {
                "role": "Manager",
                "description": "Test manager agent",
                "skills": ["coordination"],
                "agent_class": "akgentic.core.agent.Akgent",
                "config": {"name": "@Manager", "role": "Manager"},
                "routes_to": [],
            },
            "headcount": 1,
            "members": [],
        },
    ],
    "message_types": [{"__type__": "akgentic.core.messages.UserMessage"}],
    "agent_profiles": [],
}


class _FakeRuntime:
    """Stand-in for ``TeamRuntime`` exposing only what ``LocalTeamHandle`` uses.

    ``LocalTeamHandle`` reads ``runtime.id`` and delegates ``send`` to it; a
    full actor-backed ``TeamRuntime`` is unnecessary for a route-level cache test.
    """

    def __init__(self, team_id: uuid.UUID) -> None:
        self.id = team_id
        self.sent: list[str] = []

    def send(self, content: str) -> None:
        self.sent.append(content)


def _build_team_card() -> TeamCard:
    """Build a validated minimal TeamCard for the worker create request."""
    return TeamCard.model_validate(_TEAM_CARD_PAYLOAD)


def _build_process(team_id: uuid.UUID, team_card: TeamCard) -> Process:
    """Build the persisted Process metadata the worker handle returns."""
    now = datetime.now(UTC)
    return Process(
        team_id=team_id,
        team_card=team_card,
        status=TeamStatus.RUNNING,
        user_id="user-1",
        created_at=now,
        updated_at=now,
    )


def _build_services(
    runtime: _FakeRuntime,
    process: Process,
    cache: LocalRuntimeCache,
) -> SimpleNamespace:
    """Assemble a WorkerServices-shaped stub around a real LocalRuntimeCache."""
    return SimpleNamespace(
        team_manager=SimpleNamespace(create_team=lambda **_kwargs: runtime),
        worker_handle=SimpleNamespace(get_team=lambda _tid: process),
        runtime_cache=cache,
    )


def _make_create_body(team_id: uuid.UUID, team_card: TeamCard) -> WorkerCreateTeamRequest:
    """Build a valid WorkerCreateTeamRequest for the given team."""
    return WorkerCreateTeamRequest(
        team_card=team_card,
        user_id="user-1",
        user_email="user@example.com",
        team_id=team_id,
    )


def test_create_team_stores_handle_in_cache() -> None:
    """AC#1: POST /teams stores a LocalTeamHandle before returning (201 shape)."""
    team_id = uuid.uuid4()
    team_card = _build_team_card()
    runtime = _FakeRuntime(team_id)
    process = _build_process(team_id, team_card)
    cache = LocalRuntimeCache()
    services = _build_services(runtime, process, cache)

    response = create_team(_make_create_body(team_id, team_card), services)  # type: ignore[arg-type]

    cached = cache.get(team_id)
    assert isinstance(cached, LocalTeamHandle)
    assert cached.team_id == team_id
    # Response shape / id unchanged from today.
    assert response.team_id == team_id
    assert response.name == "Test Team"
    assert response.user_id == "user-1"
    assert response.status == TeamStatus.RUNNING.value


def test_create_then_message_hits_cache_and_returns_204() -> None:
    """AC#2: a message sent right after create resolves the cached handle."""
    team_id = uuid.uuid4()
    team_card = _build_team_card()
    runtime = _FakeRuntime(team_id)
    process = _build_process(team_id, team_card)
    cache = LocalRuntimeCache()
    services = _build_services(runtime, process, cache)

    create_team(_make_create_body(team_id, team_card), services)  # type: ignore[arg-type]

    # send_message returns None (HTTP 204) only if the cache lookup hit.
    result = send_message(
        team_id,
        SendMessageRequest(content="hello team"),
        services,  # type: ignore[arg-type]
    )

    assert result is None
    assert runtime.sent == ["hello team"]


def test_create_team_store_is_idempotent_overwrite() -> None:
    """AC#3: re-creating the same team id overwrites the cache entry harmlessly."""
    team_id = uuid.uuid4()
    team_card = _build_team_card()
    cache = LocalRuntimeCache()

    first_runtime = _FakeRuntime(team_id)
    services_a = _build_services(first_runtime, _build_process(team_id, team_card), cache)
    create_team(_make_create_body(team_id, team_card), services_a)  # type: ignore[arg-type]
    first_handle = cache.get(team_id)

    second_runtime = _FakeRuntime(team_id)
    services_b = _build_services(second_runtime, _build_process(team_id, team_card), cache)
    create_team(_make_create_body(team_id, team_card), services_b)  # type: ignore[arg-type]
    second_handle = cache.get(team_id)

    assert isinstance(second_handle, LocalTeamHandle)
    assert second_handle is not first_handle
    assert second_handle.team_id == team_id


def _build_lifecycle_services(
    runtime: _FakeRuntime,
    process: Process,
    cache: LocalRuntimeCache,
) -> SimpleNamespace:
    """WorkerServices stub whose worker_handle also supports stop/delete (no-ops)."""
    return SimpleNamespace(
        team_manager=SimpleNamespace(create_team=lambda **_kwargs: runtime),
        worker_handle=SimpleNamespace(
            get_team=lambda _tid: process,
            stop_team=lambda _tid: None,
            delete_team=lambda _tid: None,
        ),
        runtime_cache=cache,
    )


def test_stop_team_evicts_handle_from_cache() -> None:
    """Regression: stopping a team removes its handle so the cache cannot pin it.

    Without the eviction the worker-lifetime LocalRuntimeCache retains the whole
    TeamRuntime graph (proxies, ActorRefs, pykka AttrInfo) of every stopped team.
    """
    team_id = uuid.uuid4()
    team_card = _build_team_card()
    cache = LocalRuntimeCache()
    services = _build_lifecycle_services(
        _FakeRuntime(team_id), _build_process(team_id, team_card), cache
    )

    create_team(_make_create_body(team_id, team_card), services)  # type: ignore[arg-type]
    assert cache.get(team_id) is not None

    stop_team(team_id, services)  # type: ignore[arg-type]

    assert cache.get(team_id) is None, "stop_team must evict the handle from runtime_cache"


def test_delete_team_evicts_handle_from_cache() -> None:
    """Regression: deleting a team removes its handle from the cache."""
    team_id = uuid.uuid4()
    team_card = _build_team_card()
    cache = LocalRuntimeCache()
    services = _build_lifecycle_services(
        _FakeRuntime(team_id), _build_process(team_id, team_card), cache
    )

    create_team(_make_create_body(team_id, team_card), services)  # type: ignore[arg-type]
    assert cache.get(team_id) is not None

    delete_team(team_id, services)  # type: ignore[arg-type]

    assert cache.get(team_id) is None, "delete_team must evict the handle from runtime_cache"


def test_send_message_without_create_returns_404() -> None:
    """Control: an un-stored team still 404s, proving the cache gates the route."""
    from fastapi import HTTPException

    team_id = uuid.uuid4()
    cache = LocalRuntimeCache()
    services = SimpleNamespace(runtime_cache=cache)

    with pytest.raises(HTTPException) as exc_info:
        send_message(
            team_id,
            SendMessageRequest(content="nobody home"),
            services,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404
