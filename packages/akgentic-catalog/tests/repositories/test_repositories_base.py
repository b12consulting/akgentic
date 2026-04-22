"""Tests for abstract repository interfaces."""

from __future__ import annotations

import builtins
import typing

import pytest

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.queries import AgentQuery, TeamQuery, TemplateQuery, ToolQuery
from akgentic.catalog.models.team import TeamEntry
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.models.tool import ToolEntry
from akgentic.catalog.repositories.base import (
    AgentCatalogRepository,
    TeamCatalogRepository,
    TemplateCatalogRepository,
    ToolCatalogRepository,
)

# --- Concrete test implementations ---


class ConcreteTemplateRepo(TemplateCatalogRepository):
    def create(self, template_entry: TemplateEntry) -> str:
        return template_entry.id

    def get(self, id: str) -> TemplateEntry | None:
        return None

    def list(self) -> list[TemplateEntry]:
        return []

    def search(self, query: TemplateQuery) -> list[TemplateEntry]:
        return []

    def update(self, id: str, template_entry: TemplateEntry) -> None:
        return None

    def delete(self, id: str) -> None:
        return None


class ConcreteToolRepo(ToolCatalogRepository):
    def create(self, tool_entry: ToolEntry) -> str:
        return tool_entry.id

    def get(self, id: str) -> ToolEntry | None:
        return None

    def list(self) -> list[ToolEntry]:
        return []

    def search(self, query: ToolQuery) -> list[ToolEntry]:
        return []

    def update(self, id: str, tool_entry: ToolEntry) -> None:
        return None

    def delete(self, id: str) -> None:
        return None


class ConcreteAgentRepo(AgentCatalogRepository):
    def create(self, agent_entry: AgentEntry) -> str:
        return agent_entry.id

    def get(self, id: str) -> AgentEntry | None:
        return None

    def list(self) -> list[AgentEntry]:
        return []

    def search(self, query: AgentQuery) -> list[AgentEntry]:
        return []

    def update(self, id: str, agent_entry: AgentEntry) -> None:
        return None

    def delete(self, id: str) -> None:
        return None


class ConcreteTeamRepo(TeamCatalogRepository):
    def create(self, team_entry: TeamEntry) -> str:
        return team_entry.id

    def get(self, id: str) -> TeamEntry | None:
        return None

    def list(self) -> list[TeamEntry]:
        return []

    def search(self, query: TeamQuery) -> list[TeamEntry]:
        return []

    def update(self, id: str, team_entry: TeamEntry) -> None:
        return None

    def delete(self, id: str) -> None:
        return None


# --- Partial implementations for negative tests ---


class PartialTemplateRepo(TemplateCatalogRepository):  # type: ignore[abstract]
    def create(self, template_entry: TemplateEntry) -> str:
        return template_entry.id


class PartialToolRepo(ToolCatalogRepository):  # type: ignore[abstract]
    def create(self, tool_entry: ToolEntry) -> str:
        return tool_entry.id


class PartialAgentRepo(AgentCatalogRepository):  # type: ignore[abstract]
    def create(self, agent_entry: AgentEntry) -> str:
        return agent_entry.id


class PartialTeamRepo(TeamCatalogRepository):  # type: ignore[abstract]
    def create(self, team_entry: TeamEntry) -> str:
        return team_entry.id


# --- Tests ---


class TestTemplateCatalogRepository:
    """Tests for TemplateCatalogRepository ABC."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            TemplateCatalogRepository()  # type: ignore[abstract]

    def test_concrete_subclass_can_instantiate(self) -> None:
        repo = ConcreteTemplateRepo()
        assert isinstance(repo, TemplateCatalogRepository)

    def test_partial_subclass_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            PartialTemplateRepo()

    def test_method_signatures(self) -> None:
        hints = typing.get_type_hints(TemplateCatalogRepository.create)
        assert hints["return"] is str

        hints = typing.get_type_hints(TemplateCatalogRepository.get)
        assert hints["return"] == TemplateEntry | None

        hints = typing.get_type_hints(TemplateCatalogRepository.list)
        assert hints["return"] == builtins.list[TemplateEntry]

        hints = typing.get_type_hints(TemplateCatalogRepository.search)
        assert hints["return"] == builtins.list[TemplateEntry]

        hints = typing.get_type_hints(TemplateCatalogRepository.update)
        assert hints["return"] is type(None)

        hints = typing.get_type_hints(TemplateCatalogRepository.delete)
        assert hints["return"] is type(None)


class TestToolCatalogRepository:
    """Tests for ToolCatalogRepository ABC."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            ToolCatalogRepository()  # type: ignore[abstract]

    def test_concrete_subclass_can_instantiate(self) -> None:
        repo = ConcreteToolRepo()
        assert isinstance(repo, ToolCatalogRepository)

    def test_partial_subclass_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            PartialToolRepo()


class TestAgentCatalogRepository:
    """Tests for AgentCatalogRepository ABC."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            AgentCatalogRepository()  # type: ignore[abstract]

    def test_concrete_subclass_can_instantiate(self) -> None:
        repo = ConcreteAgentRepo()
        assert isinstance(repo, AgentCatalogRepository)

    def test_partial_subclass_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            PartialAgentRepo()


class TestTeamCatalogRepository:
    """Tests for TeamCatalogRepository ABC."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            TeamCatalogRepository()  # type: ignore[abstract]

    def test_concrete_subclass_can_instantiate(self) -> None:
        repo = ConcreteTeamRepo()
        assert isinstance(repo, TeamCatalogRepository)

    def test_partial_subclass_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            PartialTeamRepo()


class TestMethodSignatures:
    """Test that method signatures match expected types across all repositories."""

    def _hints(self, cls: type, method: str) -> dict[str, type]:
        return typing.get_type_hints(getattr(cls, method))

    def test_tool_repo_signatures(self) -> None:
        assert self._hints(ToolCatalogRepository, "create")["return"] is str
        assert self._hints(ToolCatalogRepository, "get")["return"] == ToolEntry | None
        assert self._hints(ToolCatalogRepository, "list")["return"] == builtins.list[ToolEntry]
        assert self._hints(ToolCatalogRepository, "search")["return"] == builtins.list[ToolEntry]
        assert self._hints(ToolCatalogRepository, "update")["return"] is type(None)
        assert self._hints(ToolCatalogRepository, "delete")["return"] is type(None)

    def test_agent_repo_signatures(self) -> None:
        assert self._hints(AgentCatalogRepository, "create")["return"] is str
        assert self._hints(AgentCatalogRepository, "get")["return"] == AgentEntry | None
        assert self._hints(AgentCatalogRepository, "list")["return"] == builtins.list[AgentEntry]
        assert self._hints(AgentCatalogRepository, "search")["return"] == builtins.list[AgentEntry]
        assert self._hints(AgentCatalogRepository, "update")["return"] is type(None)
        assert self._hints(AgentCatalogRepository, "delete")["return"] is type(None)

    def test_team_repo_signatures(self) -> None:
        assert self._hints(TeamCatalogRepository, "create")["return"] is str
        assert self._hints(TeamCatalogRepository, "get")["return"] == TeamEntry | None
        assert self._hints(TeamCatalogRepository, "list")["return"] == builtins.list[TeamEntry]
        assert self._hints(TeamCatalogRepository, "search")["return"] == builtins.list[TeamEntry]
        assert self._hints(TeamCatalogRepository, "update")["return"] is type(None)
        assert self._hints(TeamCatalogRepository, "delete")["return"] is type(None)


class TestPublicAPIExports:
    """Test that all query models and repository ABCs are importable from akgentic.catalog."""

    def test_query_models_importable_from_catalog(self) -> None:
        from akgentic.catalog import AgentQuery, TeamQuery, TemplateQuery, ToolQuery

        assert AgentQuery is not None
        assert TeamQuery is not None
        assert TemplateQuery is not None
        assert ToolQuery is not None

    def test_repository_abcs_importable_from_catalog(self) -> None:
        from akgentic.catalog import (
            AgentCatalogRepository,
            TeamCatalogRepository,
            TemplateCatalogRepository,
            ToolCatalogRepository,
        )

        assert AgentCatalogRepository is not None
        assert TeamCatalogRepository is not None
        assert TemplateCatalogRepository is not None
        assert ToolCatalogRepository is not None

    def test_query_models_in_catalog_all(self) -> None:
        import akgentic.catalog

        for name in ["TemplateQuery", "ToolQuery", "AgentQuery", "TeamQuery"]:
            assert name in akgentic.catalog.__all__

    def test_repository_abcs_in_catalog_all(self) -> None:
        import akgentic.catalog

        for name in [
            "TemplateCatalogRepository",
            "ToolCatalogRepository",
            "AgentCatalogRepository",
            "TeamCatalogRepository",
        ]:
            assert name in akgentic.catalog.__all__
