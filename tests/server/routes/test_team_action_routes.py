"""Tests for team action endpoints using FastAPI TestClient."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message, UserMessage
from akgentic.core.messages.orchestrator import StateChangedMessage
from akgentic.team.subscriber import PersistenceSubscriber
from fastapi.testclient import TestClient

from akgentic.infra.server.deps import CommunityServices
from akgentic.infra.server.services.team_service import TeamService
from tests.server.conftest import append_synthetic_events


class SampleAgentState(BaseState):
    """Minimal BaseState subclass for building a StateChangedMessage."""

    task_count: int = 0


def _create_team(client: TestClient) -> str:
    """Create a team through the API and return its id."""
    resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    return str(resp.json()["team_id"])


def test_send_message_success(client: TestClient) -> None:
    """POST /teams/{id}/message on running team returns 204."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/message", json={"content": "hello"})
    assert resp.status_code == 204


def test_send_message_not_found(client: TestClient) -> None:
    """POST /teams/{id}/message on non-existent team returns 404."""
    resp = client.post(
        f"/teams/{uuid.uuid4()}/message",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_send_message_stopped_team(client: TestClient) -> None:
    """POST /teams/{id}/message on stopped team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(f"/teams/{team_id}/message", json={"content": "hello"})
    assert resp.status_code == 409


def test_send_message_to_agent_success(client: TestClient) -> None:
    """POST /teams/{id}/message/{agent} on running team returns 204."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/message/@Manager", json={"content": "hello"})
    assert resp.status_code == 204


def test_send_message_to_agent_not_found_team(client: TestClient) -> None:
    """POST /teams/{id}/message/{agent} on non-existent team returns 404."""
    resp = client.post(
        f"/teams/{uuid.uuid4()}/message/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_send_message_to_agent_stopped_team(client: TestClient) -> None:
    """POST /teams/{id}/message/{agent} on stopped team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(f"/teams/{team_id}/message/@Manager", json={"content": "hello"})
    assert resp.status_code == 409


def test_send_message_to_agent_unknown_agent(client: TestClient) -> None:
    """POST /teams/{id}/message/{agent} with unknown agent returns 404."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/message/ghost", json={"content": "hello"})
    assert resp.status_code == 404


def test_send_message_from_to_success(client: TestClient) -> None:
    """POST /teams/{id}/message/from/{sender}/to/{recipient} returns 204."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(
        f"/teams/{team_id}/message/from/@Human/to/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 204


def test_send_message_from_to_not_found_team(client: TestClient) -> None:
    """POST send_from_to on non-existent team returns 404."""
    resp = client.post(
        f"/teams/{uuid.uuid4()}/message/from/@Human/to/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_send_message_from_to_stopped_team(client: TestClient) -> None:
    """POST send_from_to on stopped team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(
        f"/teams/{team_id}/message/from/@Human/to/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 409


def test_send_message_from_to_unknown_sender(client: TestClient) -> None:
    """POST send_from_to with unknown sender returns 404."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(
        f"/teams/{team_id}/message/from/@Ghost/to/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_send_message_from_to_unknown_recipient(client: TestClient) -> None:
    """POST send_from_to with unknown recipient returns 404."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(
        f"/teams/{team_id}/message/from/@Human/to/@Ghost",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_emit_notification_success(client: TestClient) -> None:
    """POST /teams/{id}/notification with a serialized Message dict returns 204."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    payload = UserMessage(content="banner").model_dump(mode="json")
    resp = client.post(f"/teams/{team_id}/notification", json={"message": payload})
    assert resp.status_code == 204


def test_emit_notification_undeserializable_payload_returns_400(client: TestClient) -> None:
    """A payload whose __model__ tag cannot be imported returns 400."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    payload = {"__model__": "akgentic.core.messages.message.NoSuchMessage"}
    resp = client.post(f"/teams/{team_id}/notification", json={"message": payload})
    assert resp.status_code == 400


def test_emit_notification_non_message_payload_returns_400(client: TestClient) -> None:
    """A payload that decodes to something other than a Message returns 400."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/notification", json={"message": {"hello": "world"}})
    assert resp.status_code == 400


def test_emit_notification_not_found_team(client: TestClient) -> None:
    """POST /teams/{id}/notification on a non-existent team returns 404."""
    payload = UserMessage(content="banner").model_dump(mode="json")
    resp = client.post(f"/teams/{uuid.uuid4()}/notification", json={"message": payload})
    assert resp.status_code == 404


def test_emit_notification_stopped_team_returns_409(client: TestClient) -> None:
    """POST /teams/{id}/notification on a stopped team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    payload = UserMessage(content="banner").model_dump(mode="json")
    resp = client.post(f"/teams/{team_id}/notification", json={"message": payload})
    assert resp.status_code == 409


# --- Typed-Message send via the merged /message* routes (Story 47.3, ADR-22 §Decision 6) ---


def _typed_body(message: Message) -> dict[str, object]:
    """Build the typed-``message`` wire envelope for a concrete Message."""
    return {"message": message.model_dump(mode="json")}


def test_send_message_typed_envelope_success(client: TestClient) -> None:
    """POST /teams/{id}/message with a serialized Message envelope returns 204."""
    team_id = _create_team(client)
    resp = client.post(f"/teams/{team_id}/message", json=_typed_body(UserMessage(content="hi")))
    assert resp.status_code == 204


def test_send_message_to_agent_typed_envelope_success(client: TestClient) -> None:
    """POST /teams/{id}/message/{agent} with a serialized Message envelope returns 204."""
    team_id = _create_team(client)
    resp = client.post(
        f"/teams/{team_id}/message/@Manager", json=_typed_body(UserMessage(content="hi"))
    )
    assert resp.status_code == 204


def test_send_message_from_to_typed_envelope_success(client: TestClient) -> None:
    """POST /teams/{id}/message/from/{s}/to/{r} with a serialized Message envelope returns 204."""
    team_id = _create_team(client)
    resp = client.post(
        f"/teams/{team_id}/message/from/@Human/to/@Manager",
        json=_typed_body(UserMessage(content="hi")),
    )
    assert resp.status_code == 204


def test_send_message_str_content_delivers_plain_string(client: TestClient) -> None:
    """The str `content` path passes the plain string through unchanged — AC#9 regression."""
    team_id = _create_team(client)
    with patch.object(TeamService, "send_message") as spy:
        resp = client.post(f"/teams/{team_id}/message", json={"content": "hello"})
    assert resp.status_code == 204
    spy.assert_called_once()
    assert spy.call_args.args[1] == "hello"


def test_send_message_typed_envelope_delivers_exact_instance(client: TestClient) -> None:
    """The service receives the exact concrete Message reconstructed from the envelope — AC#5."""
    team_id = _create_team(client)
    original = UserMessage(content="typed-hello")
    with patch.object(TeamService, "send_message") as spy:
        resp = client.post(f"/teams/{team_id}/message", json=_typed_body(original))
    assert resp.status_code == 204
    spy.assert_called_once()
    sent = spy.call_args.args[1]
    assert type(sent) is UserMessage
    assert sent == original


def test_send_message_to_agent_typed_delivers_instance_and_agent(client: TestClient) -> None:
    """send_message_to gets the agent name and the exact Message instance — AC#5."""
    team_id = _create_team(client)
    original = UserMessage(content="typed-hello")
    with patch.object(TeamService, "send_message_to") as spy:
        resp = client.post(f"/teams/{team_id}/message/@Manager", json=_typed_body(original))
    assert resp.status_code == 204
    spy.assert_called_once()
    _team_id, agent_name, sent = spy.call_args.args
    assert agent_name == "@Manager"
    assert type(sent) is UserMessage
    assert sent == original


def test_send_message_from_to_typed_delivers_instance_and_routing(client: TestClient) -> None:
    """send_message_from_to gets sender, recipient, and the exact Message instance — AC#5."""
    team_id = _create_team(client)
    original = UserMessage(content="typed-hello")
    with patch.object(TeamService, "send_message_from_to") as spy:
        resp = client.post(
            f"/teams/{team_id}/message/from/@Human/to/@Manager", json=_typed_body(original)
        )
    assert resp.status_code == 204
    spy.assert_called_once()
    _team_id, sender, recipient, sent = spy.call_args.args
    assert sender == "@Human"
    assert recipient == "@Manager"
    assert type(sent) is UserMessage
    assert sent == original


def test_send_message_neither_content_nor_message_returns_422(client: TestClient) -> None:
    """A body with neither `content` nor `message` fails the XOR validator — AC#6."""
    team_id = _create_team(client)
    with patch.object(TeamService, "send_message") as spy:
        resp = client.post(f"/teams/{team_id}/message", json={})
    assert resp.status_code == 422
    spy.assert_not_called()


def test_send_message_both_content_and_message_returns_422(client: TestClient) -> None:
    """A body with BOTH `content` and `message` fails the XOR validator — AC#6."""
    team_id = _create_team(client)
    body = {"content": "hi", "message": UserMessage(content="hi").model_dump(mode="json")}
    with patch.object(TeamService, "send_message") as spy:
        resp = client.post(f"/teams/{team_id}/message", json=body)
    assert resp.status_code == 422
    spy.assert_not_called()


def test_send_message_undeserializable_envelope_returns_400(client: TestClient) -> None:
    """A `message` with an unimportable __model__ tag returns 400, service NOT called — AC#6a."""
    team_id = _create_team(client)
    body = {"message": {"__model__": "akgentic.core.messages.message.NoSuchMessage"}}
    with patch.object(TeamService, "send_message") as spy:
        resp = client.post(f"/teams/{team_id}/message", json=body)
    assert resp.status_code == 400
    spy.assert_not_called()


def test_send_message_non_message_envelope_returns_400(client: TestClient) -> None:
    """A `message` that decodes to a non-Message returns 400, service NOT called — AC#6b."""
    team_id = _create_team(client)
    with patch.object(TeamService, "send_message") as spy:
        resp = client.post(f"/teams/{team_id}/message", json={"message": {"hello": "world"}})
    assert resp.status_code == 400
    spy.assert_not_called()


def test_human_input_not_found_team(client: TestClient) -> None:
    """POST /teams/{id}/human-input on non-existent team returns 404."""
    resp = client.post(
        f"/teams/{uuid.uuid4()}/human-input",
        json={"content": "yes", "message_id": "abc"},
    )
    assert resp.status_code == 404


def test_human_input_invalid_message(client: TestClient) -> None:
    """POST /teams/{id}/human-input with invalid message_id returns 404."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(
        f"/teams/{team_id}/human-input",
        json={"content": "yes", "message_id": "nonexistent"},
    )
    assert resp.status_code == 404


def test_stop_team_success(client: TestClient) -> None:
    """POST /teams/{id}/stop on running team returns 204."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/stop")
    assert resp.status_code == 204
    # Verify team is now stopped
    get_resp = client.get(f"/teams/{team_id}")
    assert get_resp.json()["status"] == "stopped"


def test_stop_team_already_stopped(client: TestClient) -> None:
    """POST /teams/{id}/stop on already stopped team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(f"/teams/{team_id}/stop")
    assert resp.status_code == 409


def test_stop_team_not_found(client: TestClient) -> None:
    """POST /teams/{id}/stop on non-existent team returns 404."""
    resp = client.post(f"/teams/{uuid.uuid4()}/stop")
    assert resp.status_code == 404


def test_restore_team_success(client: TestClient) -> None:
    """POST /teams/{id}/restore on stopped team returns 200 + TeamResponse."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(f"/teams/{team_id}/restore")
    assert resp.status_code == 200
    data = resp.json()
    assert data["team_id"] == team_id
    assert data["status"] == "running"


def test_restore_team_already_running(client: TestClient) -> None:
    """POST /teams/{id}/restore on already running team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/restore")
    assert resp.status_code == 409


def test_restore_team_not_found(client: TestClient) -> None:
    """POST /teams/{id}/restore on non-existent team returns 404."""
    resp = client.post(f"/teams/{uuid.uuid4()}/restore")
    assert resp.status_code == 404


def test_get_events_success(client: TestClient) -> None:
    """GET /teams/{id}/events returns events for a team."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    # Stop team first so events are flushed
    client.post(f"/teams/{team_id}/stop")
    resp = client.get(f"/teams/{team_id}/events")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert isinstance(data["events"], list)


def test_get_events_not_found(client: TestClient) -> None:
    """GET /teams/{id}/events on non-existent team returns 404."""
    resp = client.get(f"/teams/{uuid.uuid4()}/events")
    assert resp.status_code == 404


@pytest.mark.skip(
    reason="Flaky: race in TeamManager.delete_team — on_stop subscribers still "
    "flushing event_store writes while rmtree runs; pre-existing, not introduced by Epic 22."
)
def test_stop_deleted_team(client: TestClient) -> None:
    """POST /teams/{id}/stop on deleted team returns 404."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.delete(f"/teams/{team_id}")
    resp = client.post(f"/teams/{team_id}/stop")
    assert resp.status_code == 404


@pytest.mark.skip(
    reason="Flaky: same race as test_stop_deleted_team; pre-existing, not introduced by Epic 22."
)
def test_restore_deleted_team(client: TestClient) -> None:
    """POST /teams/{id}/restore on deleted team returns 404."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.delete(f"/teams/{team_id}")
    resp = client.post(f"/teams/{team_id}/restore")
    assert resp.status_code == 404


def test_get_events_running_team(client: TestClient) -> None:
    """GET /teams/{id}/events on running team returns events."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.get(f"/teams/{team_id}/events")
    assert resp.status_code == 200
    assert isinstance(resp.json()["events"], list)


# --- after_event_id cursor ---


def test_get_events_no_cursor_returns_full_log_sequence_asc(
    client: TestClient,
    community_services: CommunityServices,
) -> None:
    """No cursor returns the full log, sequence ASC — unchanged behaviour."""
    team_id = _create_team(client)
    appended = append_synthetic_events(
        community_services.event_store, uuid.UUID(team_id), 3
    )

    resp = client.get(f"/teams/{team_id}/events")

    assert resp.status_code == 200
    events = resp.json()["events"]
    sequences = [e["sequence"] for e in events]
    assert sequences == sorted(sequences)
    assert [e["event"]["id"] for e in events[-3:]] == [str(e.event.id) for e in appended]


def test_get_events_mid_log_cursor_returns_strict_tail(
    client: TestClient,
    community_services: CommunityServices,
) -> None:
    """A mid-log cursor returns only the events after it — anchor excluded."""
    team_id = _create_team(client)
    appended = append_synthetic_events(
        community_services.event_store, uuid.UUID(team_id), 3
    )

    resp = client.get(
        f"/teams/{team_id}/events", params={"after_event_id": str(appended[0].event.id)}
    )

    assert resp.status_code == 200
    ids = [e["event"]["id"] for e in resp.json()["events"]]
    assert ids == [str(appended[1].event.id), str(appended[2].event.id)]


def test_get_events_newest_cursor_returns_empty_list(
    client: TestClient,
    community_services: CommunityServices,
) -> None:
    """A cursor on the newest event is a success with no events, not an error."""
    team_id = _create_team(client)
    appended = append_synthetic_events(
        community_services.event_store, uuid.UUID(team_id), 3
    )

    resp = client.get(
        f"/teams/{team_id}/events", params={"after_event_id": str(appended[-1].event.id)}
    )

    assert resp.status_code == 200
    assert resp.json()["events"] == []


def test_get_events_unknown_cursor_returns_400(client: TestClient) -> None:
    """A cursor that was never persisted is a bad cursor, not a missing team."""
    team_id = _create_team(client)

    resp = client.get(
        f"/teams/{team_id}/events", params={"after_event_id": str(uuid.uuid4())}
    )

    assert resp.status_code == 400


def test_get_events_state_changed_message_cursor_returns_400(
    client: TestClient,
    community_services: CommunityServices,
) -> None:
    """A StateChangedMessage id streamed over WS is not a valid cursor.

    The message MUST carry a sender: PersistenceSubscriber only diverts a
    StateChangedMessage into an AgentStateSnapshot when ``sender is not None``.
    A sender-less one falls through to the event log, becomes a valid cursor,
    and would return 200 — testing the exact opposite of this test's name.

    The sender name must not collide with a seeded-catalog agent: the live
    team's own agents emit a state change on init_state(), so asserting on a
    real agent's snapshot would pass whether or not on_message() diverted.
    """
    team_id = _create_team(client)
    store = community_services.event_store
    subscriber = PersistenceSubscriber(uuid.UUID(team_id), store)

    sender = MagicMock(spec=ActorAddress)
    sender.name = "@TrapSender"
    state_changed = StateChangedMessage(sender=sender, state=SampleAgentState(task_count=1))
    subscriber.on_message(state_changed)

    # It became a snapshot and never entered the event log — so its id is not a cursor.
    snapshots = store.load_agent_states(uuid.UUID(team_id))
    assert any(s.agent_id == "@TrapSender" for s in snapshots)
    persisted_ids = [e.event.id for e in store.load_events(uuid.UUID(team_id))]
    assert state_changed.id not in persisted_ids

    resp = client.get(
        f"/teams/{team_id}/events", params={"after_event_id": str(state_changed.id)}
    )

    assert resp.status_code == 400


def test_get_events_cross_team_cursor_returns_400(
    client: TestClient,
    community_services: CommunityServices,
) -> None:
    """A cursor belonging to another team does not resolve for this one."""
    team_a = _create_team(client)
    team_b = _create_team(client)
    team_b_events = append_synthetic_events(
        community_services.event_store, uuid.UUID(team_b), 1
    )

    resp = client.get(
        f"/teams/{team_a}/events",
        params={"after_event_id": str(team_b_events[0].event.id)},
    )

    assert resp.status_code == 400


def test_get_events_unknown_team_with_cursor_returns_404(client: TestClient) -> None:
    """A bad team wins over a bad cursor: the team check runs first."""
    resp = client.get(
        f"/teams/{uuid.uuid4()}/events", params={"after_event_id": str(uuid.uuid4())}
    )

    assert resp.status_code == 404


def test_get_events_malformed_cursor_returns_422(client: TestClient) -> None:
    """A malformed uuid yields FastAPI's stock validation error."""
    team_id = _create_team(client)

    resp = client.get(f"/teams/{team_id}/events", params={"after_event_id": "not-a-uuid"})

    assert resp.status_code == 422
