"""Tests for create_app() factory function and error handling integration."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.catalog.api.app import create_app
from akgentic.catalog.models.team import TeamMemberSpec
from akgentic.catalog.repositories.mongo import MongoCatalogConfig
from tests.conftest import make_agent, make_team, make_template


@pytest.fixture(autouse=True)
def _cleanup_catalog_state() -> Generator[None, None, None]:
    """Reset module-level catalog state after each test to prevent pollution."""
    yield
    from akgentic.catalog.api.agent_router import set_catalog as set_agent_cat
    from akgentic.catalog.api.team_router import set_catalog as set_team_cat
    from akgentic.catalog.api.template_router import set_catalog as set_template_cat
    from akgentic.catalog.api.tool_router import set_catalog as set_tool_cat

    set_template_cat(None)  # type: ignore[arg-type]
    set_tool_cat(None)  # type: ignore[arg-type]
    set_agent_cat(None)  # type: ignore[arg-type]
    set_team_cat(None)  # type: ignore[arg-type]


class TestCreateAppYaml:
    """AC4: App assembly with YAML backend."""

    def test_creates_working_app(self, tmp_path: Path) -> None:
        """create_app with yaml backend returns a FastAPI app with 4 routers."""
        app = create_app(backend="yaml", yaml_base_path=tmp_path)
        assert isinstance(app, FastAPI)
        assert app.title == "Akgentic Org API"
        # Should have 4 routers registered (templates, tools, agents, teams)
        route_paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/templates" in route_paths
        assert "/api/tools" in route_paths
        assert "/api/agents" in route_paths
        assert "/api/teams" in route_paths

    def test_crud_round_trip(self, tmp_path: Path) -> None:
        """Create a TemplateEntry via POST, retrieve via GET, verify round-trip."""
        app = create_app(backend="yaml", yaml_base_path=tmp_path)
        client = TestClient(app)

        entry = make_template(id="tpl-rt", template="Hello {name}")
        resp = client.post("/api/templates/", json=entry.model_dump())
        assert resp.status_code == 201
        assert resp.json()["id"] == "tpl-rt"

        resp = client.get("/api/templates/tpl-rt")
        assert resp.status_code == 200
        assert resp.json()["id"] == "tpl-rt"
        assert resp.json()["template"] == "Hello {name}"

    def test_raises_without_yaml_base_path(self) -> None:
        """create_app with yaml backend but no base path raises ValueError."""
        with pytest.raises(ValueError, match="yaml_base_path"):
            create_app(backend="yaml")

    def test_creates_subdirectories(self, tmp_path: Path) -> None:
        """create_app creates templates/tools/agents/teams subdirs if absent."""
        create_app(backend="yaml", yaml_base_path=tmp_path)
        for name in ("templates", "tools", "agents", "teams"):
            assert (tmp_path / name).is_dir()

    def test_raises_on_invalid_backend(self) -> None:
        """create_app with unknown backend raises ValueError."""
        with pytest.raises(ValueError, match="Unknown backend"):
            create_app(backend="redis")  # type: ignore[arg-type]


class TestCreateAppMongodb:
    """AC4: App assembly with MongoDB backend."""

    @pytest.fixture()
    def mongo_config(self) -> MongoCatalogConfig:
        """MongoCatalogConfig for testing."""
        return MongoCatalogConfig(
            connection_string="mongodb://localhost:27017", database="test_catalog"
        )

    def test_creates_working_app(self, mongo_config: MongoCatalogConfig) -> None:
        """create_app with mongodb backend returns a working FastAPI app."""
        with patch(
            "akgentic.catalog.repositories.mongo._config.MongoCatalogConfig.create_client",
            return_value=mongomock.MongoClient(),
        ):
            app = create_app(backend="mongodb", mongo_config=mongo_config)
        assert isinstance(app, FastAPI)
        assert app.title == "Akgentic Org API"

    def test_crud_round_trip(self, mongo_config: MongoCatalogConfig) -> None:
        """Create + get round-trip through MongoDB-backed app."""
        with patch(
            "akgentic.catalog.repositories.mongo._config.MongoCatalogConfig.create_client",
            return_value=mongomock.MongoClient(),
        ):
            app = create_app(backend="mongodb", mongo_config=mongo_config)
        client = TestClient(app)

        entry = make_template(id="tpl-mongo", template="Mongo {test}")
        resp = client.post("/api/templates/", json=entry.model_dump())
        assert resp.status_code == 201

        resp = client.get("/api/templates/tpl-mongo")
        assert resp.status_code == 200
        assert resp.json()["id"] == "tpl-mongo"

    def test_raises_without_mongo_config(self) -> None:
        """create_app with mongodb backend but no config raises ValueError."""
        with pytest.raises(ValueError, match="mongo_config"):
            create_app(backend="mongodb")


class TestAppErrorHandling:
    """AC1-3: Structured error responses through assembled app."""

    def test_duplicate_create_returns_409(self, tmp_path: Path) -> None:
        """CatalogValidationError on duplicate → 409 with ErrorResponse body."""
        app = create_app(backend="yaml", yaml_base_path=tmp_path)
        client = TestClient(app)

        entry = make_template(id="tpl-409")
        client.post("/api/templates/", json=entry.model_dump())
        resp = client.post("/api/templates/", json=entry.model_dump())
        assert resp.status_code == 409
        body = resp.json()
        assert "detail" in body
        assert "errors" in body
        assert isinstance(body["errors"], list)
        assert len(body["errors"]) > 0

    def test_delete_referenced_entry_returns_409(self, tmp_path: Path) -> None:
        """CatalogValidationError on delete of referenced entry → 409."""
        app = create_app(backend="yaml", yaml_base_path=tmp_path)
        client = TestClient(app)

        # Create template with single placeholder, then agent referencing it via @-ref
        tpl = make_template(id="tpl-ref", template="You are {role}.")
        resp = client.post("/api/templates/", json=tpl.model_dump())
        assert resp.status_code == 201

        agent = make_agent(id="agent-ref", template_ref="@tpl-ref", params={"role": "test"})
        resp = client.post("/api/agents/", json=agent.model_dump())
        assert resp.status_code == 201

        # Try to delete the referenced template
        resp = client.delete("/api/templates/tpl-ref")
        assert resp.status_code == 409
        body = resp.json()
        assert "detail" in body

    def test_get_nonexistent_returns_404(self, tmp_path: Path) -> None:
        """EntryNotFoundError on GET → 404 with ErrorResponse body."""
        app = create_app(backend="yaml", yaml_base_path=tmp_path)
        client = TestClient(app)

        resp = client.get("/api/templates/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body
        assert "errors" in body
        assert body["errors"] == []

    def test_update_nonexistent_returns_404(self, tmp_path: Path) -> None:
        """EntryNotFoundError on PUT → 404 with ErrorResponse body."""
        app = create_app(backend="yaml", yaml_base_path=tmp_path)
        client = TestClient(app)

        entry = make_template(id="nonexistent")
        resp = client.put("/api/templates/nonexistent", json=entry.model_dump())
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, tmp_path: Path) -> None:
        """EntryNotFoundError on DELETE → 404 with ErrorResponse body."""
        app = create_app(backend="yaml", yaml_base_path=tmp_path)
        client = TestClient(app)

        resp = client.delete("/api/templates/nonexistent")
        assert resp.status_code == 404

    def test_delete_agent_referenced_by_team_returns_409(self, tmp_path: Path) -> None:
        """CatalogValidationError on delete of agent referenced by team → 409."""
        app = create_app(backend="yaml", yaml_base_path=tmp_path)
        client = TestClient(app)

        agent = make_agent(id="agent-prot")
        resp = client.post("/api/agents/", json=agent.model_dump())
        assert resp.status_code == 201

        team = make_team(
            id="team-prot",
            entry_point="agent-prot",
            members=[TeamMemberSpec(agent_id="agent-prot")],
        )
        resp = client.post("/api/teams/", json=team.model_dump())
        assert resp.status_code == 201

        resp = client.delete("/api/agents/agent-prot")
        assert resp.status_code == 409
        body = resp.json()
        assert "detail" in body
        assert "errors" in body
        assert len(body["errors"]) > 0

    def test_invalid_payload_returns_422(self, tmp_path: Path) -> None:
        """Pydantic ValidationError → 422 with structured detail array."""
        app = create_app(backend="yaml", yaml_base_path=tmp_path)
        client = TestClient(app)

        resp = client.post("/api/templates/", json={"bad_field": "val"})
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body
        assert isinstance(body["detail"], list)
        assert len(body["detail"]) > 0
        item = body["detail"][0]
        assert "loc" in item
        assert "msg" in item
        assert "type" in item
