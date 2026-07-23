"""Shared worker team-route tests for the typed-send and notification surfaces.

The worker ``/message*`` routes accept a pre-formed ``Message`` wire envelope
alongside the plain-string path, and ``/notification`` publishes a pre-formed
Message through ``handle.emitMessage`` with no agent processing.

The handlers are exercised directly with a real ``LocalRuntimeCache`` seeded
with a ``LocalTeamHandle`` over a stub runtime, so the assertion is on the
genuine decode-and-delegate path rather than on team creation.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from akgentic.core.messages.message import UserMessage
from fastapi import HTTPException

from akgentic.infra.adapters.community.local_runtime_cache import LocalRuntimeCache
from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.infra.server.models import EmitMessageRequest, SendMessageRequest
from akgentic.infra.worker.routes.teams import emit_notification, send_message

_BAD_ENVELOPE = {"__model__": "akgentic.core.messages.message.NoSuchMessage"}


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
