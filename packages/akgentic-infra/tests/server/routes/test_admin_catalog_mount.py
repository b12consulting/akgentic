"""Mount-smoke tests for the v2 admin catalog surface (ADR-023 §D1, §D3, §D5).

Asserts the invariants infra owns for the admin-catalog mount, all expressed
through the single ADR-023 identity seam (``get_request_user``):

a. The community default identity (anonymous) PASSES the gate — no 401.
b. A *raising* ``get_request_user`` override surfaces 401 (the dept/enterprise
   bad-credential path); the gate adds no failure branch of its own.
c. The accepting (community) path dispatches to the v2 router and the response
   shape matches the v2 ``Entry`` contract.
d. The mutation-log middleware emits one INFO line per mutation, carrying the
   principal ID stashed by the gate from the resolved ``RequestUser``.

Does NOT re-test v2 CRUD semantics — those live in the ``akgentic-catalog``
package's own test suite.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from akgentic.infra.server.auth import RequestUser, get_request_user


@pytest.fixture()
def mount_client(client: TestClient) -> Iterator[TestClient]:
    """The community ``client`` with any per-test identity override cleaned up.

    Mutation-log and 401 tests install an ``app.dependency_overrides`` entry for
    ``get_request_user``; this fixture guarantees it is popped on teardown so it
    cannot leak into other tests sharing the app.
    """
    yield client
    client.app.dependency_overrides.pop(get_request_user, None)


def _raising_user() -> RequestUser:
    """A ``get_request_user`` override mirroring the dept/enterprise 401 path."""
    raise HTTPException(status_code=401, detail="authentication required")


def test_community_anonymous_passes_gate(mount_client: TestClient) -> None:
    """AC2: the community default (anonymous) PASSES the gate → 200, not 401.

    No dependency override — the ``client`` fixture wires ``wire_community()``
    (NoAuth) and the default ``get_request_user`` resolves the anonymous
    principal. The gate has no anonymous/empty-roles failure branch.
    """
    resp = mount_client.get("/admin/catalog/team", params={"namespace": "test-team"})

    assert resp.status_code == 200


def test_raising_override_surfaces_401(mount_client: TestClient) -> None:
    """AC3: a raising ``get_request_user`` override → 401 (raise propagates)."""
    mount_client.app.dependency_overrides[get_request_user] = _raising_user

    resp = mount_client.get("/admin/catalog/team", params={"namespace": "test-team"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "authentication required"


def test_anonymous_dispatches_to_v2_router(mount_client: TestClient) -> None:
    """AC2: the community anonymous path → v2 handler runs, v2 response shape."""
    resp = mount_client.get("/admin/catalog/team", params={"namespace": "test-team"})

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # v2 Entry shape — each row has id/kind/namespace keys and a payload dict.
    assert all({"id", "kind", "namespace", "payload"} <= set(row.keys()) for row in body)


def test_mutation_log_records_stashed_principal_id_on_post(
    mount_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """AC4: a POST mutation emits one INFO log carrying the stashed principal ID.

    The principal comes from the ``RequestUser`` the gate stashed on
    ``request.state`` (here overridden to ``log-principal``), NOT from any
    ``services.auth`` call.
    """
    mount_client.app.dependency_overrides[get_request_user] = lambda: RequestUser(
        user_id="log-principal"
    )

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
        resp = mount_client.post("/admin/catalog/team", json=new_entry)

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


def test_mutation_log_community_anonymous_principal_on_post(
    mount_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """AC4: with no override, the community POST logs ``principal_id="anonymous"``."""
    new_entry = {
        "id": "mount-smoke-anon",
        "kind": "team",
        "namespace": "mount-smoke-anon-ns",
        "model_type": "akgentic.team.models.TeamCard",
        "description": "mount smoke anon team",
        "payload": {
            "name": "Mount Smoke Anon Team",
            "description": "mount smoke anon",
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
        resp = mount_client.post("/admin/catalog/team", json=new_entry)

    assert resp.status_code == 201

    matching = [r for r in caplog.records if r.name == logger_name]
    assert len(matching) == 1
    assert getattr(matching[0], "principal_id") == "anonymous"
