"""Tests for worker app factory and routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from akgentic.core import ActorSystem
from akgentic.team.manager import TeamManager
from akgentic.team.ports import EventStore, NullServiceRegistry
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.worker_handle import WorkerHandle
from akgentic.infra.worker.app import _lifespan, create_worker_app
from akgentic.infra.worker.deps import WorkerServices
from akgentic.infra.worker.settings import WorkerSettings
from tests.fixtures.events import build_sent_message


def _make_mock_process(team_id: uuid.UUID | None = None) -> MagicMock:
    """Create a mock Process with the attributes routes need."""
    now = datetime.now(tz=UTC)
    process = MagicMock()
    process.team_id = team_id or uuid.uuid4()
    process.team_card.name = "Test Team"
    process.status.value = "running"
    process.user_id = "test-user"
    process.created_at = now
    process.updated_at = now
    return process


@pytest.fixture()
def mock_services() -> WorkerServices:
    """Create WorkerServices with all-mocked dependencies."""
    return WorkerServices(
        team_manager=MagicMock(spec=TeamManager),
        actor_system=MagicMock(spec=ActorSystem),
        event_store=MagicMock(spec=EventStore),
        service_registry=NullServiceRegistry(),
        runtime_cache=MagicMock(spec=RuntimeCache),
        worker_handle=MagicMock(spec=WorkerHandle),
    )


@pytest.fixture()
def worker_app(mock_services: WorkerServices) -> FastAPI:
    """Create a worker app with mocked services."""
    settings = WorkerSettings()
    return create_worker_app(mock_services, settings)


class TestReadiness:
    """Worker readiness endpoint tests (AC #4)."""

    @pytest.mark.asyncio
    async def test_readiness_returns_200(self, worker_app: FastAPI) -> None:
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readiness")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}

    @pytest.mark.asyncio
    async def test_readiness_returns_503_when_draining(self, worker_app: FastAPI) -> None:
        worker_app.state.draining = True
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readiness")
        assert resp.status_code == 503
        assert resp.json() == {"status": "draining"}


class TestCreateTeam:
    """POST /teams route tests (AC #4)."""

    @pytest.mark.asyncio
    async def test_create_team_returns_201(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        process = _make_mock_process()
        mock_runtime = MagicMock()
        mock_runtime.id = process.team_id
        mock_services.team_manager.create_team.return_value = mock_runtime  # type: ignore[attr-defined]
        mock_services.worker_handle.get_team.return_value = process  # type: ignore[attr-defined]

        # Build a minimal valid TeamCard JSON payload
        team_card_json = {
            "name": "Test Team",
            "description": "A test team",
            "entry_point": {
                "card": {
                    "role": "Human",
                    "description": "Human user",
                    "skills": [],
                    "agent_class": "akgentic.agent.HumanProxy",
                    "config": {"name": "@Human", "role": "Human"},
                },
            },
            "members": [],
        }
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/teams",
                json={
                    "team_card": team_card_json,
                    "user_id": "test-user",
                },
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["team_id"] == str(process.team_id)
        assert data["name"] == "Test Team"
        assert data["status"] == "running"
        assert data["user_id"] == "test-user"


class TestSendMessage:
    """POST /teams/{team_id}/message route tests (AC #4)."""

    @pytest.mark.asyncio
    async def test_send_message_returns_204(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message",
                json={"content": "hello"},
            )
        assert resp.status_code == 204
        mock_handle.send.assert_called_once_with("hello")

    @pytest.mark.asyncio
    async def test_send_message_returns_404_when_not_cached(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.runtime_cache.get.return_value = None  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message",
                json={"content": "hello"},
            )
        assert resp.status_code == 404


class TestGetTeam:
    """GET /teams/{team_id} route tests (AC #1)."""

    @pytest.mark.asyncio
    async def test_get_team_returns_team_response(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        process = _make_mock_process(team_id)
        mock_services.worker_handle.get_team.return_value = process  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/teams/{team_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["team_id"] == str(team_id)
        assert data["name"] == "Test Team"
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_get_team_returns_404_when_not_found(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.worker_handle.get_team.return_value = None  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/teams/{team_id}")
        assert resp.status_code == 404


class TestSendMessageToAgent:
    """POST /teams/{team_id}/message/{agent_name} route tests (AC #2)."""

    @pytest.mark.asyncio
    async def test_send_to_agent_returns_204(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message/TestAgent",
                json={"content": "hello agent"},
            )
        assert resp.status_code == 204
        mock_handle.send_to.assert_called_once_with("TestAgent", "hello agent")

    @pytest.mark.asyncio
    async def test_send_to_agent_returns_404_when_not_in_cache(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.runtime_cache.get.return_value = None  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message/TestAgent",
                json={"content": "hello"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_send_to_agent_returns_409_on_state_conflict(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_handle.send_to.side_effect = ValueError("team is stopped")
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message/TestAgent",
                json={"content": "hello"},
            )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_send_to_agent_returns_404_on_not_found(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_handle.send_to.side_effect = ValueError("agent not found")
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message/TestAgent",
                json={"content": "hello"},
            )
        assert resp.status_code == 404


class TestSendMessageFromTo:
    """POST /teams/{team_id}/message/from/{sender}/to/{recipient} route tests (AC #3)."""

    @pytest.mark.asyncio
    async def test_send_from_to_returns_204(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message/from/Alice/to/Bob",
                json={"content": "hello bob"},
            )
        assert resp.status_code == 204
        mock_handle.send_from_to.assert_called_once_with("Alice", "Bob", "hello bob")

    @pytest.mark.asyncio
    async def test_send_from_to_returns_404_when_not_in_cache(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.runtime_cache.get.return_value = None  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message/from/Alice/to/Bob",
                json={"content": "hello"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_send_from_to_returns_409_on_state_conflict(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_handle.send_from_to.side_effect = ValueError("team is stopped")
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message/from/Alice/to/Bob",
                json={"content": "hello"},
            )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_send_from_to_returns_404_on_not_found(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_handle.send_from_to.side_effect = ValueError("recipient not found")
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message/from/Alice/to/Bob",
                json={"content": "hello"},
            )
        assert resp.status_code == 404


class TestHumanInput:
    """POST /teams/{team_id}/human-input route tests (AC #4)."""

    @pytest.mark.asyncio
    async def test_human_input_returns_204(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        message_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        # Build a real SentMessage so isinstance() check passes after unwrapping
        sent_msg = build_sent_message(id=message_id)

        # Two events: first doesn't match, second does — exercises loop iteration branch
        non_matching_event = MagicMock()
        non_matching_event.event.id = uuid.uuid4()
        matching_event = MagicMock()
        matching_event.event = sent_msg
        mock_services.event_store.load_events.return_value = [  # type: ignore[attr-defined]
            non_matching_event,
            matching_event,
        ]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/human-input",
                json={"content": "user reply", "message_id": str(message_id)},
            )
        assert resp.status_code == 204
        mock_services.event_store.load_events.assert_called_once_with(team_id)  # type: ignore[attr-defined]
        mock_handle.process_human_input.assert_called_once_with(
            "user reply", sent_msg.message
        )

    @pytest.mark.asyncio
    async def test_human_input_returns_404_when_not_in_cache(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.runtime_cache.get.return_value = None  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/human-input",
                json={"content": "reply", "message_id": str(uuid.uuid4())},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_human_input_returns_404_when_message_not_found(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]
        mock_services.event_store.load_events.return_value = []  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/human-input",
                json={"content": "reply", "message_id": str(uuid.uuid4())},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_human_input_returns_409_on_state_conflict(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        message_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_handle.process_human_input.side_effect = ValueError("team is stopped")
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        sent_msg = build_sent_message(id=message_id)
        mock_event = MagicMock()
        mock_event.event = sent_msg
        mock_services.event_store.load_events.return_value = [mock_event]  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/human-input",
                json={"content": "reply", "message_id": str(message_id)},
            )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_human_input_returns_409_when_event_not_sentmessage(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        message_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        # Event found but is NOT a SentMessage — isinstance guard should reject it
        mock_event = MagicMock()
        mock_event.event.id = message_id
        mock_services.event_store.load_events.return_value = [mock_event]  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/human-input",
                json={"content": "reply", "message_id": str(message_id)},
            )
        assert resp.status_code == 409
        assert "expected SentMessage" in resp.json()["detail"]


class TestStopTeam:
    """POST /teams/{team_id}/stop route tests (AC #4)."""

    @pytest.mark.asyncio
    async def test_stop_team_returns_204(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/teams/{team_id}/stop")
        assert resp.status_code == 204
        mock_services.worker_handle.stop_team.assert_called_once_with(team_id)  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_stop_team_returns_409_on_conflict(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.worker_handle.stop_team.side_effect = ValueError(  # type: ignore[attr-defined]
            "Team is already stopped"
        )

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/teams/{team_id}/stop")
        assert resp.status_code == 409


class TestDeleteTeam:
    """DELETE /teams/{team_id} route tests (AC #4)."""

    @pytest.mark.asyncio
    async def test_delete_team_returns_204(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/teams/{team_id}")
        assert resp.status_code == 204
        mock_services.worker_handle.delete_team.assert_called_once_with(team_id)  # type: ignore[attr-defined]


class TestResumeTeam:
    """POST /teams/{team_id}/resume route tests (AC #4)."""

    @pytest.mark.asyncio
    async def test_resume_team_returns_200(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        process = _make_mock_process(team_id)
        mock_handle = MagicMock()
        mock_services.worker_handle.resume_team.return_value = mock_handle  # type: ignore[attr-defined]
        mock_services.worker_handle.get_team.return_value = process  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/teams/{team_id}/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["team_id"] == str(team_id)
        mock_services.runtime_cache.store.assert_called_once_with(team_id, mock_handle)  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_resume_team_returns_404_when_not_found(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.worker_handle.resume_team.side_effect = ValueError(  # type: ignore[attr-defined]
            "Team not found"
        )
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/teams/{team_id}/resume")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_resume_team_returns_409_on_conflict(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.worker_handle.resume_team.side_effect = ValueError(  # type: ignore[attr-defined]
            "Team is already running"
        )
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/teams/{team_id}/resume")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_resume_team_returns_404_when_get_team_none(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_services.worker_handle.resume_team.return_value = mock_handle  # type: ignore[attr-defined]
        mock_services.worker_handle.get_team.return_value = None  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/teams/{team_id}/resume")
        assert resp.status_code == 404


class TestSendMessageErrors:
    """Error path tests for POST /teams/{team_id}/message."""

    @pytest.mark.asyncio
    async def test_send_message_returns_409_on_value_error(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_handle = MagicMock()
        mock_handle.send.side_effect = ValueError("Team is already stopped")
        mock_services.runtime_cache.get.return_value = mock_handle  # type: ignore[attr-defined]

        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/teams/{team_id}/message",
                json={"content": "hello"},
            )
        assert resp.status_code == 409


class TestDeleteTeamErrors:
    """Error path tests for DELETE /teams/{team_id}."""

    @pytest.mark.asyncio
    async def test_delete_team_returns_404_when_not_found(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.worker_handle.delete_team.side_effect = ValueError(  # type: ignore[attr-defined]
            "Team not found"
        )
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/teams/{team_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_team_returns_404_when_deleted(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.worker_handle.delete_team.side_effect = ValueError(  # type: ignore[attr-defined]
            "Team already deleted"
        )
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/teams/{team_id}")
        assert resp.status_code == 404


class TestStopTeamErrors:
    """Additional error path tests for POST /teams/{team_id}/stop."""

    @pytest.mark.asyncio
    async def test_stop_team_returns_404_when_not_found(
        self, worker_app: FastAPI, mock_services: WorkerServices
    ) -> None:
        team_id = uuid.uuid4()
        mock_services.worker_handle.stop_team.side_effect = ValueError(  # type: ignore[attr-defined]
            "Team not found"
        )
        transport = ASGITransport(app=worker_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/teams/{team_id}/stop")
        assert resp.status_code == 404


class TestLifespan:
    """Worker lifespan handler tests."""

    @pytest.mark.asyncio
    async def test_lifespan_sets_draining_false_on_startup(
        self, worker_app: FastAPI
    ) -> None:
        async with _lifespan(worker_app):
            assert worker_app.state.draining is False

    @pytest.mark.asyncio
    async def test_lifespan_sets_draining_true_on_shutdown(
        self, worker_app: FastAPI
    ) -> None:
        async with _lifespan(worker_app):
            pass
        assert worker_app.state.draining is True

    @pytest.mark.asyncio
    async def test_lifespan_calls_stop_all(self, worker_app: FastAPI) -> None:
        async with _lifespan(worker_app):
            pass
        worker_app.state.services.worker_handle.stop_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_handles_timeout(self, worker_app: FastAPI) -> None:
        with patch(
            "akgentic.infra.worker.services.lifecycle.asyncio.wait_for",
            side_effect=TimeoutError,
        ):
            worker_app.state.settings = WorkerSettings(
                shutdown_drain_timeout=0, shutdown_pre_drain_delay=0
            )
            async with _lifespan(worker_app):
                pass
        # Should not raise — timeout is handled gracefully in WorkerLifecycle
