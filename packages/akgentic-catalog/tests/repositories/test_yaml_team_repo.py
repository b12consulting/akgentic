"""Tests for YamlTeamCatalogRepository."""

from pathlib import Path

import pytest

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import TeamQuery
from akgentic.catalog.models.team import TeamEntry
from akgentic.catalog.repositories.yaml.team_repo import YamlTeamCatalogRepository
from tests.repositories.conftest import write_yaml


def _team_dict(
    id: str,
    *,
    name: str = "Test Team",
    description: str = "A test team",
    entry_point: str = "test-manager",
    members: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if members is None:
        members = [{"agent_id": "test-manager", "headcount": 1}]
    return {
        "id": id,
        "name": name,
        "description": description,
        "entry_point": entry_point,
        "message_types": ["akgentic.agent.AgentMessage"],
        "members": members,
        "agent_profiles": [],
    }


# ---- Loading (AC #2) ----


def test_load_team_entry_from_yaml(tmp_path: Path) -> None:
    """AC #2: Load TeamEntry from YAML with recursive TeamMemberSpec trees."""
    members = [
        {
            "agent_id": "eng-manager",
            "headcount": 1,
            "members": [
                {"agent_id": "eng-assistant", "headcount": 2},
            ],
        },
    ]
    write_yaml(
        tmp_path / "teams.yaml",
        [_team_dict("eng-team", name="Engineering", members=members)],
    )
    repo = YamlTeamCatalogRepository(tmp_path)
    entries = repo.list()
    assert len(entries) == 1
    assert isinstance(entries[0], TeamEntry)
    assert entries[0].members[0].agent_id == "eng-manager"
    assert entries[0].members[0].members[0].agent_id == "eng-assistant"


# ---- CRUD (AC #2) ----


def test_create_persists_team(tmp_path: Path) -> None:
    """AC #2: create() persists team entry to YAML file."""
    repo = YamlTeamCatalogRepository(tmp_path)
    data = _team_dict("new-team", name="New Team")
    entry = TeamEntry.model_validate(data)
    result_id = repo.create(entry)
    assert result_id == "new-team"

    repo2 = YamlTeamCatalogRepository(tmp_path)
    loaded = repo2.get("new-team")
    assert loaded is not None
    assert loaded.name == "New Team"


def test_create_duplicate_id_raises(tmp_path: Path) -> None:
    """create() raises CatalogValidationError for duplicate id."""
    write_yaml(tmp_path / "t.yaml", [_team_dict("dup")])
    repo = YamlTeamCatalogRepository(tmp_path)
    entry = TeamEntry.model_validate(_team_dict("dup", name="Another"))
    with pytest.raises(CatalogValidationError):
        repo.create(entry)


def test_get_returns_team_by_id(tmp_path: Path) -> None:
    """get() returns team by id."""
    write_yaml(tmp_path / "t.yaml", [_team_dict("t1")])
    repo = YamlTeamCatalogRepository(tmp_path)
    entry = repo.get("t1")
    assert entry is not None
    assert entry.id == "t1"


def test_get_returns_none_for_missing(tmp_path: Path) -> None:
    """get() returns None for non-existent id."""
    write_yaml(tmp_path / "t.yaml", [_team_dict("t1")])
    repo = YamlTeamCatalogRepository(tmp_path)
    assert repo.get("missing") is None


def test_list_returns_all_entries(tmp_path: Path) -> None:
    """list() returns all cached entries."""
    write_yaml(
        tmp_path / "t.yaml",
        [_team_dict("t1"), _team_dict("t2", name="Second Team")],
    )
    repo = YamlTeamCatalogRepository(tmp_path)
    assert len(repo.list()) == 2


def test_update_modifies_team(tmp_path: Path) -> None:
    """update() modifies team entry in file."""
    write_yaml(tmp_path / "t.yaml", [_team_dict("t1", name="Old Name")])
    repo = YamlTeamCatalogRepository(tmp_path)
    updated = TeamEntry.model_validate(_team_dict("t1", name="New Name"))
    repo.update("t1", updated)
    entry = repo.get("t1")
    assert entry is not None
    assert entry.name == "New Name"


def test_update_raises_for_missing_id(tmp_path: Path) -> None:
    """update() raises EntryNotFoundError for missing id."""
    write_yaml(tmp_path / "t.yaml", [_team_dict("t1")])
    repo = YamlTeamCatalogRepository(tmp_path)
    entry = TeamEntry.model_validate(_team_dict("nope"))
    with pytest.raises(EntryNotFoundError):
        repo.update("nope", entry)


def test_delete_removes_team(tmp_path: Path) -> None:
    """delete() removes team entry and deletes file if empty."""
    write_yaml(tmp_path / "t1.yaml", [_team_dict("t1")])
    repo = YamlTeamCatalogRepository(tmp_path)
    repo.delete("t1")
    assert repo.get("t1") is None
    assert not (tmp_path / "t1.yaml").exists()


def test_delete_raises_for_missing_id(tmp_path: Path) -> None:
    """delete() raises EntryNotFoundError for missing id."""
    write_yaml(tmp_path / "t.yaml", [_team_dict("t1")])
    repo = YamlTeamCatalogRepository(tmp_path)
    with pytest.raises(EntryNotFoundError):
        repo.delete("nonexistent")


def test_duplicate_id_across_files_raises(tmp_path: Path) -> None:
    """Duplicate id across files raises CatalogValidationError."""
    write_yaml(tmp_path / "file_a.yaml", [_team_dict("dup")])
    write_yaml(tmp_path / "file_b.yaml", [_team_dict("dup", name="Another")])
    repo = YamlTeamCatalogRepository(tmp_path)
    with pytest.raises(CatalogValidationError) as exc_info:
        repo.list()
    assert "dup" in str(exc_info.value)
    assert "file_a.yaml" in str(exc_info.value)
    assert "file_b.yaml" in str(exc_info.value)


# ---- search() (AC #4) ----


def test_search_by_id(tmp_path: Path) -> None:
    """search(TeamQuery(id='t1')) exact id match."""
    write_yaml(
        tmp_path / "teams.yaml",
        [_team_dict("t1", name="Alpha"), _team_dict("t2", name="Beta")],
    )
    repo = YamlTeamCatalogRepository(tmp_path)
    results = repo.search(TeamQuery(id="t1"))
    assert len(results) == 1
    assert results[0].id == "t1"


def test_search_by_id_no_match(tmp_path: Path) -> None:
    """search(TeamQuery(id='missing')) returns empty."""
    write_yaml(tmp_path / "teams.yaml", [_team_dict("t1")])
    repo = YamlTeamCatalogRepository(tmp_path)
    assert repo.search(TeamQuery(id="missing")) == []


def test_search_by_name(tmp_path: Path) -> None:
    """AC #4: search(TeamQuery(name='engineering')) case-insensitive substring."""
    write_yaml(
        tmp_path / "teams.yaml",
        [
            _team_dict("eng", name="Engineering Team"),
            _team_dict("mkt", name="Marketing Team"),
        ],
    )
    repo = YamlTeamCatalogRepository(tmp_path)
    results = repo.search(TeamQuery(name="engineering"))
    assert len(results) == 1
    assert results[0].id == "eng"


def test_search_by_agent_id_root_member(tmp_path: Path) -> None:
    """AC #4: search(TeamQuery(agent_id='eng-manager')) finds team where agent is root member."""
    members = [{"agent_id": "eng-manager", "headcount": 1}]
    write_yaml(
        tmp_path / "teams.yaml",
        [_team_dict("eng", name="Engineering", members=members)],
    )
    repo = YamlTeamCatalogRepository(tmp_path)
    results = repo.search(TeamQuery(agent_id="eng-manager"))
    assert len(results) == 1
    assert results[0].id == "eng"


def test_search_by_agent_id_nested_child(tmp_path: Path) -> None:
    """AC #4: search(TeamQuery(agent_id='eng-assistant')) finds team where agent is nested."""
    members = [
        {
            "agent_id": "eng-manager",
            "headcount": 1,
            "members": [
                {"agent_id": "eng-assistant", "headcount": 2},
            ],
        },
    ]
    write_yaml(
        tmp_path / "teams.yaml",
        [_team_dict("eng", name="Engineering", members=members)],
    )
    repo = YamlTeamCatalogRepository(tmp_path)
    results = repo.search(TeamQuery(agent_id="eng-assistant"))
    assert len(results) == 1
    assert results[0].id == "eng"


def test_search_by_agent_id_not_found(tmp_path: Path) -> None:
    """search(TeamQuery(agent_id='nonexistent')) returns empty."""
    write_yaml(
        tmp_path / "teams.yaml",
        [_team_dict("eng", name="Engineering")],
    )
    repo = YamlTeamCatalogRepository(tmp_path)
    results = repo.search(TeamQuery(agent_id="nonexistent"))
    assert results == []


def test_search_and_semantics(tmp_path: Path) -> None:
    """search(TeamQuery(name='eng', agent_id='eng-manager')) AND semantics."""
    members_eng = [{"agent_id": "eng-manager", "headcount": 1}]
    members_mkt = [{"agent_id": "eng-manager", "headcount": 1}]
    write_yaml(
        tmp_path / "teams.yaml",
        [
            _team_dict("eng", name="Engineering Team", members=members_eng),
            _team_dict("mkt", name="Marketing Team", members=members_mkt),
        ],
    )
    repo = YamlTeamCatalogRepository(tmp_path)
    # Both teams have eng-manager, but only one matches name "eng"
    results = repo.search(TeamQuery(name="eng", agent_id="eng-manager"))
    assert len(results) == 1
    assert results[0].id == "eng"


def test_search_by_description(tmp_path: Path) -> None:
    """search(TeamQuery(description='software')) case-insensitive substring."""
    write_yaml(
        tmp_path / "teams.yaml",
        [
            _team_dict("eng", description="Software engineering team"),
            _team_dict("mkt", description="Digital marketing team"),
        ],
    )
    repo = YamlTeamCatalogRepository(tmp_path)
    results = repo.search(TeamQuery(description="software"))
    assert len(results) == 1
    assert results[0].id == "eng"


# ---- Caching ----


def test_caching_no_reread(tmp_path: Path) -> None:
    """Second list() call doesn't re-read files."""
    write_yaml(tmp_path / "t.yaml", [_team_dict("t1")])
    repo = YamlTeamCatalogRepository(tmp_path)
    assert len(repo.list()) == 1

    write_yaml(
        tmp_path / "t.yaml",
        [_team_dict("t1"), _team_dict("t2", name="Second")],
    )
    assert len(repo.list()) == 1


def test_reload_forces_rescan(tmp_path: Path) -> None:
    """reload() forces re-scan from disk."""
    write_yaml(tmp_path / "t.yaml", [_team_dict("t1")])
    repo = YamlTeamCatalogRepository(tmp_path)
    assert len(repo.list()) == 1

    write_yaml(
        tmp_path / "t.yaml",
        [_team_dict("t1"), _team_dict("t2", name="Second")],
    )
    repo.reload()
    assert len(repo.list()) == 2


# ---- Public API export ----


def test_public_api_export() -> None:
    """YamlTeamCatalogRepository is importable from public API."""
    from akgentic.catalog import YamlTeamCatalogRepository as Exported

    assert Exported is YamlTeamCatalogRepository
