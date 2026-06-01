"""Integration tests for the v2 unified ``/catalog`` FastAPI router.

Covers every route in the router — happy path and every documented error
status code. See Story 16.1 ACs 21-31 for the mapping from tests to ACs.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from akgentic.catalog.catalog import Catalog  # noqa: E402
from akgentic.catalog.models.entry import Entry  # noqa: E402
from akgentic.catalog.models.namespace_meta import NamespaceMeta  # noqa: E402

_TEAM_TYPE = "akgentic.team.models.TeamCard"
_AGENT_TYPE = "akgentic.core.agent_card.AgentCard"


# --- helpers ----------------------------------------------------------------


def _team_payload() -> dict[str, Any]:
    """Return a minimal valid ``TeamCard`` payload."""
    return {
        "name": "team",
        "description": "",
        "entry_point": {
            "card": {
                "role": "entry",
                "description": "",
                "skills": [],
                "agent_class": "akgentic.core.agent.Akgent",
                "config": {"name": "entry", "role": "entry"},
            },
            "headcount": 1,
            "members": [],
        },
        "members": [],
        "agent_profiles": [],
    }


def _agent_payload(name: str = "a") -> dict[str, Any]:
    """Return a minimal valid ``AgentCard`` payload."""
    return {
        "role": "r",
        "description": "",
        "skills": [],
        "agent_class": "akgentic.core.agent.Akgent",
        "config": {"name": name, "role": "r"},
        "routes_to": [],
        "metadata": {},
    }


def _seed_team(catalog: Catalog, namespace: str, user_id: str = "anonymous") -> Entry:
    """Seed a team entry in ``namespace``."""
    return catalog.create(
        Entry(
            id="team",
            kind="team",
            namespace=namespace,
            user_id=user_id,
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
    )


def _seed_agent(
    catalog: Catalog,
    namespace: str,
    id: str = "agent-a",
    user_id: str = "anonymous",
    payload: dict[str, Any] | None = None,
) -> Entry:
    """Seed a minimal agent entry sharing the team's ownership."""
    return catalog.create(
        Entry(
            id=id,
            kind="agent",
            namespace=namespace,
            user_id=user_id,
            model_type=_AGENT_TYPE,
            payload=payload if payload is not None else _agent_payload(id),
        )
    )


# --- CRUD -------------------------------------------------------------------


class TestCreate:
    """POST /catalog/{kind} — AC7, AC23."""

    def test_create_team_returns_201(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        body = {
            "id": "team",
            "kind": "team",
            "namespace": "ns-create",
            "model_type": _TEAM_TYPE,
            "payload": _team_payload(),
        }
        response = client.post("/catalog/team", json=body)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "team"
        assert data["namespace"] == "ns-create"
        assert data["kind"] == "team"

    def test_create_kind_mismatch_400(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        body = {
            "id": "team",
            "kind": "team",
            "namespace": "ns-mismatch",
            "model_type": _TEAM_TYPE,
            "payload": _team_payload(),
        }
        response = client.post("/catalog/agent", json=body)
        assert response.status_code == 400

    def test_create_duplicate_409(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-dup")
        body = {
            "id": "team",
            "kind": "team",
            "namespace": "ns-dup",
            "model_type": _TEAM_TYPE,
            "payload": _team_payload(),
        }
        response = client.post("/catalog/team", json=body)
        assert response.status_code == 409

    def test_create_body_missing_model_type_422(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, _ = api_client
        body = {
            "id": "agent",
            "kind": "agent",
            "namespace": "ns-422",
            "payload": {},
        }
        response = client.post("/catalog/agent", json=body)
        assert response.status_code == 422

    def test_create_agent_without_team_409(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        body = {
            "id": "lone",
            "kind": "agent",
            "namespace": "ns-no-team",
            "model_type": _AGENT_TYPE,
            "payload": _agent_payload("lone"),
        }
        response = client.post("/catalog/agent", json=body)
        assert response.status_code == 409


class TestGet:
    """GET /catalog/{kind}/{id} — AC8."""

    def test_get_happy_path(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-g")
        response = client.get("/catalog/team/team", params={"namespace": "ns-g"})
        assert response.status_code == 200
        assert response.json()["id"] == "team"

    def test_get_missing_404(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get("/catalog/team/team", params={"namespace": "nope"})
        assert response.status_code == 404

    def test_get_kind_mismatch_404(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-km")
        response = client.get("/catalog/agent/team", params={"namespace": "ns-km"})
        assert response.status_code == 404

    def test_get_without_namespace_422(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get("/catalog/team/team")
        assert response.status_code == 422

    def test_get_native_value_entry_returns_standard_shape(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """Story 26.1 / AC 16 — ``GET /catalog/{ns}/{id}`` on a NativeValue
        entry returns the standard entry JSON shape (``model_type``,
        ``payload``) with no special-casing of the kind.
        """
        client, catalog = api_client
        _seed_team(catalog, "ns-native")
        catalog.create(
            Entry(
                id="id_native",
                kind="prompt",
                namespace="ns-native",
                user_id="anonymous",
                model_type="akgentic.catalog.NativeValue",
                payload={"value": "shared-prompt-body"},
            )
        )
        response = client.get("/catalog/prompt/id_native", params={"namespace": "ns-native"})
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "id_native"
        assert body["model_type"] == "akgentic.catalog.NativeValue"
        assert body["payload"] == {"value": "shared-prompt-body"}


class TestUpdate:
    """PUT /catalog/{kind}/{id} — AC9."""

    def test_update_happy_path(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-u")
        body = {
            "id": "team",
            "kind": "team",
            "namespace": "ns-u",
            "model_type": _TEAM_TYPE,
            "description": "updated",
            "payload": _team_payload(),
        }
        response = client.put("/catalog/team/team", params={"namespace": "ns-u"}, json=body)
        assert response.status_code == 200
        assert response.json()["description"] == "updated"
        stored = catalog.get("ns-u", "team")
        assert stored.description == "updated"

    def test_update_missing_404(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        body = {
            "id": "team",
            "kind": "team",
            "namespace": "ns-none",
            "model_type": _TEAM_TYPE,
            "payload": _team_payload(),
        }
        response = client.put("/catalog/team/team", params={"namespace": "ns-none"}, json=body)
        assert response.status_code == 404

    def test_update_id_mismatch_400(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-um")
        _seed_agent(catalog, "ns-um", id="foo")
        body = {
            "id": "bar",
            "kind": "agent",
            "namespace": "ns-um",
            "model_type": _AGENT_TYPE,
            "payload": _agent_payload("bar"),
        }
        response = client.put("/catalog/agent/foo", params={"namespace": "ns-um"}, json=body)
        assert response.status_code == 400

    def test_update_without_namespace_422(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.put(
            "/catalog/team/team",
            json={
                "id": "team",
                "kind": "team",
                "namespace": "x",
                "model_type": _TEAM_TYPE,
                "payload": _team_payload(),
            },
        )
        assert response.status_code == 422


class TestDelete:
    """DELETE /catalog/{kind}/{id} — AC10."""

    def test_delete_returns_204_and_gone(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-d")
        _seed_agent(catalog, "ns-d", id="a-1")
        response = client.delete("/catalog/agent/a-1", params={"namespace": "ns-d"})
        assert response.status_code == 204
        follow = client.get("/catalog/agent/a-1", params={"namespace": "ns-d"})
        assert follow.status_code == 404

    def test_delete_missing_404(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.delete("/catalog/agent/nope", params={"namespace": "ns-d-missing"})
        assert response.status_code == 404

    def test_delete_without_namespace_422(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.delete("/catalog/agent/foo")
        assert response.status_code == 422


# --- Listing and search -----------------------------------------------------


class TestList:
    """GET /catalog/{kind} — AC11."""

    def test_list_filters_by_namespace(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-a", user_id="alice")
        _seed_team(catalog, "ns-b", user_id="alice")
        _seed_agent(catalog, "ns-a", id="a-a", user_id="alice")
        _seed_agent(catalog, "ns-b", id="a-b", user_id="alice")
        response = client.get("/catalog/agent", params={"namespace": "ns-a"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "a-a"

    def test_list_without_filter_returns_all_for_kind(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-la")
        _seed_team(catalog, "ns-lb")
        response = client.get("/catalog/team")
        assert response.status_code == 200
        assert len(response.json()) == 2


class TestSearch:
    """POST /catalog/{kind}/search — AC12, AC26."""

    def test_search_filters_by_user_id_and_namespace(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-s", user_id="alice")
        _seed_team(catalog, "ns-s2", user_id="bob")
        _seed_agent(catalog, "ns-s", id="a1", user_id="alice")
        _seed_agent(catalog, "ns-s2", id="a2", user_id="bob")
        body = {"namespace": "ns-s", "user_id": "alice"}
        response = client.post("/catalog/agent/search", json=body)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "a1"

    def test_search_kind_mismatch_400(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        body = {"kind": "tool"}
        response = client.post("/catalog/agent/search", json=body)
        assert response.status_code == 400

    def test_search_with_no_kind_uses_path(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-sk")
        _seed_agent(catalog, "ns-sk", id="only-agent")
        response = client.post("/catalog/agent/search", json={})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["kind"] == "agent"


# --- Graph routes -----------------------------------------------------------


class TestClone:
    """POST /catalog/clone — AC13, AC27."""

    def test_clone_copies_tree(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "src-ns", user_id="alice")
        _seed_agent(catalog, "src-ns", id="agent-a", user_id="alice")
        response = client.post(
            "/catalog/clone",
            json={
                "src_namespace": "src-ns",
                "src_id": "team",
                "dst_namespace": "dst-ns",
                "dst_user_id": "alice",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["namespace"] == "dst-ns"
        dst_entries = catalog.list_by_namespace("dst-ns")
        ids = {e.id for e in dst_entries}
        assert "team" in ids

    def test_clone_missing_source_404(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.post(
            "/catalog/clone",
            json={
                "src_namespace": "nope",
                "src_id": "team",
                "dst_namespace": "dst",
                "dst_user_id": "anonymous",
            },
        )
        assert response.status_code == 404


class TestResolveEntry:
    """GET /catalog/{kind}/{id}/resolve — AC14."""

    def test_resolve_happy_path(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-re")
        _seed_agent(catalog, "ns-re", id="agent-r")
        response = client.get("/catalog/agent/agent-r/resolve", params={"namespace": "ns-re"})
        assert response.status_code == 200
        body = response.json()
        assert body["config"]["name"] == "agent-r"

    def test_resolve_missing_404(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get("/catalog/agent/nope/resolve", params={"namespace": "ns-none"})
        assert response.status_code == 404

    def test_resolve_kind_mismatch_404(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-rk")
        response = client.get("/catalog/agent/team/resolve", params={"namespace": "ns-rk"})
        assert response.status_code == 404


class TestResolveTeam:
    """GET /catalog/team/{namespace}/resolve — AC15, AC31."""

    def test_resolve_team_happy_path(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-rt")
        response = client.get("/catalog/team/ns-rt/resolve")
        assert response.status_code == 200
        body = response.json()
        # TeamCard has a ``name`` field and an ``entry_point`` field — see
        # akgentic.team.models.TeamCard for the pinned shape.
        assert body["name"] == "team"
        assert "entry_point" in body

    def test_resolve_team_missing_409(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get("/catalog/team/ns-empty/resolve")
        assert response.status_code == 409


class TestResolveTeamProjection:
    """GET /catalog/team/{namespace}/resolve — Story 17.9 display projection.

    Asserts the wire-level behaviour of ``Catalog.load_team``'s ``name`` /
    ``description`` projection. The route's response schema is unchanged
    (the route already returns ``TeamCard.model_dump()``); only the
    values of ``name`` / ``description`` change when the team entry leaves
    them blank.
    """

    @staticmethod
    def _team_payload_no_name() -> dict[str, Any]:
        """Minimal valid ``TeamCard`` payload with empty ``name`` / ``description``.

        Empty strings (rather than omission / ``None``) so the test holds
        against the current pinned ``akgentic-team`` where
        ``TeamCard.name: str`` is required (Pydantic rejects ``None``).
        The projection branch fires on ``""`` per AC1's ``None OR ""`` rule.
        """
        return {
            "name": "",
            "description": "",
            "entry_point": {
                "card": {
                    "role": "entry",
                    "description": "",
                    "skills": [],
                    "agent_class": "akgentic.core.agent.Akgent",
                    "config": {"name": "entry", "role": "entry"},
                },
                "headcount": 1,
                "members": [],
            },
            "members": [],
            "agent_profiles": [],
        }

    @classmethod
    def _put_team_no_name(cls, catalog: Catalog, namespace: str) -> None:
        """Direct repository ``put`` for a team entry that omits ``name``.

        Bypasses ``Catalog.create``'s ``prepare_for_write`` so the on-disk
        payload faithfully omits ``name`` (which Pydantic's TeamCard then
        defaults to ``None`` on resolve). The catalog bootstrap is unaffected
        — these tests do not add sub-entries.
        """
        catalog._repository.put(
            Entry(
                id="team",
                kind="team",
                namespace=namespace,
                model_type=_TEAM_TYPE,
                payload=cls._team_payload_no_name(),
            )
        )

    @staticmethod
    def _put_meta(
        catalog: Catalog,
        namespace: str,
        *,
        payload_name: str | None,
        entry_description: str = "",
    ) -> None:
        """Direct repository ``put`` for a meta entry — bypasses validation."""
        payload: dict[str, Any] = {
            "description": "",
            "properties": {},
            "shareable": False,
        }
        if payload_name is not None:
            payload["name"] = payload_name
        catalog._repository.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace=namespace,
                model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
                description=entry_description,
                payload=payload,
            )
        )

    def test_response_name_projects_meta_name_when_team_name_is_none(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, catalog = api_client
        self._put_team_no_name(catalog, "ns-r-meta")
        self._put_meta(catalog, "ns-r-meta", payload_name="Friendly")
        response = client.get("/catalog/team/ns-r-meta/resolve")
        assert response.status_code == 200
        assert response.json()["name"] == "Friendly"

    def test_response_name_falls_back_to_namespace_when_no_meta(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, catalog = api_client
        self._put_team_no_name(catalog, "ns-r-fallback")
        response = client.get("/catalog/team/ns-r-fallback/resolve")
        assert response.status_code == 200
        assert response.json()["name"] == "ns-r-fallback"

    def test_response_name_preserves_explicit_team_name(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, catalog = api_client
        # Use the standard seed which sets payload["name"] = "team".
        _seed_team(catalog, "ns-r-explicit")
        # Add a meta entry with a different name to confirm no override.
        self._put_meta(catalog, "ns-r-explicit", payload_name="Friendly")
        response = client.get("/catalog/team/ns-r-explicit/resolve")
        assert response.status_code == 200
        assert response.json()["name"] == "team"


class TestReferences:
    """GET /catalog/{kind}/{id}/references — AC16, AC30."""

    def test_references_returns_referrers(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-1")
        # Seed a model entry that agent-a, agent-b will reference via __ref__.
        catalog._repository.put(
            Entry(
                id="id_gpt_41",
                kind="model",
                namespace="ns-1",
                model_type="akgentic.core.agent_card.AgentCard",
                payload=_agent_payload("gpt41"),
            )
        )
        # Put agents with a ref marker in metadata.
        agent_payload = _agent_payload("a")
        agent_payload["metadata"] = {"model": {"__ref__": "id_gpt_41"}}
        catalog._repository.put(
            Entry(
                id="agent-a",
                kind="agent",
                namespace="ns-1",
                model_type=_AGENT_TYPE,
                payload=agent_payload,
            )
        )
        agent_payload_b = _agent_payload("b")
        agent_payload_b["metadata"] = {"model": {"__ref__": "id_gpt_41"}}
        catalog._repository.put(
            Entry(
                id="agent-b",
                kind="agent",
                namespace="ns-1",
                model_type=_AGENT_TYPE,
                payload=agent_payload_b,
            )
        )
        response = client.get("/catalog/model/id_gpt_41/references", params={"namespace": "ns-1"})
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()}
        assert ids == {"agent-a", "agent-b"}

    def test_references_missing_entry_404(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get(
            "/catalog/model/id_gpt_41/references",
            params={"namespace": "ns-empty"},
        )
        assert response.status_code == 404


# --- Schema introspection ---------------------------------------------------


class TestSchema:
    """GET /catalog/schema — AC17, AC28."""

    def test_schema_allowlisted(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get(
            "/catalog/schema",
            params={"model_type": "akgentic.core.agent_card.AgentCard"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body.get("type") == "object"
        assert "properties" in body

    def test_schema_disallowed_prefix_409(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get("/catalog/schema", params={"model_type": "datetime.datetime"})
        assert response.status_code == 409

    def test_schema_missing_class_409(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get(
            "/catalog/schema",
            params={"model_type": "akgentic.does.not.exist.Foo"},
        )
        assert response.status_code == 409


class TestCreateApp:
    """AC4, AC5 — ``create_app`` factory wires YAML + MongoDB backends."""

    def test_yaml_backend_default_path(self, tmp_path: Any) -> None:
        from akgentic.catalog.api.app import create_app

        base = tmp_path / "custom-root"
        app = create_app(backend="yaml", yaml_base_path=base)
        assert app.title == "Akgentic Catalog"
        assert base.exists()
        client = TestClient(app)
        response = client.get("/catalog/model_types")
        assert response.status_code == 200

    def test_unknown_backend_raises(self) -> None:
        from akgentic.catalog.api.app import create_app

        with pytest.raises(ValueError, match="Unknown backend"):
            create_app(backend="sqlite")  # type: ignore[arg-type]

    def test_mongodb_missing_config_raises(self) -> None:
        from akgentic.catalog.api.app import create_app

        with pytest.raises(ValueError, match="mongo_config is required"):
            create_app(backend="mongodb")

    def test_mongodb_backend_wires_collection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("mongomock")
        import mongomock

        from akgentic.catalog.api.app import create_app
        from akgentic.catalog.repositories.mongo import MongoCatalogConfig

        config = MongoCatalogConfig(connection_string="mongodb://x", database="db_test")

        def _fake_client(self: MongoCatalogConfig) -> mongomock.MongoClient:
            return mongomock.MongoClient()

        monkeypatch.setattr(MongoCatalogConfig, "create_client", _fake_client)
        app = create_app(backend="mongodb", mongo_config=config)
        assert app.title == "Akgentic Catalog"


class TestModelTypes:
    """GET /catalog/model_types — AC18, AC29."""

    def test_model_types_lists_imported_classes(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, _ = api_client
        # Ensure at least one known class is loaded in-process.
        import akgentic.core.agent_card  # noqa: F401

        response = client.get("/catalog/model_types")
        assert response.status_code == 200
        paths = response.json()
        assert isinstance(paths, list)
        assert all(isinstance(p, str) and p.startswith("akgentic.") for p in paths)
        assert "akgentic.core.agent_card.AgentCard" in paths


# --- Namespace bundle routes (Story 16.2) ----------------------------------


class TestNamespaceExport:
    """GET /catalog/namespace/{namespace}/export — AC23, AC25."""

    def test_export_happy_path(self, api_client: tuple[TestClient, Catalog]) -> None:
        import yaml

        client, catalog = api_client
        _seed_team(catalog, "ns-exp", user_id="alice")
        _seed_agent(catalog, "ns-exp", id="a-1", user_id="alice")
        response = client.get("/catalog/namespace/ns-exp/export")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/yaml")
        doc = yaml.safe_load(response.text)
        # Story 18.2 — eight top-level keys in declaration order. The header
        # (name/description/properties/shareable/public) is auto-synthesised
        # from the team-payload fallback when no _meta entry exists in the
        # namespace.
        assert list(doc.keys()) == [
            "namespace",
            "user_id",
            "name",
            "description",
            "properties",
            "shareable",
            "public",
            "entries",
        ]
        assert doc["namespace"] == "ns-exp"
        assert doc["shareable"] is False
        assert doc["public"] is False
        assert set(doc["entries"].keys()) == {"team", "a-1"}

    def test_export_empty_namespace_409(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get("/catalog/namespace/nope/export")
        assert response.status_code == 409


class TestNamespaceImport:
    """POST /catalog/namespace/import — AC24, AC25, AC33."""

    def _build_bundle(self) -> str:
        import yaml as _yaml

        doc = {
            "namespace": "ns-imp",
            "user_id": "alice",
            "entries": {
                "team": {
                    "kind": "team",
                    "model_type": _TEAM_TYPE,
                    "description": "",
                    "payload": _team_payload(),
                },
                "a": {
                    "kind": "agent",
                    "model_type": _AGENT_TYPE,
                    "description": "",
                    "payload": _agent_payload("a"),
                },
            },
        }
        return _yaml.safe_dump(doc, sort_keys=False)

    def test_import_happy_path(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        yaml_text = self._build_bundle()
        response = client.post(
            "/catalog/namespace/import",
            content=yaml_text.encode("utf-8"),
            headers={"Content-Type": "application/yaml"},
        )
        assert response.status_code == 201
        data = response.json()
        assert isinstance(data, list)
        assert {e["id"] for e in data} == {"team", "a"}

    def test_import_malformed_yaml_422(self, api_client: tuple[TestClient, Catalog]) -> None:
        """Malformed YAML is a transport-level structural failure → HTTP 422.

        Mirrors the ``/namespace/validate`` contract — the router intercepts
        ``yaml.YAMLError`` before the catalog-service call so clients can
        distinguish syntactic YAML breakage from catalog-invariant (409)
        failures.
        """
        client, _ = api_client
        response = client.post(
            "/catalog/namespace/import",
            content=b"{{{ not yaml }",
        )
        assert response.status_code == 422
        assert "failed to parse bundle YAML" in response.json()["detail"]

    def test_import_missing_team_409(self, api_client: tuple[TestClient, Catalog]) -> None:
        import yaml as _yaml

        client, _ = api_client
        doc = {
            "namespace": "ns-noteam",
            "user_id": "alice",
            "entries": {
                "a": {
                    "kind": "agent",
                    "model_type": _AGENT_TYPE,
                    "description": "",
                    "payload": _agent_payload("a"),
                }
            },
        }
        response = client.post(
            "/catalog/namespace/import",
            content=_yaml.safe_dump(doc).encode("utf-8"),
        )
        assert response.status_code == 409
        assert any("has no team entry and no meta entry" in e for e in response.json()["errors"])

    def test_import_dangling_ref_409(self, api_client: tuple[TestClient, Catalog]) -> None:
        import yaml as _yaml

        client, catalog = api_client
        # Pre-seed namespace with a ghost target so prepare_for_write passes,
        # leaving the dangling-ref-in-bundle check as the failure surface.
        _seed_team(catalog, "ns-dref", user_id="alice")
        catalog.create(
            Entry(
                id="ghost",
                kind="model",
                namespace="ns-dref",
                user_id="alice",
                model_type=_AGENT_TYPE,  # any allowlisted class with payload shape compat
                payload=_agent_payload("ghost"),
            )
        )
        doc = {
            "namespace": "ns-dref",
            "user_id": "alice",
            "entries": {
                "team": {
                    "kind": "team",
                    "model_type": _TEAM_TYPE,
                    "description": "",
                    "payload": _team_payload(),
                },
                "dangler": {
                    "kind": "agent",
                    "model_type": _AGENT_TYPE,
                    "description": "",
                    "payload": {
                        "role": "r",
                        "description": "",
                        "skills": [],
                        "agent_class": "akgentic.core.agent.Akgent",
                        "config": {"name": "dangler", "role": "r"},
                        "routes_to": [],
                        "metadata": {"ref": {"__ref__": "ghost", "__type__": _AGENT_TYPE}},
                    },
                },
            },
        }
        response = client.post(
            "/catalog/namespace/import",
            content=_yaml.safe_dump(doc).encode("utf-8"),
        )
        assert response.status_code == 409
        assert any("not found in bundle" in e for e in response.json()["errors"])

    def test_import_non_utf8_body_400(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.post(
            "/catalog/namespace/import",
            content=b"\xff\xfe\xfd",
        )
        assert response.status_code == 400
        assert "UTF-8" in response.json()["detail"]


class TestNamespaceBundleRoundTrip:
    """Atomic replace through HTTP (AC34)."""

    def test_round_trip_atomic_replace(self, api_client: tuple[TestClient, Catalog]) -> None:
        import yaml as _yaml

        client, catalog = api_client
        # Seed ns-a with {team, agent_a, tool_x}.
        _seed_team(catalog, "ns-a", user_id="alice")
        _seed_agent(catalog, "ns-a", id="agent_a", user_id="alice")
        catalog.create(
            Entry(
                id="tool_x",
                kind="tool",
                namespace="ns-a",
                user_id="alice",
                model_type=_AGENT_TYPE,
                payload=_agent_payload("tool_x"),
            )
        )

        # Bundle: team + agent_a_modified + tool_y (tool_x dropped).
        doc = {
            "namespace": "ns-a",
            "user_id": "alice",
            "entries": {
                "team": {
                    "kind": "team",
                    "model_type": _TEAM_TYPE,
                    "description": "",
                    "payload": _team_payload(),
                },
                "agent_a": {
                    "kind": "agent",
                    "model_type": _AGENT_TYPE,
                    "description": "updated",
                    "payload": _agent_payload("agent_a"),
                },
                "tool_y": {
                    "kind": "tool",
                    "model_type": _AGENT_TYPE,
                    "description": "",
                    "payload": _agent_payload("tool_y"),
                },
            },
        }
        response = client.post(
            "/catalog/namespace/import",
            content=_yaml.safe_dump(doc).encode("utf-8"),
        )
        assert response.status_code == 201

        # Verify per-entry state via HTTP GETs.
        r_agent = client.get("/catalog/agent/agent_a", params={"namespace": "ns-a"})
        assert r_agent.status_code == 200
        assert r_agent.json()["description"] == "updated"

        r_tool_y = client.get("/catalog/tool/tool_y", params={"namespace": "ns-a"})
        assert r_tool_y.status_code == 200

        r_tool_x = client.get("/catalog/tool/tool_x", params={"namespace": "ns-a"})
        assert r_tool_x.status_code == 404


# --- Namespace validation endpoints (Story 16.3) ---------------------------


def _validation_bundle(
    namespace: str = "ns-v",
    user_id: str = "alice",
    extra_entries: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Build a minimal valid bundle YAML, optionally appending extra entries."""
    import yaml as _yaml

    entries_map: dict[str, Any] = {
        "team": {
            "kind": "team",
            "model_type": _TEAM_TYPE,
            "description": "",
            "payload": _team_payload(),
        },
        "a": {
            "kind": "agent",
            "model_type": _AGENT_TYPE,
            "description": "",
            "payload": _agent_payload("a"),
        },
    }
    if extra_entries:
        entries_map.update(extra_entries)
    doc = {"namespace": namespace, "user_id": user_id, "entries": entries_map}
    return _yaml.safe_dump(doc, sort_keys=False)


class TestNamespaceValidateGet:
    """``GET /catalog/namespace/{namespace}/validate`` — AC36."""

    def test_get_happy_path(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-get-ok", user_id="alice")
        _seed_agent(catalog, "ns-get-ok", id="agent-a", user_id="alice")
        response = client.get("/catalog/namespace/ns-get-ok/validate")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["namespace"] == "ns-get-ok"
        assert body["global_errors"] == []
        assert body["entry_issues"] == []

    def test_get_empty_namespace(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.get("/catalog/namespace/ns-empty/validate")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["namespace"] == "ns-empty"  # AC18 patch
        assert body["global_errors"] == ["namespace has no entries"]

    def test_get_dangling_ref_corruption(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-get-dr", user_id="alice")
        # Bypass the service to seed a payload with a dangling ref.
        dangler_payload = _agent_payload("dangler")
        dangler_payload["metadata"] = {"ref": {"__ref__": "ghost"}}
        catalog._repository.put(
            Entry(
                id="dangler",
                kind="agent",
                namespace="ns-get-dr",
                user_id="alice",
                model_type=_AGENT_TYPE,
                payload=dangler_payload,
            )
        )
        response = client.get("/catalog/namespace/ns-get-dr/validate")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert any("dangling ref" in m for m in body["global_errors"])


class TestNamespaceValidatePost:
    """``POST /catalog/namespace/validate`` — AC36, AC37."""

    def test_post_happy_path(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        yaml_text = _validation_bundle(namespace="ns-post-ok", user_id="alice")
        response = client.post(
            "/catalog/namespace/validate",
            content=yaml_text.encode("utf-8"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["namespace"] == "ns-post-ok"

    def test_post_malformed_yaml_422(self, api_client: tuple[TestClient, Catalog]) -> None:
        """Malformed YAML → HTTP 422 per shard 07 transport-level contract."""
        client, _ = api_client
        response = client.post(
            "/catalog/namespace/validate",
            content=b"{{{ not yaml",
        )
        assert response.status_code == 422
        assert "failed to parse bundle YAML" in response.json()["detail"]

    def test_post_non_utf8_body_422(self, api_client: tuple[TestClient, Catalog]) -> None:
        """Non-UTF-8 request body → HTTP 422 (structural request-body failure)."""
        client, _ = api_client
        response = client.post(
            "/catalog/namespace/validate",
            content=b"\xff\xfe\xfd",
        )
        assert response.status_code == 422
        assert "not valid UTF-8" in response.json()["detail"]

    def test_post_allowlist_violation_returns_200_with_ok_false(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        import yaml as _yaml

        client, _ = api_client
        doc = {
            "namespace": "ns-post-allow",
            "user_id": "alice",
            "entries": {
                "team": {
                    "kind": "team",
                    "model_type": _TEAM_TYPE,
                    "description": "",
                    "payload": _team_payload(),
                },
                "bad": {
                    "kind": "model",
                    "model_type": "builtins.dict",
                    "description": "",
                    "payload": {},
                },
            },
        }
        yaml_text = _yaml.safe_dump(doc, sort_keys=False)
        response = client.post(
            "/catalog/namespace/validate",
            content=yaml_text.encode("utf-8"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert any("outside allowlist" in m for m in body["global_errors"])

    def test_post_dangling_ref_returns_200_with_ok_false(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, _ = api_client
        dangler_payload = _agent_payload("dangler")
        dangler_payload["metadata"] = {"ref": {"__ref__": "ghost"}}
        yaml_text = _validation_bundle(
            namespace="ns-post-dangling",
            user_id="alice",
            extra_entries={
                "dangler": {
                    "kind": "agent",
                    "model_type": _AGENT_TYPE,
                    "description": "",
                    "payload": dangler_payload,
                }
            },
        )
        response = client.post(
            "/catalog/namespace/validate",
            content=yaml_text.encode("utf-8"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert any("dangling ref" in m for m in body["global_errors"])

    def test_post_missing_team_returns_200_with_ok_false(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        import yaml as _yaml

        client, _ = api_client
        doc = {
            "namespace": "ns-post-noteam",
            "user_id": "alice",
            "entries": {
                "a": {
                    "kind": "agent",
                    "model_type": _AGENT_TYPE,
                    "description": "",
                    "payload": _agent_payload("a"),
                }
            },
        }
        response = client.post(
            "/catalog/namespace/validate",
            content=_yaml.safe_dump(doc, sort_keys=False).encode("utf-8"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert any("has no team entry and no meta entry" in m for m in body["global_errors"])

    def test_service_vs_http_divergence_on_malformed_yaml(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """AC24 — service returns report; HTTP returns 422.

        Service-level: ``Catalog.validate_namespace_yaml("{{{")`` returns a
        report with ``ok=False`` and the parse error in ``global_errors``
        (no exception). HTTP-level: ``POST`` with the same payload surfaces a
        422 at the transport boundary, per shard 07's "structural
        request-body errors (malformed YAML) still surface as 422" contract.
        """
        client, catalog = api_client
        report = catalog.validate_namespace_yaml("{{{")
        assert report.ok is False
        assert report.namespace is None
        assert any("Failed to parse bundle YAML" in m for m in report.global_errors)

        response = client.post(
            "/catalog/namespace/validate",
            content=b"{{{",
        )
        assert response.status_code == 422

    def test_post_json_round_trip(self, api_client: tuple[TestClient, Catalog]) -> None:
        """AC37 — the 200 response body deserialises into NamespaceValidationReport."""
        from akgentic.catalog.validation import NamespaceValidationReport

        client, _ = api_client
        yaml_text = _validation_bundle(namespace="ns-roundtrip", user_id="alice")
        response = client.post(
            "/catalog/namespace/validate",
            content=yaml_text.encode("utf-8"),
        )
        assert response.status_code == 200
        parsed = NamespaceValidationReport.model_validate_json(response.text)
        assert parsed.ok is True
        assert parsed.namespace == "ns-roundtrip"
        assert parsed.global_errors == []
        assert parsed.entry_issues == []


# --- Compliance-review regression tests (Epic 16 spec-compliance fix) -------


def test_router_namespace_validate_malformed_yaml_returns_422(
    api_client: tuple[TestClient, Catalog],
) -> None:
    """``POST /catalog/namespace/validate`` returns 422 on malformed YAML.

    Regression for Epic 16 spec-compliance review (BLOCKING V4): shard 07
    pins 422 — not 400 — as the transport-level status for structural
    request-body errors (malformed YAML) on validation endpoints.
    """
    client, _ = api_client
    response = client.post(
        "/catalog/namespace/validate",
        content=b"{{{ still : not : yaml",
    )
    assert response.status_code == 422
    assert "failed to parse bundle YAML" in response.json()["detail"]


# --- List namespaces (Story 16.6) ------------------------------------------


class TestListNamespaces:
    """``GET /catalog/namespaces`` — Story 16.6 ACs 1-5."""

    def test_empty_catalog_returns_empty_list(self, api_client: tuple[TestClient, Catalog]) -> None:
        """AC #2 — empty catalog → HTTP 200, ``[]``."""
        client, _ = api_client
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_summaries_sorted_by_namespace(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """AC #1 — two namespaces with team entries project to sorted summaries.

        Story 17.8 — ``name`` falls back to the namespace identifier (NOT
        the team's payload ``name``) when no meta entry exists for the
        namespace. Both fixtures here are team-only, so each row's ``name``
        is the namespace identifier.
        """
        client, catalog = api_client
        # Seed namespaces out of alphabetical order and with distinct team
        # names + descriptions to verify the projection. Team payload
        # ``name`` is set to a deliberately misleading sentinel — Story 17.8
        # demands the picker NEVER read team.payload["name"].
        team_b = _team_payload()
        team_b["name"] = "TeamPayloadIgnored-B"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="ns-b",
                model_type=_TEAM_TYPE,
                description="beta team",
                payload=team_b,
            )
        )
        team_a = _team_payload()
        team_a["name"] = "TeamPayloadIgnored-A"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="ns-a",
                model_type=_TEAM_TYPE,
                description="alpha team",
                payload=team_a,
            )
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        data = response.json()
        assert data == [
            {
                "namespace": "ns-a",
                "name": "ns-a",
                "description": "alpha team",
                "team": True,
                "shareable": False,
                "public": False,
            },
            {
                "namespace": "ns-b",
                "name": "ns-b",
                "description": "beta team",
                "team": True,
                "shareable": False,
                "public": False,
            },
        ]

    def test_missing_name_in_payload_falls_back_to_namespace_identifier(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """Story 17.8 — defensive: missing/absent team-payload ``name`` is irrelevant.

        Bypasses the service to seed a team entry whose payload lacks the
        ``name`` key (a state ``create`` would reject). Pre-17.8 the row's
        ``name`` was ``""``; the new two-rung rule projects the namespace
        identifier instead. The team payload's ``name`` is never read either
        way.
        """
        client, catalog = api_client
        payload_without_name = _team_payload()
        payload_without_name.pop("name")
        catalog._repository.put(
            Entry(
                id="team",
                kind="team",
                namespace="ns-no-name",
                model_type=_TEAM_TYPE,
                description="",
                payload=payload_without_name,
            )
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        data = response.json()
        assert data == [
            {
                "namespace": "ns-no-name",
                "name": "ns-no-name",
                "description": "",
                "team": True,
                "shareable": False,
                "public": False,
            }
        ]

    def test_namespace_without_team_entry_is_skipped(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """AC #3 — namespace with sub-entries but no team entry is omitted.

        The service's ``create`` invariants forbid seeding an agent without a
        team (tested via 409 in :class:`TestCreate`), so we bypass via the
        repository to synthesize the corrupted state, then verify the
        endpoint skips it silently (no error, no partial row).
        """
        client, catalog = api_client
        catalog._repository.put(
            Entry(
                id="orphan-agent",
                kind="agent",
                namespace="ns-no-team",
                model_type=_AGENT_TYPE,
                payload=_agent_payload("orphan"),
            )
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        namespaces = [row["namespace"] for row in response.json()]
        assert "ns-no-team" not in namespaces

    def test_includes_entries_across_user_ids(self, api_client: tuple[TestClient, Catalog]) -> None:
        """AC #4 — the endpoint does not filter by ``user_id``.

        Community-tier (``user_id="anonymous"``) and multi-tenant
        (``user_id="alice"``, ``user_id="bob"``) namespaces must all appear.
        Tenancy filtering is a caller concern.
        """
        client, catalog = api_client
        _seed_team(catalog, "ns-public", user_id="anonymous")
        _seed_team(catalog, "ns-alice", user_id="alice")
        _seed_team(catalog, "ns-bob", user_id="bob")
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        namespaces = [row["namespace"] for row in response.json()]
        assert namespaces == ["ns-alice", "ns-bob", "ns-public"]

    def test_openapi_declares_response_model(self, api_client: tuple[TestClient, Catalog]) -> None:
        """AC #5 — ``/openapi.json`` exposes the operation with the right shape."""
        client, _ = api_client
        response = client.get("/openapi.json")
        assert response.status_code == 200
        spec = response.json()
        op = spec["paths"]["/catalog/namespaces"]["get"]
        # Response schema is declared as ``list[NamespaceSummary]``.
        content = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert content["type"] == "array"
        # Resolve the referenced component name.
        ref = content["items"]["$ref"]
        assert ref.endswith("/NamespaceSummary")
        component = spec["components"]["schemas"]["NamespaceSummary"]
        # Story 18.2 — six pinned fields in declaration order.
        assert set(component["properties"].keys()) == {
            "namespace",
            "name",
            "description",
            "team",
            "shareable",
            "public",
        }
        # AC5 — declaration order pinned via OpenAPI's required-list ordering
        # (FastAPI emits declaration order in ``required:`` for required
        # fields). All six fields are required (no defaults that allow
        # omission in the response shape).
        assert component["required"] == [
            "namespace",
            "name",
            "description",
            "team",
            "shareable",
            "public",
        ]


def test_router_namespace_import_malformed_yaml_returns_422(
    api_client: tuple[TestClient, Catalog],
) -> None:
    """``POST /catalog/namespace/import`` returns 422 on malformed YAML.

    Regression for Epic 16 spec-compliance review: without the router-level
    ``yaml.YAMLError`` guard, malformed YAML reaches ``load_namespace`` and
    surfaces as ``CatalogValidationError`` → 409, which the client cannot
    distinguish from catalog-invariant failures. Mirrors the ``/validate``
    endpoint contract.
    """
    client, _ = api_client
    response = client.post(
        "/catalog/namespace/import",
        content=b"{{{ still : not : yaml",
    )
    assert response.status_code == 422
    assert "failed to parse bundle YAML" in response.json()["detail"]


# --- Story 17.2 — namespace-meta routes -----------------------------------


_NAMESPACE_META_TYPE_ROUTER = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


def _seed_meta_entry(
    catalog: Catalog,
    namespace: str,
    user_id: str = "anonymous",
    name: str = "Tenant 42",
    description: str = "primary tenant",
) -> Entry:
    """Seed a kind=meta entry directly through the catalog (Story 17.2)."""
    return catalog.create(
        Entry(
            id="_meta",
            kind="meta",
            namespace=namespace,
            user_id=user_id,
            model_type=_NAMESPACE_META_TYPE_ROUTER,
            description=description,
            payload={"name": name, "description": description, "properties": {}},
        )
    )


class TestListNamespacesMetaFallback:
    """Story 17.2 AC6 — meta-then-team fallback for ``GET /catalog/namespaces``."""

    def test_meta_entry_takes_precedence_over_team(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, catalog = api_client
        team_payload = _team_payload()
        team_payload["name"] = "Team Display Name"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="tenant-42",
                model_type=_TEAM_TYPE,
                description="team description",
                payload=team_payload,
            )
        )
        _seed_meta_entry(
            catalog,
            namespace="tenant-42",
            user_id="anonymous",
            name="Friendly Display",
            description="meta description",
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        assert rows == [
            {
                "namespace": "tenant-42",
                "name": "Friendly Display",
                "description": "meta description",
                "team": True,
                "shareable": False,
                "public": False,
            }
        ]

    def test_namespace_identifier_fallback_when_no_meta_entry(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """Story 17.8 — when no meta entry exists, the row's ``name`` is the namespace identifier.

        The team's ``payload["name"]`` is set to a deliberately misleading
        sentinel and the assertion isolates the rule that the picker never
        reads it.
        """
        client, catalog = api_client
        team_payload = _team_payload()
        team_payload["name"] = "TeamPayloadIgnored"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="tenant-42",
                model_type=_TEAM_TYPE,
                description="team description",
                payload=team_payload,
            )
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        assert rows == [
            {
                "namespace": "tenant-42",
                "name": "tenant-42",
                "description": "team description",
                "team": True,
                "shareable": False,
                "public": False,
            }
        ]

    def test_meta_with_empty_name_falls_back_to_namespace_identifier(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """Story 17.8 — meta exists but ``name`` is missing/empty → namespace identifier wins.

        Pre-17.8 (Story 17.7): a graceful-degradation rung re-anchored the
        row's ``name`` on the team payload. Post-17.8: the fallback is the
        namespace identifier itself; the team payload's ``name`` is never
        read.
        """
        client, catalog = api_client
        team_payload = _team_payload()
        team_payload["name"] = "TeamPayloadIgnored"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="tenant-42",
                model_type=_TEAM_TYPE,
                description="team description",
                payload=team_payload,
            )
        )
        # Bypass NamespaceMeta validation — directly seed a meta entry whose
        # payload lacks ``name`` (a state put cannot reject because the entry
        # is structurally valid; only ``NamespaceMeta`` validation enforces
        # name presence at the route layer).
        catalog._repository.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace="tenant-42",
                model_type=_NAMESPACE_META_TYPE_ROUTER,
                description="meta description",
                payload={"description": "meta description", "properties": {}},
            )
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        # name falls back to the namespace identifier; description comes
        # from meta.
        assert rows == [
            {
                "namespace": "tenant-42",
                "name": "tenant-42",
                "description": "meta description",
                "team": True,
                "shareable": False,
                "public": False,
            }
        ]


class TestGetNamespaceMeta:
    """Story 17.2 AC7 — ``GET /catalog/namespace/{ns}/meta``."""

    def test_returns_meta_entry_when_present(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        _seed_meta_entry(catalog, "tenant-42", name="Tenant 42")
        response = client.get("/catalog/namespace/tenant-42/meta")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "_meta"
        assert body["kind"] == "meta"
        assert body["namespace"] == "tenant-42"
        assert body["payload"]["name"] == "Tenant 42"

    def test_returns_404_when_absent(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        response = client.get("/catalog/namespace/tenant-42/meta")
        assert response.status_code == 404
        assert "errors" in response.json()


class TestPutNamespaceMeta:
    """Story 17.2 AC8 — ``PUT /catalog/namespace/{ns}/meta`` upsert."""

    def test_create_branch_returns_201(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        body = {"name": "Tenant 42", "description": "primary", "properties": {"tier": "gold"}}
        response = client.put("/catalog/namespace/tenant-42/meta", json=body)
        assert response.status_code == 201
        entry = response.json()
        assert entry["kind"] == "meta"
        assert entry["id"] == "_meta"
        assert entry["namespace"] == "tenant-42"
        assert entry["payload"]["name"] == "Tenant 42"
        assert entry["payload"]["properties"] == {"tier": "gold"}
        assert entry["model_type"] == _NAMESPACE_META_TYPE_ROUTER

    def test_update_branch_returns_200(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        _seed_meta_entry(catalog, "tenant-42", name="Old Name")
        body = {"name": "New Name", "description": "updated", "properties": {}}
        response = client.put("/catalog/namespace/tenant-42/meta", json=body)
        assert response.status_code == 200
        entry = response.json()
        assert entry["payload"]["name"] == "New Name"
        assert entry["description"] == "updated"

    def test_body_kind_id_namespace_model_type_silently_ignored(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """The handler substitutes ``kind`` / ``id`` / ``namespace`` / ``model_type``
        regardless of body content. ``NamespaceMeta`` does not declare those
        fields, so a JSON payload that carries them is simply not parsed.
        """
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        body = {
            "name": "Tenant 42",
            "description": "",
            "properties": {},
            # These should be ignored by the handler:
            "kind": "team",
            "id": "evil-id",
            "namespace": "evil-namespace",
            "model_type": "akgentic.evil",
        }
        response = client.put("/catalog/namespace/tenant-42/meta", json=body)
        assert response.status_code == 201
        entry = response.json()
        assert entry["kind"] == "meta"
        assert entry["id"] == "_meta"
        assert entry["namespace"] == "tenant-42"
        assert entry["model_type"] == _NAMESPACE_META_TYPE_ROUTER

    def test_malformed_body_missing_name_returns_422(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """Malformed body (missing required ``name``) surfaces as 422 from
        Pydantic ``ValidationError`` raised inside ``NamespaceMeta.model_validate``.
        """
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        body = {"description": "missing name", "properties": {}}
        response = client.put("/catalog/namespace/tenant-42/meta", json=body)
        assert response.status_code == 422

    def test_no_team_creates_meta_with_anonymous_user_id(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """When no team exists, PUT meta creates the first anchor entry with
        user_id == "anonymous" (Story 17.10 — meta-only namespace bootstrap).
        """
        client, _ = api_client
        body = {"name": "ghost", "description": "", "properties": {}}
        response = client.put("/catalog/namespace/ghost-ns/meta", json=body)
        assert response.status_code == 201
        entry = response.json()
        assert entry["user_id"] == "anonymous"
        assert entry["kind"] == "meta"
        assert entry["namespace"] == "ghost-ns"

    def test_no_team_update_preserves_user_id(self, api_client: tuple[TestClient, Catalog]) -> None:
        """Subsequent PUT (update) preserves the _meta.user_id from the first write."""
        client, _ = api_client
        body = {"name": "ghost", "description": "", "properties": {}}
        response = client.put("/catalog/namespace/ghost-ns/meta", json=body)
        assert response.status_code == 201
        first_user_id = response.json()["user_id"]
        assert first_user_id == "anonymous"

        # Update the meta entry
        body_update = {"name": "ghost-updated", "description": "updated", "properties": {}}
        response2 = client.put("/catalog/namespace/ghost-ns/meta", json=body_update)
        assert response2.status_code == 200
        entry = response2.json()
        assert entry["user_id"] == first_user_id
        assert entry["payload"]["name"] == "ghost-updated"


# --- Story 17.7 — union discovery (team + meta) for /catalog/namespaces ---


_NAMESPACE_META_TYPE_17_7 = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


def _seed_meta(
    catalog: Catalog,
    namespace: str,
    *,
    name: str = "Display",
    description: str = "",
    shareable: bool = False,
    user_id: str = "anonymous",
) -> Entry:
    """Seed a kind=meta entry directly through the catalog (typed-bool shape)."""
    return catalog.create(
        Entry(
            id="_meta",
            kind="meta",
            namespace=namespace,
            user_id=user_id,
            model_type=_NAMESPACE_META_TYPE_17_7,
            description=description,
            payload={
                "name": name,
                "description": description,
                "properties": {},
                "shareable": shareable,
            },
        )
    )


class TestListNamespacesUnionDiscovery:
    """Story 17.7 / AC19 — union discovery surfaces team-only, team+meta, meta-only namespaces."""

    def test_three_namespace_fixture_team_meta_union(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """Three-namespace fixture with all three row classes per AC19.

        * ``ns-team-only`` — team entry, no meta. Expect ``team=True, shareable=False``.
        * ``ns-team-meta-shared`` — team + meta (shareable=True). Expect ``team=True,
          shareable=True``.
        * ``ns-meta-only`` — meta entry only (no team), shareable=True. Expect
          ``team=False, shareable=True``. This is the regression guard for the
          union-discovery widening — pre-17.7 this row was invisible.
        """
        client, catalog = api_client

        # Case 1: team-only.
        # Story 17.8 — team payload ``name`` is a deliberately misleading
        # sentinel (NoLongerAuthoritative) so the assertion below clearly
        # demonstrates that the team's payload ``name`` is NOT being read.
        team_payload = _team_payload()
        team_payload["name"] = "NoLongerAuthoritative"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="ns-team-only",
                model_type=_TEAM_TYPE,
                description="team only desc",
                payload=team_payload,
            )
        )

        # Case 2: team + meta with shareable=True.
        team_payload_2 = _team_payload()
        team_payload_2["name"] = "Team Plus Shared Meta"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="ns-team-meta-shared",
                model_type=_TEAM_TYPE,
                description="team team desc",
                payload=team_payload_2,
            )
        )
        _seed_meta(
            catalog,
            "ns-team-meta-shared",
            name="Friendly Display",
            description="meta description",
            shareable=True,
        )

        # Case 3: meta-only (no team), shareable=True. Bypass create to avoid the
        # bootstrap invariant — meta-only library namespaces are out-of-band
        # for ``Catalog.create`` but legitimate state for the discovery query.
        catalog._repository.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace="ns-meta-only",
                user_id="anonymous",
                model_type=_NAMESPACE_META_TYPE_17_7,
                description="meta-only desc",
                payload={
                    "name": "Library NS",
                    "description": "meta-only desc",
                    "properties": {},
                    "shareable": True,
                },
            )
        )

        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()

        # Sorted alphabetically by namespace.
        assert [r["namespace"] for r in rows] == [
            "ns-meta-only",
            "ns-team-meta-shared",
            "ns-team-only",
        ]

        by_ns = {r["namespace"]: r for r in rows}
        # Story 17.8 — ``name`` is the namespace identifier (NOT
        # ``"NoLongerAuthoritative"`` — the team payload's ``name`` is no
        # longer a picker display source).
        assert by_ns["ns-team-only"] == {
            "namespace": "ns-team-only",
            "name": "ns-team-only",
            "description": "team only desc",
            "team": True,
            "shareable": False,
            "public": False,
        }
        assert by_ns["ns-team-meta-shared"] == {
            "namespace": "ns-team-meta-shared",
            "name": "Friendly Display",
            "description": "meta description",
            "team": True,
            "shareable": True,
            "public": False,
        }
        assert by_ns["ns-meta-only"] == {
            "namespace": "ns-meta-only",
            "name": "Library NS",
            "description": "meta-only desc",
            "team": False,
            "shareable": True,
            "public": False,
        }


class TestListNamespacesIgnoresTeamPayloadName:
    """Story 17.8 — regression guard: ``team.payload["name"]`` is never read by the picker.

    Pre-17.8 the projection chain re-anchored the row's ``name`` on the
    team's payload as the second rung of three. Post-17.8 the chain is
    two-rung (``meta.name`` → namespace identifier); the team payload's
    ``name`` is preserved on disk but is no longer authoritative for the
    picker. Each test below sets the team payload's ``name`` to a
    deliberately distinct sentinel so a regression that re-introduces the
    rung-2 fallback would surface a sentinel string in the assertion
    failure — making the regression visible at a glance.
    """

    def test_team_only_with_nonempty_payload_name_yields_namespace_identifier(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """(a) team-only with non-empty payload ``name`` → row ``name`` is namespace identifier."""
        client, catalog = api_client
        team_payload = _team_payload()
        team_payload["name"] = "NoLongerAuthoritative"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="ns-team-payload-ignored",
                model_type=_TEAM_TYPE,
                description="",
                payload=team_payload,
            )
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        by_ns = {r["namespace"]: r for r in rows}
        assert by_ns["ns-team-payload-ignored"]["name"] == "ns-team-payload-ignored"

    def test_team_only_with_empty_payload_name_yields_namespace_identifier(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """(b) team-only with empty payload ``name`` → row ``name`` is namespace identifier.

        Pre-17.8 (rung-2 path): an empty team-payload ``name`` projected
        as ``""``. Post-17.8: the namespace identifier is used in BOTH
        cases (a) and (b) — the team payload's ``name`` is never consulted.
        """
        client, catalog = api_client
        team_payload = _team_payload()
        team_payload["name"] = ""
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="ns-empty-team-name",
                model_type=_TEAM_TYPE,
                description="",
                payload=team_payload,
            )
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        by_ns = {r["namespace"]: r for r in rows}
        assert by_ns["ns-empty-team-name"]["name"] == "ns-empty-team-name"

    def test_team_plus_meta_with_nonempty_meta_name_meta_wins(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """(c) team + meta with non-empty meta ``name`` → meta wins; team payload irrelevant.

        The team payload's ``name`` is set to a value distinct from BOTH
        the meta name AND the namespace identifier so the assertion
        verifies the rung-1 win, not happenstance.
        """
        client, catalog = api_client
        team_payload = _team_payload()
        team_payload["name"] = "ShouldBeIgnored"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="ns-meta-wins",
                model_type=_TEAM_TYPE,
                description="",
                payload=team_payload,
            )
        )
        _seed_meta(catalog, "ns-meta-wins", name="MetaWins")
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        by_ns = {r["namespace"]: r for r in rows}
        assert by_ns["ns-meta-wins"]["name"] == "MetaWins"

    def test_empty_meta_name_with_team_falls_back_to_namespace_identifier(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """Story 17.8 — empty meta ``name`` with a team falls back to namespace identifier.

        Pre-17.8: an empty meta ``name`` triggered the graceful-degradation
        fallback to ``team.payload["name"]``. Post-17.8: the fallback is the
        namespace identifier. The team payload's ``name`` is set to a
        deliberately distinct sentinel so the test verifies that even with
        both a team AND a (name-empty) meta in hand, the team payload is
        bypassed.
        """
        client, catalog = api_client
        team_payload = _team_payload()
        team_payload["name"] = "WouldHaveBeenUsed"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="ns-team-only-with-empty-meta",
                model_type=_TEAM_TYPE,
                description="",
                payload=team_payload,
            )
        )
        # Bypass NamespaceMeta validation — directly seed a meta entry with
        # an empty ``name`` (a state the route layer would reject but the
        # repository accepts).
        catalog._repository.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace="ns-team-only-with-empty-meta",
                model_type=_NAMESPACE_META_TYPE_17_7,
                description="meta description",
                payload={
                    "name": "",
                    "description": "meta description",
                    "properties": {},
                    "shareable": False,
                },
            )
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        by_ns = {r["namespace"]: r for r in rows}
        # NOT ``"WouldHaveBeenUsed"`` (team payload — never read), NOT ``""``
        # (the empty meta name) — the namespace identifier wins.
        assert by_ns["ns-team-only-with-empty-meta"]["name"] == "ns-team-only-with-empty-meta"


class TestNamespaceTeamPayloadRoundTrip:
    """Story 17.8 — picker decoupling does NOT mutate stored team payload data."""

    def test_team_payload_name_preserved_on_disk(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """``team.payload["name"]`` round-trips byte-identically through the picker.

        Create a team with ``payload["name"]="Operations"``; list namespaces
        (assert the row's ``name`` equals the namespace identifier — NOT
        ``"Operations"``); then ``Catalog.get(ns, "team")`` and assert
        ``team.payload["name"] == "Operations"`` unchanged. This pins that
        the picker decoupling does not mutate stored data.
        """
        client, catalog = api_client
        team_payload = _team_payload()
        team_payload["name"] = "Operations"
        catalog.create(
            Entry(
                id="team",
                kind="team",
                namespace="ns-roundtrip",
                model_type=_TEAM_TYPE,
                description="",
                payload=team_payload,
            )
        )
        # Picker: the row's ``name`` is the namespace identifier — not
        # ``"Operations"`` — confirming the team payload is bypassed.
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        by_ns = {r["namespace"]: r for r in rows}
        assert by_ns["ns-roundtrip"]["name"] == "ns-roundtrip"
        # On-disk: the team payload's ``name`` is preserved byte-identically.
        stored = catalog.get("ns-roundtrip", "team")
        assert isinstance(stored.payload, dict)
        assert stored.payload["name"] == "Operations"


class TestListNamespacesNoExtraRoundtrip:
    """Story 17.7 / AC20 — counting-spy regression guard against per-row catalog.get round-trips."""

    def test_at_most_two_repository_listings_independent_of_n(
        self, counting_catalog: tuple[Catalog, Any]
    ) -> None:
        """For N namespaces, exactly 2 repository ``list`` calls fire — not 2 + N.

        Wraps the underlying repository with ``CountingEntryRepository`` and
        seeds N=3 namespaces (matching AC20's "N >= 3" minimum so a per-row
        round-trip would produce >= 5 calls vs the asserted 2).
        """
        from akgentic.catalog.api._errors import add_exception_handlers
        from akgentic.catalog.api._settings import CatalogRouterSettings
        from akgentic.catalog.api.router import build_router, set_catalog

        catalog, counting = counting_catalog

        # Seed three namespaces — each with a team to satisfy bootstrap.
        for ns_name in ("ns-1", "ns-2", "ns-3"):
            _seed_team(catalog, ns_name)
        # Add a meta to ns-2 to broaden coverage of the union path.
        _seed_meta(catalog, "ns-2", name="N2", shareable=True)

        # Reset call counts AFTER seeding (we only care about counts during
        # the route handler dispatch).
        counting.reset()

        pytest.importorskip("fastapi")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI(title="counting")
        app.include_router(build_router(CatalogRouterSettings(expose_generic_kind_crud=True)))
        set_catalog(catalog)
        add_exception_handlers(app)
        client = TestClient(app)

        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        # All three namespaces surface (regression guard for union widening).
        assert sorted(r["namespace"] for r in rows) == ["ns-1", "ns-2", "ns-3"]

        # AC20 — exactly two ``list`` calls hit the repository: one for
        # kind="team", one for kind="meta". A per-row ``catalog.get(ns,
        # "_meta")`` round-trip would produce 2 + N (= 5) repository calls
        # under the previous shape; we explicitly assert 2.
        list_calls = [c for c in counting.calls if c[0] == "list"]
        assert counting.count("list") == 2, (
            f"expected exactly 2 list() calls, got {counting.count('list')}: {list_calls}"
        )
        # Verify the two queries are kind="team" and kind="meta".
        kinds = sorted(c[1][0].kind for c in list_calls)
        assert kinds == ["meta", "team"]

        # No per-row ``catalog.get(ns, "_meta")`` round-trip during the
        # handler — this is the regression we are guarding against.
        meta_gets = [
            c for c in counting.calls if c[0] == "get" and len(c[1]) == 2 and c[1][1] == "_meta"
        ]
        assert meta_gets == [], (
            f"expected 0 catalog.get(*, _meta) calls during handler dispatch, got {meta_gets}"
        )


# --- Story 18.2 — NamespaceSummary.public projection ----------------------------


class TestNamespaceSummaryPublicField:
    """Story 18.2 AC2 — ``NamespaceSummary.public`` projection from ``_meta`` payload."""

    def test_namespace_summary_field_order(self) -> None:
        # AC2 — six pinned fields in declaration order. The lockdown catches
        # accidental reorders that would shift the OpenAPI / wire-format
        # key order downstream.
        from akgentic.catalog.api.router import NamespaceSummary

        assert list(NamespaceSummary.model_fields.keys()) == [
            "namespace",
            "name",
            "description",
            "team",
            "shareable",
            "public",
        ]

    def test_namespace_summary_has_public_field_when_meta_public_true(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-public-true")
        catalog._repository.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace="ns-public-true",
                user_id="anonymous",
                model_type=_NAMESPACE_META_TYPE_ROUTER,
                description="",
                payload={
                    "name": "Forkable Library",
                    "description": "",
                    "properties": {},
                    "shareable": False,
                    "public": True,
                },
            )
        )
        response = client.get("/catalog/namespaces")
        assert response.status_code == 200
        rows = response.json()
        by_ns = {r["namespace"]: r for r in rows}
        assert by_ns["ns-public-true"]["public"] is True
        assert by_ns["ns-public-true"]["shareable"] is False

    def test_namespace_summary_public_strict_bool(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        # AC2 — truthy strings do NOT flip the flag; the projection uses
        # ``is True`` (strict-bool comparison), mirroring ``shareable``.
        client, catalog = api_client
        _seed_team(catalog, "ns-public-string")
        catalog._repository.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace="ns-public-string",
                user_id="anonymous",
                model_type=_NAMESPACE_META_TYPE_ROUTER,
                description="",
                payload={
                    "name": "Confused Public",
                    "description": "",
                    "properties": {},
                    "shareable": False,
                    "public": "yes",  # NOT a real bool — defensive projection.
                },
            )
        )
        response = client.get("/catalog/namespaces")
        rows = response.json()
        by_ns = {r["namespace"]: r for r in rows}
        assert by_ns["ns-public-string"]["public"] is False

    def test_namespace_summary_public_default_false_when_no_meta(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, catalog = api_client
        _seed_team(catalog, "ns-no-meta")
        response = client.get("/catalog/namespaces")
        rows = response.json()
        by_ns = {r["namespace"]: r for r in rows}
        assert by_ns["ns-no-meta"]["public"] is False

    def test_namespace_summary_public_false_when_meta_payload_omits_key(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        # A meta entry whose payload omits the ``public`` key projects to
        # ``public=False`` — no AttributeError, no truthy fallback.
        client, catalog = api_client
        _seed_team(catalog, "ns-meta-no-public-key")
        catalog._repository.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace="ns-meta-no-public-key",
                user_id="anonymous",
                model_type=_NAMESPACE_META_TYPE_ROUTER,
                description="",
                payload={"name": "Pre-18.2 meta", "description": "", "properties": {}},
            )
        )
        response = client.get("/catalog/namespaces")
        rows = response.json()
        by_ns = {r["namespace"]: r for r in rows}
        assert by_ns["ns-meta-no-public-key"]["public"] is False


class TestPutNamespaceMetaPublicField:
    """Story 18.2 AC6 — PUT/GET /catalog/namespace/{ns}/meta round-trips ``public``."""

    def test_put_public_true_round_trips(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        body = {"name": "Tenant 42", "description": "", "properties": {}, "public": True}
        response = client.put("/catalog/namespace/tenant-42/meta", json=body)
        assert response.status_code == 201
        entry = response.json()
        assert entry["payload"]["public"] is True
        # GET surfaces the same typed bool.
        get_resp = client.get("/catalog/namespace/tenant-42/meta")
        assert get_resp.status_code == 200
        assert get_resp.json()["payload"]["public"] is True

    def test_put_public_false_round_trips(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        body = {"name": "Tenant 42", "description": "", "properties": {}, "public": False}
        response = client.put("/catalog/namespace/tenant-42/meta", json=body)
        assert response.status_code == 201
        entry = response.json()
        assert entry["payload"]["public"] is False

    def test_put_public_invalid_type_yields_422(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        # Story 18.2 — the route's body parser runs ``model_validate``
        # (non-strict) so ``"yes"`` coerces to True (matching ``shareable``'s
        # established route-boundary behaviour — see story Open Question #6:
        # "match shareable exactly: lenient projection, strict upsert"). A
        # value Pydantic cannot coerce (e.g. a list / object) DOES surface
        # as 422, locking the validation envelope at the route boundary.
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        body = {"name": "Tenant 42", "description": "", "properties": {}, "public": [1, 2, 3]}
        response = client.put("/catalog/namespace/tenant-42/meta", json=body)
        assert response.status_code == 422

    def test_put_public_string_coerces_at_route_strict_at_projection(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        # Story 18.2 — the route accepts a coercible string (``"true"``) and
        # the upsert path stores ``payload["public"] = True`` (Pydantic's
        # default-mode coercion). The strict-bool guarantee — only a real
        # bool ``True`` flips the picker / bundle-header projections —
        # lives at the read side (``NamespaceSummary._build_namespace_summary``,
        # ``BundleHeader._project_header``). This test pins both sides:
        #   1. The route accepts the body (no 422) — matches ``shareable``.
        #   2. ``NamespaceMeta.model_validate({...}, strict=True)`` on the
        #      same body raises ``ValidationError`` — strict mode is
        #      available at the model level for callers who opt in.
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        body = {"name": "Tenant 42", "description": "", "properties": {}, "public": "true"}
        response = client.put("/catalog/namespace/tenant-42/meta", json=body)
        assert response.status_code == 201
        # Strict mode at the model level rejects the same body — the
        # contract is available, just not auto-applied at the route.
        from pydantic import ValidationError as _ValidationError

        with pytest.raises(_ValidationError):
            NamespaceMeta.model_validate(body, strict=True)

    def test_put_default_public_is_false(self, api_client: tuple[TestClient, Catalog]) -> None:
        # A body that omits ``public`` defaults to False on the upserted
        # entry — the field default flows through NamespaceMeta.
        client, catalog = api_client
        _seed_team(catalog, "tenant-42")
        body = {"name": "Tenant 42", "description": "", "properties": {}}
        response = client.put("/catalog/namespace/tenant-42/meta", json=body)
        assert response.status_code == 201
        entry = response.json()
        assert entry["payload"]["public"] is False
