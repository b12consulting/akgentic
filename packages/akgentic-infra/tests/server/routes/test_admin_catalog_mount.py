"""Mount-smoke tests for the v2 admin catalog surface (ADR-023 §D1, §D3, §D5).

Asserts the three invariants that infra owns for the admin-catalog mount:

a. A rejecting ``AuthStrategy`` returns 401 before the v2 handler is reached.
b. An accepting ``AuthStrategy`` dispatches to the v2 router and the response
   shape matches the v2 ``Entry`` contract.
c. The mutation-log middleware emits one INFO line per mutation, carrying the
   principal ID resolved via the wired strategy.

Does NOT re-test v2 CRUD semantics — those live in the ``akgentic-catalog``
package's own test suite.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient


class _RejectingAuth:
    """AuthStrategy stub that always rejects by returning ``None``."""

    def authenticate(self, request: Any) -> str | None:  # noqa: ANN401
        return None


class _AcceptingAuth:
    """AuthStrategy stub that returns a fixed principal ID."""

    def __init__(self, principal: str = "test-principal") -> None:
        self._principal = principal

    def authenticate(self, request: Any) -> str | None:  # noqa: ANN401
        return self._principal


def test_rejecting_auth_returns_401_before_v2_handler(client: TestClient) -> None:
    """AC #7a: a rejecting AuthStrategy → 401 without entering the v2 handler."""
    client.app.state.services.auth = _RejectingAuth()

    resp = client.get("/admin/catalog/team", params={"namespace": "test-team"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "authentication required"


def test_accepting_auth_dispatches_to_v2_router(client: TestClient) -> None:
    """AC #7b: an accepting AuthStrategy → v2 handler runs, v2 response shape."""
    client.app.state.services.auth = _AcceptingAuth()

    resp = client.get("/admin/catalog/team", params={"namespace": "test-team"})

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # v2 Entry shape — each row has id/kind/namespace keys and a payload dict.
    assert all({"id", "kind", "namespace", "payload"} <= set(row.keys()) for row in body)


def test_accepting_auth_allows_community_noauth(client: TestClient) -> None:
    """AC #9 smoke: community's NoAuth (default wiring) yields 200, not 401."""
    # ``client`` fixture is built with wire_community() which wires NoAuth.
    resp = client.get("/admin/catalog/team", params={"namespace": "test-team"})

    assert resp.status_code == 200


def test_mutation_log_records_principal_id_on_post(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """AC #7c: a POST mutation emits one INFO log with the resolved principal ID."""
    client.app.state.services.auth = _AcceptingAuth(principal="log-principal")

    new_entry = {
        "id": "mount-smoke-team",
        "kind": "team",
        "namespace": "mount-smoke-ns",
        "model_type": "akgentic.team.models.TeamCard",
        "description": "mount smoke test team",
        "payload": {
            "name": "Mount Smoke Team",
            "description": "mount smoke",
            "entry_point": {
                "card": {
                    "role": "Human",
                    "description": "Human",
                    "skills": [],
                    "agent_class": "akgentic.core.agent.Akgent",
                    "config": {"name": "@Human", "role": "Human"},
                    "routes_to": [],
                },
                "headcount": 1,
                "members": [],
            },
            "members": [],
            "message_types": [{"__type__": "akgentic.core.messages.UserMessage"}],
            "agent_profiles": [],
        },
    }

    logger_name = "akgentic.infra.server.routes._admin_mutation_log"
    with caplog.at_level(logging.INFO, logger=logger_name):
        resp = client.post("/admin/catalog/team", json=new_entry)

    assert resp.status_code == 201

    matching = [r for r in caplog.records if r.name == logger_name]
    assert len(matching) == 1
    record = matching[0]
    assert record.levelno == logging.INFO
    # Structured fields are attached as ``extra``; the caplog LogRecord exposes
    # them as direct attributes.
    assert getattr(record, "principal_id") == "log-principal"
    assert getattr(record, "kind") == "team"
    assert getattr(record, "operation") == "create"
    assert getattr(record, "status_code") == 201
