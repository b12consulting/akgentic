"""Tests for Angular V1 adapter — models and protocol compliance (Story 3.2a).

Route-level tests have been moved to tests/integration/test_v1_adapter.py
(Story 9.5) where they exercise real services via TestClient.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from akgentic.core.messages.message import UserMessage
from fastapi import FastAPI

from akgentic.infra.server.routes.frontend_adapter import FrontendAdapter, WrappedWsEvent
from akgentic.infra.server.routes.frontend_adapter.angular_v1 import AngularV1Adapter
from akgentic.infra.server.routes.frontend_adapter.angular_v1.models import (
    V1ActorAddress,
    V1ConfigPutBody,
    V1LlmContextEntry,
    V1MessageEntry,
    V1ProcessContext,
    V1ProcessParams,
    V1StateEntry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)
_TEAM_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Task 4: V1 response model tests (AC #8, #9)
# ---------------------------------------------------------------------------


class TestV1Models:
    """Verify V1 response models."""

    def test_v1_process_context_from_process(self) -> None:
        """V1ProcessContext can be constructed from V2 Process data."""
        ctx = V1ProcessContext(
            id=str(_TEAM_ID),
            type="Test Team",
            status="running",
            created_at=_NOW.isoformat(),
            updated_at=_NOW.isoformat(),
            params={},
        )
        assert ctx.id == str(_TEAM_ID)
        assert ctx.type == "Test Team"
        assert ctx.status == "running"
        assert ctx.params == {}

    def test_v1_process_context_serialization_roundtrip(self) -> None:
        """V1ProcessContext survives JSON serialization roundtrip."""
        ctx = V1ProcessContext(
            id=str(_TEAM_ID),
            type="Test Team",
            status="running",
            created_at=_NOW.isoformat(),
            updated_at=_NOW.isoformat(),
            params={},
        )
        data = ctx.model_dump()
        restored = V1ProcessContext.model_validate(data)
        assert restored == ctx

    def test_v1_message_entry_construction(self) -> None:
        """V1MessageEntry can be constructed from event data."""
        entry = V1MessageEntry(
            id=str(uuid.uuid4()),
            sender="@Human",
            content="hello",
            timestamp=_NOW.isoformat(),
            type="user",
        )
        assert entry.content == "hello"
        assert entry.type == "user"

    def test_v1_message_entry_serialization_roundtrip(self) -> None:
        """V1MessageEntry survives JSON roundtrip."""
        entry = V1MessageEntry(
            id=str(uuid.uuid4()),
            sender="@Manager",
            content="response",
            timestamp=_NOW.isoformat(),
            type="agent",
        )
        data = entry.model_dump()
        restored = V1MessageEntry.model_validate(data)
        assert restored == entry

    def test_v1_actor_address_serialization(self) -> None:
        """V1ActorAddress serializes correctly with all fields."""
        addr = V1ActorAddress(name="@Human", role="Human")
        data = addr.model_dump()
        assert data["name"] == "@Human"
        assert data["role"] == "Human"
        assert data["address"] == ""
        assert data["agent_id"] == ""
        assert data["squad_id"] == ""
        assert data["user_message"] == ""
        restored = V1ActorAddress.model_validate(data)
        assert restored == addr

    def test_v1_process_params_serialization(self) -> None:
        """V1ProcessParams serializes correctly."""
        params = V1ProcessParams(
            type="test-team",
            agents=[V1ActorAddress(name="@Human", role="Human")],
        )
        data = params.model_dump()
        restored = V1ProcessParams.model_validate(data)
        assert restored == params

    def test_v1_config_put_body_serialization(self) -> None:
        """V1ConfigPutBody serializes correctly."""
        body = V1ConfigPutBody(
            id="cfg-1",
            name="my-config",
            config={"key": "value"},
            dry_run=True,
        )
        data = body.model_dump()
        restored = V1ConfigPutBody.model_validate(data)
        assert restored == body
        assert restored.dry_run is True

    def test_v1_config_put_body_defaults(self) -> None:
        """V1ConfigPutBody has sensible defaults."""
        body = V1ConfigPutBody(id="cfg-1")
        assert body.name == ""
        assert body.config == {}
        assert body.dry_run is False

    def test_v1_llm_context_entry_serialization(self) -> None:
        """V1LlmContextEntry serializes correctly."""
        entry = V1LlmContextEntry(
            role="user",
            content="hello",
            timestamp=_NOW.isoformat(),
        )
        data = entry.model_dump()
        restored = V1LlmContextEntry.model_validate(data)
        assert restored == entry
        assert restored.role == "user"

    def test_v1_state_entry_serialization(self) -> None:
        """V1StateEntry serializes correctly."""
        entry = V1StateEntry(
            agent="@Manager",
            state={"status": "active"},
            timestamp=_NOW.isoformat(),
        )
        data = entry.model_dump()
        restored = V1StateEntry.model_validate(data)
        assert restored == entry
        assert restored.agent == "@Manager"


# ---------------------------------------------------------------------------
# Task 3: AngularV1Adapter tests (AC #8)
# ---------------------------------------------------------------------------


class TestAngularV1Adapter:
    """Verify AngularV1Adapter satisfies FrontendAdapter protocol."""

    def test_satisfies_frontend_adapter_protocol(self) -> None:
        """AngularV1Adapter is recognized as a FrontendAdapter."""
        adapter = AngularV1Adapter()
        assert isinstance(adapter, FrontendAdapter)

    def test_register_routes_adds_v1_paths(self) -> None:
        """register_routes adds V1 route paths to the app."""
        adapter = AngularV1Adapter()
        app = FastAPI()
        adapter.register_routes(app)
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/process/{type}" in paths
        assert "/process/" in paths or "/process" in paths
        assert "/messages/{id}" in paths
        assert "/llm_context/{id}" in paths
        assert "/states/{id}" in paths

    def test_wrap_ws_event_returns_wrapped_event(self) -> None:
        """wrap_ws_event returns a WrappedWsEvent with payload."""
        adapter = AngularV1Adapter()
        msg = UserMessage(content="hello")
        result = adapter.wrap_ws_event(msg)
        assert isinstance(result, WrappedWsEvent)
        assert hasattr(result.payload, "type")

    def test_adapter_fqdn_loading(self) -> None:
        """Adapter can be loaded via FQDN through load_frontend_adapter."""
        from akgentic.infra.server.routes.frontend_adapter import load_frontend_adapter

        adapter = load_frontend_adapter(
            "akgentic.infra.server.routes.frontend_adapter.angular_v1.AngularV1Adapter"
        )
        assert isinstance(adapter, FrontendAdapter)
