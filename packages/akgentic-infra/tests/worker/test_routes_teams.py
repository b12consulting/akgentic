"""Shared worker team-route tests for create, typed-send and notification surfaces.

The worker ``/message*`` routes accept a pre-formed ``Message`` wire envelope
alongside the plain-string path, and ``/notification`` publishes a pre-formed
Message through ``handle.emitMessage`` with no agent processing.

The handlers are exercised directly with a real ``LocalRuntimeCache`` seeded
with a ``LocalTeamHandle`` over a stub runtime, so the assertion is on the
genuine decode-and-delegate path rather than on team creation.

``POST /teams`` is exercised the same way: the create handler must forward
``user_email`` / ``team_id`` to the team manager and populate the very cache the
``/message*`` routes read, so a freshly created team is immediately reachable.

``POST /teams/{id}/stop`` and ``DELETE /teams/{id}`` close the same loop from the
other end: each evicts the cached handle only once the lifecycle call succeeded.
A rejected call leaves the handle in place — a delete refused because the team is
still RUNNING has to stay reachable — while the mapped HTTP error propagates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from akgentic.core.messages.message import UserMessage
from akgentic.team.models import Process, TeamCard, TeamStatus
from fastapi import HTTPException

from akgentic.infra.adapters.community.local_runtime_cache import LocalRuntimeCache
from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.infra.server.models import EmitMessageRequest, SendMessageRequest
from akgentic.infra.worker.routes.teams import (
    WorkerCreateTeamRequest,
    create_team,
    delete_team,
    emit_notification,
    send_message,
    stop_team,
)

_BAD_ENVELOPE = {"__model__": "akgentic.core.messages.message.NoSuchMessage"}
_NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


class _FakeRuntime:
    """Stand-in for ``TeamRuntime`` exposing only what ``LocalTeamHandle`` uses."""

    def __init__(self, team_id: uuid.UUID) -> None:
        self.id = team_id
        self.sent: list[object] = []
        self.emitted: list[object] = []

    def send(self, content: object) -> None:
        self.sent.append(content)

    def emitMessage(self, message: object) -> None:  # noqa: N802
        self.emitted.append(message)


def _seed_cache(team_id: uuid.UUID) -> tuple[_FakeRuntime, SimpleNamespace]:
    """Store a live handle for ``team_id`` and return the runtime plus services."""
    runtime = _FakeRuntime(team_id)
    cache = LocalRuntimeCache()
    cache.store(team_id, LocalTeamHandle(runtime))  # type: ignore[arg-type]
    return runtime, SimpleNamespace(runtime_cache=cache)


def _make_team_card(name: str = "Test Team") -> TeamCard:
    """Minimal ``TeamCard`` double — the create handler only forwards it."""
    card = MagicMock(spec=TeamCard)
    card.name = name
    return card  # type: ignore[no-any-return]


def _make_process(team_id: uuid.UUID, user_id: str) -> Process:
    """Persisted metadata the worker handle returns after a create."""
    return Process(
        team_id=team_id,
        team_card=_make_team_card(),
        status=TeamStatus.RUNNING,
        user_id=user_id,
        created_at=_NOW,
        updated_at=_NOW,
    )


@dataclass(frozen=True)
class _CreateCall:
    """One recorded ``create_team`` invocation — typed, so assertions are checked."""

    team_card: TeamCard
    user_id: str
    user_email: str
    team_id: uuid.UUID | None


class _RecordingTeamManager:
    """Records ``create_team`` kwargs and honours a caller-supplied ``team_id``.

    The signature has no defaults on purpose: a handler that fails to forward
    ``user_email`` or ``team_id`` raises ``TypeError`` instead of passing.
    """

    def __init__(self) -> None:
        self.calls: list[_CreateCall] = []
        self.runtime: _FakeRuntime | None = None

    def create_team(
        self,
        *,
        team_card: TeamCard,
        user_id: str,
        user_email: str,
        team_id: uuid.UUID | None,
    ) -> _FakeRuntime:
        self.calls.append(
            _CreateCall(
                team_card=team_card,
                user_id=user_id,
                user_email=user_email,
                team_id=team_id,
            )
        )
        self.runtime = _FakeRuntime(team_id or uuid.uuid4())
        return self.runtime


class _RecordingWorkerHandle:
    """Returns the created ``Process`` and records cache state at lookup time.

    ``found=False`` reproduces the created-but-not-persisted path, where
    ``get_team`` returns ``None`` exactly as the real worker handle may.
    """

    def __init__(self, cache: LocalRuntimeCache, user_id: str, *, found: bool = True) -> None:
        self._cache = cache
        self._user_id = user_id
        self._found = found
        self.cached_at_lookup: list[bool] = []

    def get_team(self, team_id: uuid.UUID) -> Process | None:
        self.cached_at_lookup.append(self._cache.get(team_id) is not None)
        if not self._found:
            return None
        return _make_process(team_id, self._user_id)


def _create_services(user_id: str = "user-1", *, found: bool = True) -> SimpleNamespace:
    """Worker services stub for the create route: manager + handle + real cache."""
    cache = LocalRuntimeCache()
    return SimpleNamespace(
        team_manager=_RecordingTeamManager(),
        worker_handle=_RecordingWorkerHandle(cache, user_id, found=found),
        runtime_cache=cache,
    )


def test_create_team_stores_a_local_team_handle_before_the_process_lookup() -> None:
    """The created runtime is cached under ``runtime.id`` ahead of ``get_team``."""
    services = _create_services()
    body = WorkerCreateTeamRequest(team_card=_make_team_card(), user_id="user-1")

    response = create_team(body, services)  # type: ignore[arg-type]

    runtime = services.team_manager.runtime
    assert runtime is not None
    cached = services.runtime_cache.get(runtime.id)
    assert isinstance(cached, LocalTeamHandle)
    assert cached.team_id == runtime.id
    # The store must precede the worker-handle lookup and the response build.
    assert services.worker_handle.cached_at_lookup == [True]
    # The 201 body still carries the persisted Process, field for field.
    assert response.team_id == runtime.id
    assert response.status == TeamStatus.RUNNING.value
    assert response.name == "Test Team"
    assert response.user_id == "user-1"
    assert response.created_at == _NOW
    assert response.updated_at == _NOW


def test_create_team_makes_the_team_reachable_by_the_message_route() -> None:
    """A create followed by a send resolves through the cache — no 404."""
    services = _create_services()
    body = WorkerCreateTeamRequest(team_card=_make_team_card(), user_id="user-1")

    response = create_team(body, services)  # type: ignore[arg-type]
    original = UserMessage(content="post-create hello")
    result = send_message(
        response.team_id,
        SendMessageRequest(message=original.model_dump(mode="json")),
        services,  # type: ignore[arg-type]
    )

    assert result is None
    runtime = services.team_manager.runtime
    assert runtime is not None
    assert runtime.sent == [original]


def test_create_team_defaults_forward_empty_email_and_no_team_id() -> None:
    """A body carrying only ``team_card`` + ``user_id`` still validates."""
    services = _create_services()
    body = WorkerCreateTeamRequest(team_card=_make_team_card(), user_id="user-1")

    assert body.user_email == ""
    assert body.team_id is None

    create_team(body, services)  # type: ignore[arg-type]

    call = services.team_manager.calls[0]
    assert call.user_id == "user-1"
    assert call.user_email == ""
    assert call.team_id is None


def test_create_team_forwards_supplied_user_email_and_team_id() -> None:
    """Both new fields reach the team manager, and the id is honoured."""
    services = _create_services()
    requested_id = uuid.uuid4()
    body = WorkerCreateTeamRequest(
        team_card=_make_team_card(),
        user_id="user-1",
        user_email="user@example.com",
        team_id=requested_id,
    )

    response = create_team(body, services)  # type: ignore[arg-type]

    call = services.team_manager.calls[0]
    assert call.user_email == "user@example.com"
    assert call.team_id == requested_id
    assert response.team_id == requested_id
    assert services.runtime_cache.get(requested_id) is not None


def test_create_team_evicts_the_handle_when_the_process_lookup_fails() -> None:
    """A create that never reaches the event store leaves no handle behind."""
    services = _create_services(found=False)
    body = WorkerCreateTeamRequest(team_card=_make_team_card(), user_id="user-1")

    with pytest.raises(RuntimeError, match="not found in event store"):
        create_team(body, services)  # type: ignore[arg-type]

    runtime = services.team_manager.runtime
    assert runtime is not None
    assert services.runtime_cache.get(runtime.id) is None


def test_send_message_typed_envelope_delivers_decoded_message() -> None:
    """The worker /message route accepts a serialized Message envelope (str|Message path)."""
    team_id = uuid.uuid4()
    runtime, services = _seed_cache(team_id)

    original = UserMessage(content="typed-hello")
    result = send_message(
        team_id,
        SendMessageRequest(message=original.model_dump(mode="json")),
        services,  # type: ignore[arg-type]
    )

    assert result is None
    assert len(runtime.sent) == 1
    delivered = runtime.sent[0]
    assert type(delivered) is UserMessage
    assert delivered == original


def test_send_message_bad_envelope_returns_400() -> None:
    """A message envelope with an unimportable __model__ tag surfaces a 400."""
    team_id = uuid.uuid4()
    runtime, services = _seed_cache(team_id)

    with pytest.raises(HTTPException) as exc_info:
        send_message(
            team_id,
            SendMessageRequest(message=_BAD_ENVELOPE),
            services,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert runtime.sent == []


def test_send_message_non_message_envelope_returns_400() -> None:
    """A message envelope that decodes to a non-Message surfaces a 400."""
    team_id = uuid.uuid4()
    runtime, services = _seed_cache(team_id)

    with pytest.raises(HTTPException) as exc_info:
        send_message(
            team_id,
            SendMessageRequest(message={"hello": "world"}),
            services,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert runtime.sent == []


def test_emit_notification_delivers_decoded_message_and_returns_204() -> None:
    """The worker /notification route decodes the envelope and calls emitMessage.

    The message is published through ``handle.emitMessage`` (durable store +
    live stream) with no agent processing, so ``send`` is never touched.
    """
    team_id = uuid.uuid4()
    runtime, services = _seed_cache(team_id)

    original = UserMessage(content="notify-hello")
    result = emit_notification(
        team_id,
        EmitMessageRequest(message=original.model_dump(mode="json")),
        services,  # type: ignore[arg-type]
    )

    assert result is None
    assert runtime.sent == []
    assert len(runtime.emitted) == 1
    delivered = runtime.emitted[0]
    assert type(delivered) is UserMessage
    assert delivered == original


def test_emit_notification_without_handle_returns_404() -> None:
    """A valid envelope for an un-stored team 404s (the cache gates the route)."""
    body = EmitMessageRequest(message=UserMessage(content="nobody home").model_dump(mode="json"))
    services = SimpleNamespace(runtime_cache=LocalRuntimeCache())

    with pytest.raises(HTTPException) as exc_info:
        emit_notification(uuid.uuid4(), body, services)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404


def test_emit_notification_bad_envelope_returns_400() -> None:
    """A notification envelope with an unimportable __model__ tag surfaces a 400."""
    team_id = uuid.uuid4()
    runtime, services = _seed_cache(team_id)

    with pytest.raises(HTTPException) as exc_info:
        emit_notification(
            team_id,
            EmitMessageRequest(message=_BAD_ENVELOPE),
            services,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert runtime.emitted == []


class _LifecycleWorkerHandle:
    """Records stop/delete calls and optionally raises a configured ValueError.

    The signatures mirror the real worker handle exactly, so a handler that
    drifts surfaces as a type error rather than a silent pass.
    """

    def __init__(self, error: ValueError | None = None) -> None:
        self._error = error
        self.stopped: list[uuid.UUID] = []
        self.deleted: list[uuid.UUID] = []

    def stop_team(self, team_id: uuid.UUID) -> None:
        self.stopped.append(team_id)
        if self._error is not None:
            raise self._error

    def delete_team(self, team_id: uuid.UUID) -> None:
        self.deleted.append(team_id)
        if self._error is not None:
            raise self._error


def _lifecycle_services(team_id: uuid.UUID, *, error: ValueError | None = None) -> SimpleNamespace:
    """Services stub for stop/delete: a real cache seeded with ``team_id``."""
    cache = LocalRuntimeCache()
    cache.store(team_id, LocalTeamHandle(_FakeRuntime(team_id)))  # type: ignore[arg-type]
    return SimpleNamespace(
        runtime_cache=cache,
        worker_handle=_LifecycleWorkerHandle(error),
    )


def test_stop_team_releases_the_cached_handle() -> None:
    """A successful stop returns 204 (None) and leaves nothing in the cache."""
    team_id = uuid.uuid4()
    services = _lifecycle_services(team_id)

    result = stop_team(team_id, services)  # type: ignore[arg-type]

    assert result is None
    assert services.worker_handle.stopped == [team_id]
    assert services.runtime_cache.get(team_id) is None


def test_stop_team_keeps_the_handle_when_the_stop_fails() -> None:
    """A rejected stop leaves the handle cached, and the mapped 404 propagates."""
    team_id = uuid.uuid4()
    services = _lifecycle_services(team_id, error=ValueError(f"Team {team_id} not found"))

    with pytest.raises(HTTPException) as exc_info:
        stop_team(team_id, services)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404
    assert services.runtime_cache.get(team_id) is not None


def test_stop_team_without_a_cached_handle_is_a_no_op() -> None:
    """Evicting an absent team_id raises nothing and spares unrelated entries."""
    team_id = uuid.uuid4()
    other_id = uuid.uuid4()
    services = _lifecycle_services(other_id)

    result = stop_team(team_id, services)  # type: ignore[arg-type]

    assert result is None
    assert services.worker_handle.stopped == [team_id]
    assert services.runtime_cache.get(team_id) is None
    assert services.runtime_cache.get(other_id) is not None


def test_delete_team_releases_the_cached_handle() -> None:
    """A successful delete returns 204 (None) and leaves nothing in the cache."""
    team_id = uuid.uuid4()
    services = _lifecycle_services(team_id)

    result = delete_team(team_id, services)  # type: ignore[arg-type]

    assert result is None
    assert services.worker_handle.deleted == [team_id]
    assert services.runtime_cache.get(team_id) is None


def test_delete_team_keeps_the_handle_when_the_delete_fails() -> None:
    """A rejected delete leaves the handle cached, and the mapped 404 propagates."""
    team_id = uuid.uuid4()
    services = _lifecycle_services(team_id, error=ValueError(f"Team {team_id} not found"))

    with pytest.raises(HTTPException) as exc_info:
        delete_team(team_id, services)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404
    assert services.runtime_cache.get(team_id) is not None


def test_delete_team_state_conflict_keeps_the_handle_reachable() -> None:
    """A RUNNING team refused deletion keeps its handle — it is still live.

    ``TeamManager.delete_team`` rejects a RUNNING team with "stop it first". Evicting
    on that path would make a still-running team unreachable to the message routes,
    which resolve every send through ``runtime_cache.get``.
    """
    team_id = uuid.uuid4()
    # The manager's verbatim message: it carries neither "not found" nor
    # "deleted", so _raise_action_error must map it to a 409.
    refusal = f"Cannot delete team {team_id}: team is currently running. Stop it first."
    services = _lifecycle_services(team_id, error=ValueError(refusal))

    with pytest.raises(HTTPException) as exc_info:
        delete_team(team_id, services)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 409
    assert services.runtime_cache.get(team_id) is not None


def test_delete_team_without_a_cached_handle_is_a_no_op() -> None:
    """Evicting an absent team_id raises nothing and spares unrelated entries."""
    team_id = uuid.uuid4()
    other_id = uuid.uuid4()
    services = _lifecycle_services(other_id)

    result = delete_team(team_id, services)  # type: ignore[arg-type]

    assert result is None
    assert services.worker_handle.deleted == [team_id]
    assert services.runtime_cache.get(team_id) is None
    assert services.runtime_cache.get(other_id) is not None
