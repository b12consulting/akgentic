"""Tests for TeamMemberSpec and TeamEntry models."""

import pytest
from pydantic import BaseModel, ValidationError

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.models.team import TeamEntry, TeamMemberSpec
from tests.conftest import make_agent

# --- Mock catalog helper ---


class MockAgentCatalog:
    """Simple catalog mock with .get() returning AgentEntry or None."""

    def __init__(self, entries: dict[str, AgentEntry]) -> None:
        self._entries = entries

    def get(self, agent_id: str) -> AgentEntry | None:
        return self._entries.get(agent_id)


# --- TeamMemberSpec tests ---


class TestTeamMemberSpec:
    """Tests for TeamMemberSpec model (AC #1)."""

    def test_simple_member_with_default_headcount(self) -> None:
        member = TeamMemberSpec(agent_id="engineer")
        assert member.agent_id == "engineer"
        assert member.headcount == 1
        assert member.members == []

    def test_explicit_headcount_greater_than_one(self) -> None:
        member = TeamMemberSpec(agent_id="dev", headcount=3)
        assert member.headcount == 3

    def test_headcount_zero_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            TeamMemberSpec(agent_id="dev", headcount=0)

    def test_negative_headcount_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            TeamMemberSpec(agent_id="dev", headcount=-1)

    def test_nested_members_recursive_tree(self) -> None:
        leaf = TeamMemberSpec(agent_id="junior-dev")
        mid = TeamMemberSpec(agent_id="senior-dev", members=[leaf])
        root = TeamMemberSpec(agent_id="eng-manager", members=[mid])

        assert len(root.members) == 1
        assert root.members[0].agent_id == "senior-dev"
        assert len(root.members[0].members) == 1
        assert root.members[0].members[0].agent_id == "junior-dev"

    def test_empty_agent_id_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            TeamMemberSpec(agent_id="")


# --- TeamEntry tests ---


class TestTeamEntry:
    """Tests for TeamEntry model (AC #2)."""

    def _make_team_entry(self, **overrides: object) -> TeamEntry:
        defaults: dict[str, object] = {
            "id": "engineering",
            "name": "Engineering Team",
            "entry_point": "eng-manager",
            "message_types": ["pydantic.BaseModel"],
            "members": [TeamMemberSpec(agent_id="eng-manager")],
        }
        defaults.update(overrides)
        return TeamEntry(**defaults)  # type: ignore[arg-type]

    def test_valid_team_entry_all_required_fields(self) -> None:
        team = self._make_team_entry()
        assert team.id == "engineering"
        assert team.name == "Engineering Team"
        assert team.entry_point == "eng-manager"
        assert team.message_types == ["pydantic.BaseModel"]
        assert len(team.members) == 1

    def test_defaults_agent_profiles_empty_list(self) -> None:
        team = self._make_team_entry()
        assert team.agent_profiles == []

    def test_defaults_description_empty_string(self) -> None:
        team = self._make_team_entry()
        assert team.description == ""

    def test_empty_id_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            self._make_team_entry(id="")

    def test_empty_name_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            self._make_team_entry(name="")

    def test_empty_entry_point_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            self._make_team_entry(entry_point="")

    def test_empty_message_types_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            self._make_team_entry(message_types=[])

    def test_empty_members_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            self._make_team_entry(members=[])

    def test_deeply_nested_member_tree(self) -> None:
        l3 = TeamMemberSpec(agent_id="intern")
        l2 = TeamMemberSpec(agent_id="junior", members=[l3])
        l1 = TeamMemberSpec(agent_id="senior", members=[l2])
        l0 = TeamMemberSpec(agent_id="manager", members=[l1])

        team = self._make_team_entry(members=[l0])
        assert team.members[0].members[0].members[0].members[0].agent_id == "intern"

    def test_agent_profiles_accepts_strings(self) -> None:
        team = self._make_team_entry(agent_profiles=["extra-agent-1", "extra-agent-2"])
        assert team.agent_profiles == ["extra-agent-1", "extra-agent-2"]

    def test_custom_description(self) -> None:
        team = self._make_team_entry(description="A great team")
        assert team.description == "A great team"


# --- resolve_entry_point tests ---


class TestResolveEntryPoint:
    """Tests for TeamEntry.resolve_entry_point (AC #3)."""

    def test_resolve_entry_point_returns_agent_entry(self) -> None:
        agent = make_agent(id="eng-manager")
        catalog = MockAgentCatalog({"eng-manager": agent})

        team = TeamEntry(
            id="team-1",
            name="Team",
            entry_point="eng-manager",
            message_types=["pydantic.BaseModel"],
            members=[TeamMemberSpec(agent_id="eng-manager")],
        )
        result = team.resolve_entry_point(catalog)
        assert result is agent

    def test_resolve_entry_point_raises_for_missing(self) -> None:
        catalog = MockAgentCatalog({})

        team = TeamEntry(
            id="team-1",
            name="Team",
            entry_point="nonexistent",
            message_types=["pydantic.BaseModel"],
            members=[TeamMemberSpec(agent_id="eng-manager")],
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            team.resolve_entry_point(catalog)
        assert "nonexistent" in exc_info.value.errors[0]


# --- resolve_message_types tests ---


class TestResolveMessageTypes:
    """Tests for TeamEntry.resolve_message_types (AC #4)."""

    def test_resolve_valid_class_path(self) -> None:
        team = TeamEntry(
            id="team-1",
            name="Team",
            entry_point="ep",
            message_types=["pydantic.BaseModel"],
            members=[TeamMemberSpec(agent_id="a")],
        )
        result = team.resolve_message_types()
        assert result == [BaseModel]

    def test_resolve_multiple_valid_paths(self) -> None:
        team = TeamEntry(
            id="team-1",
            name="Team",
            entry_point="ep",
            message_types=["pydantic.BaseModel", "pydantic.ValidationError"],
            members=[TeamMemberSpec(agent_id="a")],
        )
        result = team.resolve_message_types()
        assert len(result) == 2
        assert result[0] is BaseModel
        assert result[1] is ValidationError

    def test_single_invalid_path_raises_catalog_validation_error(self) -> None:
        team = TeamEntry(
            id="team-1",
            name="Team",
            entry_point="ep",
            message_types=["nonexistent.module.FakeClass"],
            members=[TeamMemberSpec(agent_id="a")],
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            team.resolve_message_types()
        assert len(exc_info.value.errors) == 1
        assert "nonexistent.module.FakeClass" in exc_info.value.errors[0]

    def test_collects_all_errors_for_multiple_invalid_paths(self) -> None:
        team = TeamEntry(
            id="team-1",
            name="Team",
            entry_point="ep",
            message_types=["bad.path.One", "bad.path.Two"],
            members=[TeamMemberSpec(agent_id="a")],
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            team.resolve_message_types()
        assert len(exc_info.value.errors) == 2
        assert "bad.path.One" in exc_info.value.errors[0]
        assert "bad.path.Two" in exc_info.value.errors[1]

    def test_dotless_path_raises_catalog_validation_error(self) -> None:
        team = TeamEntry(
            id="team-1",
            name="Team",
            entry_point="ep",
            message_types=["nodotpath"],
            members=[TeamMemberSpec(agent_id="a")],
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            team.resolve_message_types()
        assert len(exc_info.value.errors) == 1
        assert "nodotpath" in exc_info.value.errors[0]

    def test_non_class_importable_raises_catalog_validation_error(self) -> None:
        team = TeamEntry(
            id="team-1",
            name="Team",
            entry_point="ep",
            message_types=["pydantic.Field"],
            members=[TeamMemberSpec(agent_id="a")],
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            team.resolve_message_types()
        assert "not a class" in exc_info.value.errors[0]

    def test_mixed_valid_and_invalid_raises_only_for_invalid(self) -> None:
        team = TeamEntry(
            id="team-1",
            name="Team",
            entry_point="ep",
            message_types=["pydantic.BaseModel", "fake.module.Nope"],
            members=[TeamMemberSpec(agent_id="a")],
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            team.resolve_message_types()
        assert len(exc_info.value.errors) == 1
        assert "fake.module.Nope" in exc_info.value.errors[0]


# --- Public API export tests ---


class TestPublicAPIExports:
    """Tests for public API exports (AC #1, #2)."""

    def test_team_member_spec_importable_from_catalog(self) -> None:
        from akgentic.catalog import TeamMemberSpec as Imported

        assert Imported is TeamMemberSpec

    def test_team_entry_importable_from_catalog(self) -> None:
        from akgentic.catalog import TeamEntry as Imported

        assert Imported is TeamEntry
