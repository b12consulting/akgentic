"""Tests for YamlToolCatalogRepository."""

from pathlib import Path

import pytest

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import ToolQuery
from akgentic.catalog.models.tool import ToolEntry
from akgentic.catalog.repositories.yaml.tool_repo import YamlToolCatalogRepository
from tests.repositories.conftest import write_yaml

SEARCH_TOOL_CLASS = "akgentic.tool.search.SearchTool"


def _tool_dict(
    id: str,
    *,
    name: str = "test-tool",
    description: str = "A test tool",
    tool_class: str = SEARCH_TOOL_CLASS,
) -> dict[str, object]:
    return {
        "id": id,
        "tool_class": tool_class,
        "tool": {"name": name, "description": description},
    }


# ---- Loading ----


def test_load_tool_entry_from_yaml(tmp_path: Path) -> None:
    """AC #1: Load ToolEntry from YAML with tool_class resolution."""
    write_yaml(
        tmp_path / "tools.yaml",
        [_tool_dict("s1", name="search", description="Web search tool")],
    )
    repo = YamlToolCatalogRepository(tmp_path)
    entries = repo.list()
    assert len(entries) == 1
    assert isinstance(entries[0], ToolEntry)
    assert entries[0].tool.name == "search"


def test_duplicate_id_detection(tmp_path: Path) -> None:
    """AC #2: Duplicate id across files raises CatalogValidationError."""
    write_yaml(tmp_path / "a.yaml", [_tool_dict("dup")])
    write_yaml(tmp_path / "b.yaml", [_tool_dict("dup")])
    repo = YamlToolCatalogRepository(tmp_path)
    with pytest.raises(CatalogValidationError) as exc_info:
        repo.list()
    assert "dup" in str(exc_info.value)
    assert "a.yaml" in str(exc_info.value)
    assert "b.yaml" in str(exc_info.value)


# ---- search() ----


def test_search_by_tool_class(tmp_path: Path) -> None:
    """AC #5: search(ToolQuery(tool_class=...)) exact match."""
    write_yaml(
        tmp_path / "tools.yaml",
        [
            _tool_dict("s1", tool_class=SEARCH_TOOL_CLASS),
            _tool_dict(
                "p1",
                tool_class="akgentic.tool.planning.PlanningTool",
                name="planner",
                description="Planning tool",
            ),
        ],
    )
    repo = YamlToolCatalogRepository(tmp_path)
    results = repo.search(ToolQuery(tool_class=SEARCH_TOOL_CLASS))
    assert len(results) == 1
    assert results[0].id == "s1"


def test_search_by_name_substring(tmp_path: Path) -> None:
    """AC #5: search(ToolQuery(name='search')) substring match on tool.name."""
    write_yaml(
        tmp_path / "tools.yaml",
        [
            _tool_dict("s1", name="web-search"),
            _tool_dict("s2", name="planner"),
        ],
    )
    repo = YamlToolCatalogRepository(tmp_path)
    results = repo.search(ToolQuery(name="search"))
    assert len(results) == 1
    assert results[0].id == "s1"


def test_search_by_name_case_insensitive(tmp_path: Path) -> None:
    """Name search is case-insensitive."""
    write_yaml(tmp_path / "tools.yaml", [_tool_dict("s1", name="WebSearch")])
    repo = YamlToolCatalogRepository(tmp_path)
    results = repo.search(ToolQuery(name="websearch"))
    assert len(results) == 1


def test_search_by_description_substring(tmp_path: Path) -> None:
    """search by description substring match."""
    write_yaml(
        tmp_path / "tools.yaml",
        [
            _tool_dict("s1", description="Searches the web for information"),
            _tool_dict("s2", description="Plans agent tasks"),
        ],
    )
    repo = YamlToolCatalogRepository(tmp_path)
    results = repo.search(ToolQuery(description="web"))
    assert len(results) == 1
    assert results[0].id == "s1"


def test_search_no_filters_returns_all(tmp_path: Path) -> None:
    """search with no filters returns all entries."""
    write_yaml(tmp_path / "tools.yaml", [_tool_dict("s1"), _tool_dict("s2", name="other")])
    repo = YamlToolCatalogRepository(tmp_path)
    results = repo.search(ToolQuery())
    assert len(results) == 2


# ---- CRUD ----


def test_create_tool_entry(tmp_path: Path) -> None:
    """AC #3: create() persists entry to YAML."""
    repo = YamlToolCatalogRepository(tmp_path)
    entry = ToolEntry.model_validate(
        _tool_dict("new-tool", name="new-search", description="A new search tool")
    )
    result_id = repo.create(entry)
    assert result_id == "new-tool"

    # Verify via fresh repo
    repo2 = YamlToolCatalogRepository(tmp_path)
    loaded = repo2.get("new-tool")
    assert loaded is not None
    assert loaded.tool.name == "new-search"


def test_create_duplicate_id_raises(tmp_path: Path) -> None:
    """create() raises CatalogValidationError if id already exists."""
    write_yaml(tmp_path / "existing.yaml", [_tool_dict("dup")])
    repo = YamlToolCatalogRepository(tmp_path)
    entry = ToolEntry.model_validate(_tool_dict("dup"))
    with pytest.raises(CatalogValidationError) as exc_info:
        repo.create(entry)
    assert "dup" in str(exc_info.value)


def test_search_by_description_case_insensitive(tmp_path: Path) -> None:
    """Description search is case-insensitive."""
    write_yaml(
        tmp_path / "tools.yaml",
        [_tool_dict("s1", description="Searches The WEB")],
    )
    repo = YamlToolCatalogRepository(tmp_path)
    results = repo.search(ToolQuery(description="the web"))
    assert len(results) == 1


def test_update_tool_entry(tmp_path: Path) -> None:
    """AC #3: update() modifies entry in file."""
    write_yaml(tmp_path / "t1.yaml", [_tool_dict("t1", name="old-name")])
    repo = YamlToolCatalogRepository(tmp_path)
    updated = ToolEntry.model_validate(
        _tool_dict("t1", name="new-name", description="Updated tool")
    )
    repo.update("t1", updated)

    entry = repo.get("t1")
    assert entry is not None
    assert entry.tool.name == "new-name"


def test_update_raises_for_missing_id(tmp_path: Path) -> None:
    """update() raises EntryNotFoundError for missing id."""
    write_yaml(tmp_path / "t.yaml", [_tool_dict("t1")])
    repo = YamlToolCatalogRepository(tmp_path)
    entry = ToolEntry.model_validate(_tool_dict("missing"))
    with pytest.raises(EntryNotFoundError):
        repo.update("missing", entry)


def test_delete_tool_entry(tmp_path: Path) -> None:
    """AC #3: delete() removes entry, deletes file if empty."""
    write_yaml(tmp_path / "t1.yaml", [_tool_dict("t1")])
    repo = YamlToolCatalogRepository(tmp_path)
    repo.delete("t1")
    assert repo.get("t1") is None
    assert not (tmp_path / "t1.yaml").exists()


def test_delete_raises_for_missing_id(tmp_path: Path) -> None:
    """delete() raises EntryNotFoundError for missing id."""
    write_yaml(tmp_path / "t.yaml", [_tool_dict("t1")])
    repo = YamlToolCatalogRepository(tmp_path)
    with pytest.raises(EntryNotFoundError):
        repo.delete("nonexistent")


# ---- Caching ----


def test_caching_and_reload(tmp_path: Path) -> None:
    """AC #4: Caching and reload behavior."""
    write_yaml(tmp_path / "t.yaml", [_tool_dict("t1")])
    repo = YamlToolCatalogRepository(tmp_path)
    assert len(repo.list()) == 1

    # Modify on disk — cache is stale
    write_yaml(tmp_path / "t.yaml", [_tool_dict("t1"), _tool_dict("t2", name="second")])
    assert len(repo.list()) == 1  # still cached

    # Reload forces re-read
    repo.reload()
    assert len(repo.list()) == 2


# ---- Public API export ----


def test_public_api_export() -> None:
    """YamlToolCatalogRepository is importable from public API."""
    from akgentic.catalog import YamlToolCatalogRepository as Exported

    assert Exported is YamlToolCatalogRepository
