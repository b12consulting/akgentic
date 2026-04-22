"""Tests for akgentic.tool.workspace.tool module (Story 5.2)."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from akgentic.tool.errors import RetriableError
from akgentic.tool.workspace.readers import DocumentReader, MediaContent
from akgentic.tool.workspace.tool import (
    ExpandMediaRefs,
    WorkspaceGlob,
    WorkspaceGrep,
    WorkspaceList,
    WorkspaceRead,
    WorkspaceTool,
    _expand_braces,
    _grep_python,
    _grep_rg,
)
from akgentic.tool.workspace.workspace import Filesystem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_observer(
    orchestrator_is_none: bool = False,
    team_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a minimal mock ActorToolObserver."""
    observer = MagicMock()
    observer.orchestrator = None if orchestrator_is_none else MagicMock()
    observer.team_id = team_id or uuid.uuid4()
    return observer


def make_tool(tmp_path: Path, team_id: uuid.UUID | None = None) -> WorkspaceTool:
    """Build a WorkspaceTool(read_only=True) wired to a Filesystem rooted at tmp_path."""
    tid = team_id or uuid.uuid4()
    fs = Filesystem(str(tmp_path), str(tid))
    observer = make_observer(team_id=tid)
    tool = WorkspaceTool(read_only=True)
    with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
        tool.observer(observer)
    return tool


# ---------------------------------------------------------------------------
# Task 1: WorkspaceTool(read_only=True) fields (AC 1)
# ---------------------------------------------------------------------------


class TestWorkspaceReadToolFields:
    def test_default_fields(self) -> None:
        tool = WorkspaceTool(read_only=True)
        assert tool.name == "Workspace"
        assert tool.workspace_id is None
        assert tool.workspace_read is True
        assert tool.workspace_list is True
        assert tool.workspace_glob is True
        assert tool.workspace_grep is True

    def test_workspace_id_set(self) -> None:
        tool = WorkspaceTool(read_only=True, workspace_id="my-workspace")
        assert tool.workspace_id == "my-workspace"

    def test_capabilities_accept_param_models(self) -> None:
        tool = WorkspaceTool(
            read_only=True,
            workspace_read=WorkspaceRead(default_limit=500),
            workspace_list=WorkspaceList(),
            workspace_glob=WorkspaceGlob(max_results=50),
            workspace_grep=WorkspaceGrep(max_results=50),
        )
        assert isinstance(tool.workspace_read, WorkspaceRead)
        assert tool.workspace_read.default_limit == 500


# ---------------------------------------------------------------------------
# Task 1: observer() wiring (AC 2, 3)
# ---------------------------------------------------------------------------


class TestObserverWiring:
    def test_observer_raises_when_orchestrator_is_none(self, tmp_path: Path) -> None:
        observer = make_observer(orchestrator_is_none=True)
        tool = WorkspaceTool(read_only=True)
        with pytest.raises(ValueError, match="orchestrator"):
            tool.observer(observer)

    def test_observer_sets_workspace_from_team_id(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        observer = make_observer(team_id=team_id)
        fs = Filesystem(str(tmp_path), str(team_id))
        tool = WorkspaceTool(read_only=True)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs) as mock_gw:
            result = tool.observer(observer)
            mock_gw.assert_called_once_with(str(team_id))
            assert tool.workspace is fs
            assert result is tool

    def test_observer_uses_explicit_workspace_id(self, tmp_path: Path) -> None:
        observer = make_observer()
        fs = Filesystem(str(tmp_path), "explicit-ws")
        tool = WorkspaceTool(read_only=True, workspace_id="explicit-ws")
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs) as mock_gw:
            tool.observer(observer)
            mock_gw.assert_called_once_with("explicit-ws")

    def test_observer_returns_self(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        observer = make_observer(team_id=team_id)
        fs = Filesystem(str(tmp_path), str(team_id))
        tool = WorkspaceTool(read_only=True)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            result = tool.observer(observer)
        assert result is tool

    def test_observer_failed_call_does_not_set_internal_observer(self) -> None:
        """When observer() raises, _observer must NOT be set (no partial init)."""
        observer = make_observer(orchestrator_is_none=True)
        tool = WorkspaceTool(read_only=True)
        with pytest.raises(ValueError):
            tool.observer(observer)
        # _observer must remain unset — check via __pydantic_private__
        assert tool.__pydantic_private__ is None or "_observer" not in (
            tool.__pydantic_private__ or {}
        )

    def test_get_tools_raises_before_observer_called(self) -> None:
        """Calling get_tools() before observer() raises RuntimeError from workspace property."""
        tool = WorkspaceTool(read_only=True)
        with pytest.raises(RuntimeError, match="observer\\(\\) was called"):
            tool.get_tools()


# ---------------------------------------------------------------------------
# Task 2: workspace_read pagination (AC 4, 5)
# ---------------------------------------------------------------------------


class TestWorkspaceRead:
    def _make_file(self, root: Path, name: str, n_lines: int) -> None:
        content = "\n".join(f"line {i}" for i in range(1, n_lines + 1))
        (root / name).write_text(content, encoding="utf-8")

    def test_default_window_returns_first_2000_lines(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(team_id))
        root = fs._root
        self._make_file(root, "big.txt", 3500)
        tool = WorkspaceTool(read_only=True)
        observer = make_observer(team_id=team_id)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        fn = tool.get_tools()[0]  # workspace_read
        result = fn("big.txt")
        lines = result.split("\n")
        # first 2000 numbered lines + truncation notice
        assert lines[0].startswith("1     ")
        assert "2000" in lines[1999]
        assert "truncated" in lines[-1]
        assert "3500 lines total" in lines[-1]

    def test_offset_and_limit(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(team_id))
        root = fs._root
        self._make_file(root, "file.txt", 200)
        tool = WorkspaceTool(read_only=True)
        observer = make_observer(team_id=team_id)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        fn = tool.get_tools()[0]
        result = fn("file.txt", offset=100, limit=50)
        lines = [ln for ln in result.split("\n") if not ln.startswith("[")]
        assert lines[0].startswith("100   ")
        assert lines[-1].startswith("149   ")
        assert len(lines) == 50

    def test_no_truncation_when_file_fits(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(team_id))
        root = fs._root
        self._make_file(root, "small.txt", 10)
        tool = WorkspaceTool(read_only=True)
        observer = make_observer(team_id=team_id)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        fn = tool.get_tools()[0]
        result = fn("small.txt")
        assert "truncated" not in result
        assert result.split("\n")[0].startswith("1     ")

    def test_line_numbers_are_correct(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(team_id))
        (fs._root / "abc.txt").write_text("alpha\nbeta\ngamma", encoding="utf-8")
        tool = WorkspaceTool(read_only=True)
        observer = make_observer(team_id=team_id)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        fn = tool.get_tools()[0]
        result = fn("abc.txt")
        assert "1     alpha" in result
        assert "2     beta" in result
        assert "3     gamma" in result


# ---------------------------------------------------------------------------
# Task 3: workspace_list (AC 6)
# ---------------------------------------------------------------------------


class TestWorkspaceList:
    def test_list_shows_dir_and_file_entries(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "src").mkdir()
        (root / "README.md").write_bytes(b"hello")
        fn = tool.get_tools()[1]  # workspace_list
        result = fn()
        assert "src/" in result
        assert "README.md (5 bytes)" in result

    def test_list_empty_directory(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        fn = tool.get_tools()[1]
        assert fn() == "Empty directory."

    def test_list_subdirectory(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        sub = root / "pkg"
        sub.mkdir()
        (sub / "mod.py").write_bytes(b"pass")
        fn = tool.get_tools()[1]
        result = fn("pkg")
        assert "mod.py (4 bytes)" in result

    def test_list_format_bytes(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "data.bin").write_bytes(b"1234567890")
        fn = tool.get_tools()[1]
        result = fn()
        assert "10 bytes" in result


# ---------------------------------------------------------------------------
# Task 4: workspace_glob (AC 7, 8)
# ---------------------------------------------------------------------------


class TestWorkspaceGlob:
    def test_glob_returns_matching_files(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "a.py").write_bytes(b"a")
        (root / "b.py").write_bytes(b"b")
        (root / "c.txt").write_bytes(b"c")
        fn = tool.get_tools()[2]  # workspace_glob
        result = fn("**/*.py")
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_glob_sorted_by_mtime_newest_first(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        old_file = root / "old.py"
        new_file = root / "new.py"
        old_file.write_bytes(b"old")
        time.sleep(0.01)
        new_file.write_bytes(b"new")
        fn = tool.get_tools()[2]
        result = fn("**/*.py")
        lines = result.split("\n")
        assert lines[0] == "new.py"
        assert lines[1] == "old.py"

    def test_glob_cap_at_max_results_with_truncation(self, tmp_path: Path) -> None:
        tool = WorkspaceTool(read_only=True, workspace_glob=WorkspaceGlob(max_results=3))
        team_id = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(team_id))
        root = fs._root
        for i in range(5):
            (root / f"f{i}.py").write_bytes(b"x")
        observer = make_observer(team_id=team_id)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        fn = tool.get_tools()[2]
        result = fn("**/*.py")
        lines = [ln for ln in result.split("\n") if not ln.startswith("[")]
        assert len(lines) == 3
        assert "truncated" in result
        assert "5 total" in result

    def test_glob_no_files_returns_no_files_found(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        fn = tool.get_tools()[2]
        assert fn("**/*.xyz") == "No files found."

    def test_glob_path_escape_raises_permission_error(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        fn = tool.get_tools()[2]
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            fn("**/*.py", path="../../etc")

    def test_glob_no_truncation_when_under_cap(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        for i in range(3):
            (root / f"f{i}.py").write_bytes(b"x")
        fn = tool.get_tools()[2]
        result = fn("**/*.py")
        assert "truncated" not in result


# ---------------------------------------------------------------------------
# Task 5: _grep_python helper
# ---------------------------------------------------------------------------


class TestGrepPython:
    def test_finds_matching_lines(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import os\nimport sys\n", encoding="utf-8")
        results = _grep_python(tmp_path, "import os", "", 100, 2000)
        assert len(results) == 1
        path, lineno, line = results[0]
        assert lineno == 1
        assert "import os" in line

    def test_respects_include_glob(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("import os\n", encoding="utf-8")
        results = _grep_python(tmp_path, "import os", "*.py", 100, 2000)
        paths = [r[0].name for r in results]
        assert "a.py" in paths
        assert "b.txt" not in paths

    def test_no_matches_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "x.py").write_text("nothing here\n", encoding="utf-8")
        results = _grep_python(tmp_path, "xyzzy_does_not_exist", "", 100, 2000)
        assert results == []

    def test_max_results_cap(self, tmp_path: Path) -> None:
        content = "\n".join(["match"] * 10)
        (tmp_path / "a.py").write_text(content, encoding="utf-8")
        results = _grep_python(tmp_path, "match", "", 3, 2000)
        assert len(results) == 3

    def test_line_truncation(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x" * 100 + "\n", encoding="utf-8")
        results = _grep_python(tmp_path, "xxx", "", 100, 10)
        assert len(results[0][2]) <= 10

    def test_skips_directories_from_rglob(self, tmp_path: Path) -> None:
        """rglob may yield directories; they must be skipped gracefully."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (tmp_path / "a.py").write_text("match\n", encoding="utf-8")
        results = _grep_python(tmp_path, "match", "", 100, 2000)
        # Only the file should produce results, not the directory
        assert all(r[0].is_file() for r in results)

    def test_skips_unreadable_files(self, tmp_path: Path) -> None:
        """OSError during read_text must be swallowed and the file skipped."""
        good = tmp_path / "good.py"
        bad = tmp_path / "bad.py"
        good.write_text("match\n", encoding="utf-8")
        bad.write_text("match\n", encoding="utf-8")
        original_read_text = Path.read_text

        def patched_read_text(self: Path, **kwargs: object) -> str:  # type: ignore[misc]
            if self.name == "bad.py":
                raise OSError("permission denied")
            return original_read_text(self, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "read_text", patched_read_text):
            results = _grep_python(tmp_path, "match", "", 100, 2000)
        result_names = [r[0].name for r in results]
        assert "good.py" in result_names
        assert "bad.py" not in result_names


# ---------------------------------------------------------------------------
# Task 5: _grep_rg helper
# ---------------------------------------------------------------------------


class TestGrepRg:
    def test_returns_none_when_rg_not_on_path(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value=None):
            result = _grep_rg(tmp_path, "pattern", "", 100)
        assert result is None

    def test_returns_none_on_subprocess_error(self, tmp_path: Path) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/rg"),
            patch("subprocess.run", side_effect=OSError("no rg")),
        ):
            result = _grep_rg(tmp_path, "pattern", "", 100)
        assert result is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        import subprocess as _subprocess

        with (
            patch("shutil.which", return_value="/usr/bin/rg"),
            patch(
                "subprocess.run",
                side_effect=_subprocess.TimeoutExpired(cmd=["rg"], timeout=15),
            ),
        ):
            result = _grep_rg(tmp_path, "pattern", "", 100)
        assert result is None

    def test_returns_none_on_nonzero_returncode(self, tmp_path: Path) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 2
        mock_result.stdout = ""
        with (
            patch("shutil.which", return_value="/usr/bin/rg"),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = _grep_rg(tmp_path, "pattern", "", 100)
        assert result is None

    def test_parses_rg_output_into_tuples(self, tmp_path: Path) -> None:
        fake_file = tmp_path / "x.py"
        fake_file.write_bytes(b"")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"{fake_file}:3:import os\n"
        with (
            patch("shutil.which", return_value="/usr/bin/rg"),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = _grep_rg(tmp_path, "import", "", 100)
        assert result is not None
        assert len(result) == 1
        path, lineno, line = result[0]
        assert lineno == 3
        assert line == "import os"


# ---------------------------------------------------------------------------
# Task 5: workspace_grep integration (AC 9, 10, 11)
# ---------------------------------------------------------------------------


class TestWorkspaceGrep:
    def test_grep_python_fallback_finds_matches(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "main.py").write_text("import os\npass\n", encoding="utf-8")
        fn = tool.get_tools()[3]  # workspace_grep
        with patch("akgentic.tool.workspace.tool._grep_rg", return_value=None):
            result = fn("import os")
        assert "main.py" in result
        assert "import os" in result

    def test_grep_no_matches_returns_no_matches_found(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "x.py").write_text("nothing\n", encoding="utf-8")
        fn = tool.get_tools()[3]
        with patch("akgentic.tool.workspace.tool._grep_rg", return_value=None):
            result = fn("xyzzy_not_found")
        assert result == "No matches found."

    def test_grep_with_include_filter(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "a.py").write_text("needle\n", encoding="utf-8")
        (root / "b.txt").write_text("needle\n", encoding="utf-8")
        fn = tool.get_tools()[3]
        with patch("akgentic.tool.workspace.tool._grep_rg", return_value=None):
            result = fn("needle", include="*.py")
        assert "a.py" in result
        assert "b.txt" not in result

    def test_grep_path_escape_raises_permission_error(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        fn = tool.get_tools()[3]
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            fn("pattern", path="../../etc")

    def test_grep_uses_rg_when_available(self, tmp_path: Path) -> None:
        """When rg returns results, _grep_python must NOT be called."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        fake_match = (root / "x.py", 1, "import os")
        fn = tool.get_tools()[3]
        with (
            patch("akgentic.tool.workspace.tool._grep_rg", return_value=[fake_match]) as mock_rg,
            patch("akgentic.tool.workspace.tool._grep_python") as mock_py,
        ):
            result = fn("import os")
        mock_rg.assert_called_once()
        mock_py.assert_not_called()
        assert "x.py" in result


# ---------------------------------------------------------------------------
# Capability toggling (AC 12)
# ---------------------------------------------------------------------------


class TestCapabilityToggling:
    def test_all_enabled_returns_four_tools(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        assert len(tool.get_tools()) == 5  # read, list, glob, grep, view

    def test_glob_and_grep_disabled_returns_three_tools(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(team_id))
        observer = make_observer(team_id=team_id)
        tool = WorkspaceTool(read_only=True, workspace_glob=False, workspace_grep=False)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        tools = tool.get_tools()
        assert len(tools) == 3  # read, list, view
        names = [t.__name__ for t in tools]
        assert "workspace_read" in names
        assert "workspace_list" in names

    def test_all_disabled_returns_empty_list(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(team_id))
        observer = make_observer(team_id=team_id)
        tool = WorkspaceTool(
            read_only=True,
            workspace_read=False,
            workspace_list=False,
            workspace_glob=False,
            workspace_grep=False,
            workspace_view=False,
        )
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        assert tool.get_tools() == []

    def test_single_capability_enabled(self, tmp_path: Path) -> None:
        team_id = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(team_id))
        observer = make_observer(team_id=team_id)
        tool = WorkspaceTool(
            read_only=True,
            workspace_read=False,
            workspace_list=False,
            workspace_glob=WorkspaceGlob(),
            workspace_grep=False,
            workspace_view=False,
        )
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        tools = tool.get_tools()
        assert len(tools) == 1
        assert tools[0].__name__ == "workspace_glob"


# ---------------------------------------------------------------------------
# Story 5.6: workspace_list depth variants
# ---------------------------------------------------------------------------


class TestWorkspaceListDepth:
    def test_depth_1_returns_flat_list_format(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "src").mkdir()
        (root / "README.md").write_bytes(b"hello")
        fn = tool.get_tools()[1]  # workspace_list
        result = fn()  # default depth=1
        # Flat format: no tree connectors
        assert "src/" in result
        assert "README.md (5 bytes)" in result
        assert "├──" not in result
        assert "└──" not in result

    def test_depth_2_returns_ascii_tree(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        src = root / "src"
        src.mkdir()
        (src / "main.py").write_bytes(b"pass")
        fn = tool.get_tools()[1]
        result = fn(depth=2)
        assert result.startswith(".")
        assert "src/" in result
        assert "main.py" in result
        # Tree connectors present
        assert "├──" in result or "└──" in result

    def test_depth_0_returns_unlimited_tree(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        deep = root / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "file.txt").write_bytes(b"x")
        fn = tool.get_tools()[1]
        result = fn(depth=0)
        assert result.startswith(".")
        assert "file.txt" in result
        assert "a/" in result
        assert "b/" in result
        assert "c/" in result

    def test_depth_tree_ordering_dirs_before_files(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "zfile.txt").write_bytes(b"z")
        (root / "adir").mkdir()
        fn = tool.get_tools()[1]
        result = fn(depth=2)
        lines = result.split("\n")
        # First non-root entry should be the directory
        entry_lines = [line for line in lines if "├──" in line or "└──" in line]
        assert "adir/" in entry_lines[0]
        assert "zfile.txt" in entry_lines[1]

    def test_empty_directory_any_depth_returns_empty(self, tmp_path: Path) -> None:
        tool = make_tool(tmp_path)
        fn = tool.get_tools()[1]
        assert fn(depth=2) == "Empty directory."
        assert fn(depth=0) == "Empty directory."


# ---------------------------------------------------------------------------
# Story 5.7: RetriableError wrapping in read-only tools
# ---------------------------------------------------------------------------


class TestRetriableErrorReadTool:
    def test_read_file_not_found_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_read raises RetriableError for non-existent files."""
        tool = make_tool(tmp_path)
        read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
        with pytest.raises(RetriableError, match="File not found: nonexistent.txt"):
            read_fn("nonexistent.txt")

    def test_read_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_read raises RetriableError when backend raises PermissionError."""
        tool = make_tool(tmp_path)
        read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
        with patch.object(tool.workspace, "read", side_effect=PermissionError("escaped")):
            with pytest.raises(RetriableError, match="Path escapes workspace root"):
                read_fn("src/file.py")

    def test_list_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_list raises RetriableError when path escapes workspace root."""
        tool = make_tool(tmp_path)
        list_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_list")
        with patch.object(tool.workspace, "_validate_path", side_effect=PermissionError("escaped")):
            with pytest.raises(RetriableError, match="Path escapes workspace root"):
                list_fn("some/path")

    def test_glob_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_glob raises RetriableError for path escaping workspace."""
        tool = make_tool(tmp_path)
        glob_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_glob")
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            glob_fn("**/*.py", path="../../escape")

    def test_grep_invalid_regex_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_grep raises RetriableError for invalid regex patterns."""
        tool = make_tool(tmp_path)
        # Write a file so grep actually tries to match
        (tool.workspace._root / "test.txt").write_text("hello", encoding="utf-8")
        grep_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_grep")
        with patch("akgentic.tool.workspace.tool._grep_rg", return_value=None):
            with pytest.raises(RetriableError, match="Invalid regex pattern"):
                grep_fn("[invalid")  # unclosed bracket — invalid regex

    def test_grep_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_grep raises RetriableError for path escaping workspace root."""
        tool = make_tool(tmp_path)
        grep_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_grep")
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            grep_fn("pattern", path="../../escape")


# ---------------------------------------------------------------------------
# Helpers for binary reading tests
# ---------------------------------------------------------------------------


def make_wired_tool(
    tmp_path: Path,
    document_reader: DocumentReader | bool = True,
) -> tuple[WorkspaceTool, Filesystem]:
    """Build a WorkspaceTool(read_only=True) wired to a Filesystem, returning both."""
    tid = uuid.uuid4()
    fs = Filesystem(str(tmp_path), str(tid))
    observer = make_observer(team_id=tid)
    tool = WorkspaceTool(read_only=True, document_reader=document_reader)
    with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
        tool.observer(observer)
    return tool, fs


# ---------------------------------------------------------------------------
# Story 5.8: Binary file reading — DocumentReader and sidecar cache
# ---------------------------------------------------------------------------


class TestBinaryFileReading:
    """Tests for binary file reading dispatch in workspace_read (AC 1-10)."""

    def test_document_reader_none_raises_value_error(self, tmp_path: Path) -> None:
        """AC 1: workspace_read on binary ext with document_reader=False -> ValueError."""
        tool, fs = make_wired_tool(tmp_path, document_reader=False)
        pdf_path = fs._root / "report.pdf"
        pdf_path.write_bytes(b"%PDF fake")

        read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
        with pytest.raises(ValueError, match='pip install "akgentic-tool\\[docs\\]"'):
            read_fn("report.pdf")

    def test_pass1_success_extracts_and_writes_sidecar(self, tmp_path: Path) -> None:
        """AC 2: Pass 1 success -> sidecar written, content returned paginated."""
        reader = DocumentReader()
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        pdf_path = fs._root / "report.pdf"
        pdf_path.write_bytes(b"%PDF fake content")

        extracted = "# Report\n" + "x" * 60
        with patch.object(DocumentReader, "extract_text", return_value=extracted):
            read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
            result = read_fn("report.pdf")

        assert "# Report" in result
        sidecar = fs._root / ".report.pdf.md"
        assert sidecar.exists()
        assert "# Report" in sidecar.read_text(encoding="utf-8")

    def test_sidecar_cache_hit(self, tmp_path: Path) -> None:
        """AC 3: Sidecar exists + force_document_regeneration=False -> no extraction."""
        reader = DocumentReader()
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        pdf_path = fs._root / "report.pdf"
        pdf_path.write_bytes(b"%PDF fake")
        sidecar = fs._root / ".report.pdf.md"
        sidecar.write_text("# Cached Content\nline two", encoding="utf-8")

        with patch.object(DocumentReader, "extract_text") as mock_extract:
            read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
            result = read_fn("report.pdf")
            mock_extract.assert_not_called()

        assert "# Cached Content" in result

    def test_force_regeneration_bypasses_cache(self, tmp_path: Path) -> None:
        """AC 4: force_document_regeneration=True -> re-extracts even if sidecar exists."""
        reader = DocumentReader()
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        pdf_path = fs._root / "report.pdf"
        pdf_path.write_bytes(b"%PDF fake")
        sidecar = fs._root / ".report.pdf.md"
        sidecar.write_text("# Old Cache", encoding="utf-8")

        extracted = "# Fresh Extract\n" + "y" * 60
        with patch.object(DocumentReader, "extract_text", return_value=extracted):
            read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
            result = read_fn("report.pdf", force_document_regeneration=True)

        assert "# Fresh Extract" in result
        assert "# Old Cache" not in sidecar.read_text(encoding="utf-8")

    def test_pass1_empty_no_llm_returns_placeholder(self, tmp_path: Path) -> None:
        """AC 5: Pass 1 empty + no LLM -> placeholder written and returned."""
        reader = DocumentReader(llm_client=None)
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        pdf_path = fs._root / "scan.pdf"
        pdf_path.write_bytes(b"%PDF image only")

        placeholder = "<!-- markitdown: no text extracted -->"
        with patch.object(DocumentReader, "extract_text", return_value=placeholder):
            read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
            result = read_fn("scan.pdf")

        assert "markitdown: no text extracted" in result
        sidecar = fs._root / ".scan.pdf.md"
        assert sidecar.exists()
        assert "markitdown: no text extracted" in sidecar.read_text(encoding="utf-8")

    def test_pass1_empty_pass2_with_llm_returns_content(self, tmp_path: Path) -> None:
        """AC 6: Pass 1 empty + LLM configured -> Pass 2 invoked, content returned."""
        reader = DocumentReader(llm_client="openai", llm_model="gpt-4o")
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        pdf_path = fs._root / "scan.pdf"
        pdf_path.write_bytes(b"%PDF image only")

        extracted = "# OCR Result\n" + "z" * 60
        with patch.object(DocumentReader, "extract_text", return_value=extracted):
            read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
            result = read_fn("scan.pdf")

        assert "# OCR Result" in result

    def test_both_passes_empty_returns_placeholder(self, tmp_path: Path) -> None:
        """AC 7: Both passes return empty -> placeholder written and returned."""
        reader = DocumentReader(llm_client="openai")
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        img_path = fs._root / "photo.png"
        img_path.write_bytes(b"\x89PNG fake")

        placeholder = "<!-- markitdown: no text extracted -->"
        with patch.object(DocumentReader, "extract_text", return_value=placeholder):
            read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
            result = read_fn("photo.png")

        assert "markitdown: no text extracted" in result
        sidecar = fs._root / ".photo.png.md"
        assert sidecar.exists()

    def test_text_extension_uses_text_path(self, tmp_path: Path) -> None:
        """AC 8: Text extension -> DocumentReader never invoked."""
        reader = DocumentReader()
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        txt_path = fs._root / "notes.txt"
        txt_path.write_text("Hello, world!", encoding="utf-8")

        with patch.object(DocumentReader, "extract_text") as mock_extract:
            read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
            result = read_fn("notes.txt")
            mock_extract.assert_not_called()

        assert "Hello, world!" in result

    def test_sidecar_self_read_guard(self, tmp_path: Path) -> None:
        """AC 9: .report.pdf.md reads as plain text, no extraction."""
        reader = DocumentReader()
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        sidecar = fs._root / ".report.pdf.md"
        sidecar.write_text("# Sidecar Content", encoding="utf-8")

        with patch.object(DocumentReader, "extract_text") as mock_extract:
            read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
            result = read_fn(".report.pdf.md")
            mock_extract.assert_not_called()

        assert "# Sidecar Content" in result

    def test_subdirectory_binary_file_sidecar_path(self, tmp_path: Path) -> None:
        """Sidecar for binary file in subdirectory is placed in same directory."""
        reader = DocumentReader()
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        docs_dir = fs._root / "docs"
        docs_dir.mkdir()
        pdf_path = docs_dir / "slides.pptx"
        pdf_path.write_bytes(b"PK fake pptx")

        extracted = "# Slides\n" + "a" * 60
        with patch.object(DocumentReader, "extract_text", return_value=extracted):
            read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
            result = read_fn("docs/slides.pptx")

        assert "# Slides" in result
        sidecar = docs_dir / ".slides.pptx.md"
        assert sidecar.exists()

    def test_unknown_extension_uses_text_path(self, tmp_path: Path) -> None:
        """Unknown extension falls through to UTF-8 decode path."""
        reader = DocumentReader()
        tool, fs = make_wired_tool(tmp_path, document_reader=reader)
        file_path = fs._root / "data.custom"
        file_path.write_text("custom format data", encoding="utf-8")

        read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
        result = read_fn("data.custom")
        assert "custom format data" in result

    def test_binary_file_not_found_raises_retriable_error(self, tmp_path: Path) -> None:
        """Binary file that doesn't exist -> RetriableError (not ValueError)."""
        reader = DocumentReader()
        tool, _fs = make_wired_tool(tmp_path, document_reader=reader)

        read_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_read")
        with pytest.raises(RetriableError, match="File not found: missing.pdf"):
            read_fn("missing.pdf")


# ---------------------------------------------------------------------------
# Story 5.8: DocumentReader.extract_text unit tests (mocked markitdown)
# ---------------------------------------------------------------------------


class TestDocumentReaderExtractText:
    """Unit tests for DocumentReader.extract_text with mocked markitdown module."""

    def _make_mock_markitdown_module(self) -> MagicMock:
        """Create a mock markitdown module with MarkItDown class."""
        mock_module = MagicMock()
        return mock_module

    def test_pass1_success_returns_content(self) -> None:
        """Pass 1 yields >= 50 non-ws chars -> returns content directly."""
        reader = DocumentReader()
        mock_md_module = self._make_mock_markitdown_module()
        mock_result = MagicMock()
        mock_result.text_content = "# Report\n" + "x" * 60
        mock_md_module.MarkItDown.return_value.convert.return_value = mock_result

        import sys

        with patch.dict(sys.modules, {"markitdown": mock_md_module}):
            result = reader.extract_text(b"%PDF fake", "report.pdf")

        assert result == "# Report\n" + "x" * 60

    def test_pass1_empty_no_llm_returns_placeholder(self) -> None:
        """Pass 1 empty + no LLM -> placeholder returned."""
        reader = DocumentReader(llm_client=None)
        mock_md_module = self._make_mock_markitdown_module()
        mock_result = MagicMock()
        mock_result.text_content = ""
        mock_md_module.MarkItDown.return_value.convert.return_value = mock_result

        import sys

        with patch.dict(sys.modules, {"markitdown": mock_md_module}):
            result = reader.extract_text(b"%PDF fake", "scan.pdf")

        assert result == "<!-- markitdown: no text extracted -->"

    def test_pass1_empty_pass2_with_llm(self) -> None:
        """Pass 1 empty + LLM -> Pass 2 invoked with lazily-created OpenAI client."""
        reader = DocumentReader(llm_client="openai", llm_model="gpt-4o")
        mock_md_module = self._make_mock_markitdown_module()

        empty_result = MagicMock()
        empty_result.text_content = ""
        vision_result = MagicMock()
        vision_result.text_content = "# OCR\n" + "z" * 60

        call_count = 0

        def mock_md_class(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            instance = MagicMock()
            if call_count == 1:
                instance.convert.return_value = empty_result
            else:
                instance.convert.return_value = vision_result
            return instance

        mock_md_module.MarkItDown = mock_md_class

        import sys

        mock_openai_instance = MagicMock()
        mock_openai_module = MagicMock()
        mock_openai_module.OpenAI = MagicMock(return_value=mock_openai_instance)
        with patch.dict(sys.modules, {"markitdown": mock_md_module, "openai": mock_openai_module}):
            result = reader.extract_text(b"%PDF image", "scan.pdf")

        assert result == "# OCR\n" + "z" * 60
        assert call_count == 2

    def test_both_passes_empty_returns_placeholder(self) -> None:
        """Both passes empty -> placeholder."""
        reader = DocumentReader(llm_client="openai")
        mock_md_module = self._make_mock_markitdown_module()
        empty_result = MagicMock()
        empty_result.text_content = ""
        mock_md_module.MarkItDown.return_value.convert.return_value = empty_result

        import sys

        mock_openai_instance = MagicMock()
        mock_openai_module = MagicMock()
        mock_openai_module.OpenAI = MagicMock(return_value=mock_openai_instance)
        with patch.dict(sys.modules, {"markitdown": mock_md_module, "openai": mock_openai_module}):
            result = reader.extract_text(b"\x89PNG", "photo.png")

        assert result == "<!-- markitdown: no text extracted -->"

    def test_markitdown_not_installed_raises_import_error(self) -> None:
        """When markitdown is not installed, raises ImportError with hint."""
        reader = DocumentReader()
        import sys

        # Ensure markitdown is not available
        with patch.dict(sys.modules, {"markitdown": None}):
            with pytest.raises(ImportError, match='pip install "akgentic-tool\\[docs\\]"'):
                reader.extract_text(b"fake", "file.pdf")


# ---------------------------------------------------------------------------
# Story 5.9: _expand_braces unit tests
# ---------------------------------------------------------------------------


class TestExpandBraces:
    def test_expand_braces_no_braces(self) -> None:
        assert _expand_braces("**/*.py") == ["**/*.py"]

    def test_expand_braces_single_group(self) -> None:
        result = _expand_braces("**/*.{py,js}")
        assert result == ["**/*.py", "**/*.js"]

    def test_expand_braces_multiple_groups(self) -> None:
        result = _expand_braces("{src,lib}/**/*.{py,js}")
        assert result == ["src/**/*.py", "src/**/*.js", "lib/**/*.py", "lib/**/*.js"]

    def test_expand_braces_strips_whitespace(self) -> None:
        result = _expand_braces("**/*.{py, js, ts}")
        assert result == ["**/*.py", "**/*.js", "**/*.ts"]

    def test_expand_braces_single_alternative(self) -> None:
        """Single alternative in braces degenerates to one pattern."""
        assert _expand_braces("**/*.{py}") == ["**/*.py"]


# ---------------------------------------------------------------------------
# Story 5.9: workspace_glob brace expansion integration tests
# ---------------------------------------------------------------------------


class TestWorkspaceGlobBraceExpansion:
    def test_single_brace_group_multi_extension(self, tmp_path: Path) -> None:
        """AC 1: workspace_glob("**/*.{py,js}") returns both .py and .js files."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "a.py").write_bytes(b"a")
        (root / "b.js").write_bytes(b"b")
        glob_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_glob")
        result = glob_fn("**/*.{py,js}")
        lines = result.splitlines()
        assert "a.py" in lines
        assert "b.js" in lines

    def test_no_brace_passthrough(self, tmp_path: Path) -> None:
        """AC 5: No-brace pattern behaves identically to pre-brace implementation."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "c.py").write_bytes(b"c")
        glob_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_glob")
        result = glob_fn("**/*.py")
        assert "c.py" in result

    def test_deduplication(self, tmp_path: Path) -> None:
        """AC 2: Duplicate expansions produce deduplicated results."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "x.py").write_bytes(b"x")
        glob_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_glob")
        result = glob_fn("**/*.{py,py}")
        lines = [ln for ln in result.split("\n") if not ln.startswith("[")]
        assert lines.count("x.py") == 1

    def test_multiple_brace_groups_combinatorial(self, tmp_path: Path) -> None:
        """AC 3, 4: Multiple brace groups expand combinatorially and merge results."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        src = root / "src"
        src.mkdir()
        lib = root / "lib"
        lib.mkdir()
        (src / "a.py").write_bytes(b"a")
        (lib / "b.js").write_bytes(b"b")
        glob_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_glob")
        result = glob_fn("{src,lib}/**/*.{py,js}")
        lines = result.splitlines()
        assert "src/a.py" in lines
        assert "lib/b.js" in lines

    def test_mtime_sort_preserved(self, tmp_path: Path) -> None:
        """AC 1: Results are sorted newest-first by mtime."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        old_file = root / "old.py"
        new_file = root / "new.py"
        old_file.write_bytes(b"old")
        time.sleep(0.05)
        new_file.write_bytes(b"new")
        glob_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_glob")
        result = glob_fn("**/*.{py,ts}")
        lines = [ln for ln in result.split("\n") if not ln.startswith("[")]
        assert lines[0] == "new.py"
        assert lines[1] == "old.py"

    def test_max_results_cap_preserved(self, tmp_path: Path) -> None:
        """AC 6: max_results cap and truncation notice still work with brace expansion."""
        tool = WorkspaceTool(read_only=True, workspace_glob=WorkspaceGlob(max_results=100))
        team_id = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(team_id))
        root = fs._root
        for i in range(110):
            (root / f"f{i:03d}.py").write_bytes(b"x")
        observer = make_observer(team_id=team_id)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        glob_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_glob")
        result = glob_fn("**/*.{py,js}")
        lines = [ln for ln in result.split("\n") if not ln.startswith("[")]
        assert len(lines) <= 100
        assert "truncated" in result


# ---------------------------------------------------------------------------
# Story 5.10: DocumentReader serialization tests
# ---------------------------------------------------------------------------


class TestDocumentReaderSerialization:
    """Verify DocumentReader.model_dump() round-trips cleanly (AC: 2)."""

    def test_model_dump_default(self) -> None:
        """Default DocumentReader dumps to expected dict."""
        assert DocumentReader().model_dump() == {"llm_client": None, "llm_model": "gpt-4o"}

    def test_model_dump_with_openai(self) -> None:
        """DocumentReader with llm_client='openai' dumps correctly."""
        assert DocumentReader(llm_client="openai").model_dump() == {
            "llm_client": "openai",
            "llm_model": "gpt-4o",
        }

    def test_no_openai_client_in_dump(self) -> None:
        """Private _openai_client must not appear in model_dump()."""
        assert "_openai_client" not in DocumentReader().model_dump()

    def test_extensions_not_in_dump(self) -> None:
        """ClassVar extensions must not appear in model_dump()."""
        assert "extensions" not in DocumentReader().model_dump()

    def test_model_validate_roundtrip_default(self) -> None:
        """model_dump() -> model_validate() round-trips for default DocumentReader."""
        original = DocumentReader()
        restored = DocumentReader.model_validate(original.model_dump())
        assert restored.llm_client == original.llm_client
        assert restored.llm_model == original.llm_model

    def test_model_validate_roundtrip_with_openai(self) -> None:
        """model_dump() -> model_validate() round-trips for llm_client='openai'."""
        original = DocumentReader(llm_client="openai")
        restored = DocumentReader.model_validate(original.model_dump())
        assert restored.llm_client == "openai"
        assert restored.llm_model == "gpt-4o"


class TestWorkspaceToolSerialization:
    """Verify WorkspaceTool(read_only=True).model_dump() succeeds without ConfigDict (AC: 5)."""

    def test_default_tool_model_dump_succeeds(self) -> None:
        """model_dump() does not raise on WorkspaceTool(read_only=True)."""
        result = WorkspaceTool(read_only=True).model_dump()
        assert isinstance(result, dict)

    def test_document_reader_true_in_dump(self) -> None:
        """Default document_reader=True serializes as True."""
        assert WorkspaceTool(read_only=True).model_dump()["document_reader"] is True

    def test_document_reader_false_in_dump(self) -> None:
        """document_reader=False serializes as False."""
        assert WorkspaceTool(read_only=True, document_reader=False).model_dump()["document_reader"] is False

    def test_document_reader_instance_in_dump(self) -> None:
        """DocumentReader instance serializes to its model_dump() dict."""
        tool = WorkspaceTool(read_only=True, document_reader=DocumentReader(llm_client="openai"))
        assert tool.model_dump()["document_reader"] == {
            "llm_client": "openai",
            "llm_model": "gpt-4o",
        }

    def test_model_validate_roundtrip_with_document_reader(self) -> None:
        """model_dump -> model_validate round-trips with DocumentReader."""
        original = WorkspaceTool(read_only=True, document_reader=DocumentReader(llm_client="openai"))
        restored = WorkspaceTool.model_validate(original.model_dump())
        assert isinstance(restored.document_reader, DocumentReader)
        assert restored.document_reader.llm_client == "openai"


class TestDocumentReaderLazyInit:
    """Verify lazy OpenAI client creation via _get_openai_client() (AC: 3)."""

    def test_get_openai_client_returns_none_when_disabled(self) -> None:
        """No llm_client -> _get_openai_client() returns None."""
        assert DocumentReader()._get_openai_client() is None

    @pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"), reason="requires OPENAI_API_KEY"
    )
    def test_get_openai_client_lazy_creation(self) -> None:
        """llm_client='openai' -> lazily constructs OpenAI() on first call."""
        reader = DocumentReader(llm_client="openai")
        # Ensure private attrs are initialised (coverage hooks can interfere)
        if reader.__pydantic_private__ is None:
            reader.__pydantic_private__ = {"_openai_client": None}
        result = reader._get_openai_client()
        # After call, a real OpenAI client is returned and cached
        assert result is not None
        assert reader._openai_client is result

    @pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"), reason="requires OPENAI_API_KEY"
    )
    def test_get_openai_client_cached(self) -> None:
        """Second _get_openai_client() call reuses cached client."""
        reader = DocumentReader(llm_client="openai")
        if reader.__pydantic_private__ is None:
            reader.__pydantic_private__ = {"_openai_client": None}
        first = reader._get_openai_client()
        second = reader._get_openai_client()
        # Same object identity — no second construction
        assert first is second


# ---------------------------------------------------------------------------
# Story 3-1: ExpandMediaRefs command (AC-1 through AC-8)
# ---------------------------------------------------------------------------


class TestExpandMediaRefs:
    """Tests for WorkspaceTool._expand_media_refs (story 3-1)."""

    def test_single_image_match(self, tmp_path: Path) -> None:
        """AC-1: Single image match → MediaContent with correct bytes and media_type."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "photo.png").write_bytes(b"fake-png")
        result = tool._expand_media_refs("look at !!photo.png please")
        assert result == [
            "look at ",
            MediaContent(data=b"fake-png", media_type="image/png"),
            " please",
        ]

    def test_multi_image_match_glob(self, tmp_path: Path) -> None:
        """AC-2: Glob pattern !!*.png → sorted list of MediaContent objects."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "a.png").write_bytes(b"aaa")
        (root / "b.png").write_bytes(b"bbb")
        result = tool._expand_media_refs("check !!*.png")
        # sorted by path → a.png before b.png
        assert result == [
            "check ",
            MediaContent(data=b"aaa", media_type="image/png"),
            MediaContent(data=b"bbb", media_type="image/png"),
            "",
        ]

    def test_document_match_hint(self, tmp_path: Path) -> None:
        """AC-3: Document match (.pdf) → hint string with workspace_read instruction."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "report.pdf").write_bytes(b"%PDF-fake")
        result = tool._expand_media_refs("show !!report.pdf")
        assert result == ["show ", "!!report.pdf[=> Use workspace_read tool]", ""]

    def test_no_match_error(self, tmp_path: Path) -> None:
        """AC-4: No match → error string with pattern name."""
        tool = make_tool(tmp_path)
        result = tool._expand_media_refs("show !!missing.png")
        assert result == ["show ", "!!missing.png[Error: no image found in the workspace]", ""]

    def test_pure_text_passthrough(self, tmp_path: Path) -> None:
        """AC-5: Pure text prompt with no !! tokens → single-element list."""
        tool = make_tool(tmp_path)
        result = tool._expand_media_refs("no tokens here")
        assert result == ["no tokens here"]

    def test_disabled_field_excludes_from_commands(self, tmp_path: Path) -> None:
        """AC-6: expand_media_refs=False → ExpandMediaRefs not in get_commands()."""
        import uuid
        from unittest.mock import patch

        tid = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(tid))
        observer = make_observer(team_id=tid)
        tool = WorkspaceTool(read_only=True, expand_media_refs=False)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        commands = tool.get_commands()
        assert ExpandMediaRefs not in commands

    def test_enabled_field_includes_in_commands(self, tmp_path: Path) -> None:
        """AC-6 inverse: expand_media_refs=True → ExpandMediaRefs in get_commands()."""
        tool = make_tool(tmp_path)
        commands = tool.get_commands()
        assert ExpandMediaRefs in commands
        assert commands[ExpandMediaRefs] == tool._expand_media_refs

    def test_media_content_model_dump(self) -> None:
        """AC-7: MediaContent.model_dump() returns correct dict."""
        mc = MediaContent(data=b"abc", media_type="image/png")
        result = mc.model_dump()
        assert result == {"data": b"abc", "media_type": "image/png"}

    def test_mixed_prompt_text_and_image(self, tmp_path: Path) -> None:
        """AC-8: Mixed prompt — text segments interleaved with image tokens."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "cat.jpg").write_bytes(b"jpg-bytes")
        result = tool._expand_media_refs("hello !!cat.jpg world")
        assert result == [
            "hello ",
            MediaContent(data=b"jpg-bytes", media_type="image/jpeg"),
            " world",
        ]

    def test_expand_media_refs_not_in_get_tools(self, tmp_path: Path) -> None:
        """AC-8 / Task 6.9: ExpandMediaRefs must NOT appear in get_tools() — COMMAND channel only."""
        tool = make_tool(tmp_path)
        tools = tool.get_tools()
        tool_names = [fn.__name__ for fn in tools]
        assert "expand_media_refs" not in tool_names

    def test_jpg_mime_type(self, tmp_path: Path) -> None:
        """Image with .jpg extension → media_type image/jpeg."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "img.jpg").write_bytes(b"jpegdata")
        result = tool._expand_media_refs("!!img.jpg")
        assert result == [MediaContent(data=b"jpegdata", media_type="image/jpeg"), ""]

    def test_webp_mime_type(self, tmp_path: Path) -> None:
        """Image with .webp extension → media_type image/webp."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "anim.webp").write_bytes(b"webpdata")
        result = tool._expand_media_refs("!!anim.webp")
        assert result == [MediaContent(data=b"webpdata", media_type="image/webp"), ""]

    def test_gif_mime_type(self, tmp_path: Path) -> None:
        """Image with .gif extension → media_type image/gif."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "anim.gif").write_bytes(b"gifdata")
        result = tool._expand_media_refs("!!anim.gif")
        assert result == [MediaContent(data=b"gifdata", media_type="image/gif"), ""]

    def test_bmp_mime_type(self, tmp_path: Path) -> None:
        """Image with .bmp extension → media_type image/bmp."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "icon.bmp").write_bytes(b"bmpdata")
        result = tool._expand_media_refs("!!icon.bmp")
        assert result == [MediaContent(data=b"bmpdata", media_type="image/bmp"), ""]

    def test_image_takes_priority_over_doc_extension(self, tmp_path: Path) -> None:
        """Image extensions shared with DocumentReader (e.g. .png) → MediaContent, not hint."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        # .png is in both _MIME_MAP and DocumentReader.extensions
        assert ".png" in DocumentReader.extensions
        (root / "photo.png").write_bytes(b"imgdata")
        result = tool._expand_media_refs("!!photo.png")
        assert result == [MediaContent(data=b"imgdata", media_type="image/png"), ""]

    def test_multiple_tokens_in_prompt(self, tmp_path: Path) -> None:
        """Multiple !! tokens in one prompt → each expands independently."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "a.png").write_bytes(b"adata")
        (root / "b.png").write_bytes(b"bdata")
        result = tool._expand_media_refs("first !!a.png middle !!b.png end")
        assert result == [
            "first ",
            MediaContent(data=b"adata", media_type="image/png"),
            " middle ",
            MediaContent(data=b"bdata", media_type="image/png"),
            " end",
        ]

    # ------------------------------------------------------------------
    # Quoted glob syntax: !!"pattern with spaces"
    # ------------------------------------------------------------------

    def test_quoted_image_with_spaces(self, tmp_path: Path) -> None:
        """Quoted syntax !!"file name.png" matches files with spaces."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "my photo.png").write_bytes(b"spaced-png")
        result = tool._expand_media_refs('look at !!"my photo.png" please')
        assert result == [
            "look at ",
            MediaContent(data=b"spaced-png", media_type="image/png"),
            " please",
        ]

    def test_quoted_glob_with_spaces(self, tmp_path: Path) -> None:
        """Quoted glob !!"sub dir/*.png" expands files in spaced directories."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "sub dir").mkdir()
        (root / "sub dir" / "a.png").write_bytes(b"aaa")
        (root / "sub dir" / "b.png").write_bytes(b"bbb")
        result = tool._expand_media_refs('check !!"sub dir/*.png"')
        assert result == [
            "check ",
            MediaContent(data=b"aaa", media_type="image/png"),
            MediaContent(data=b"bbb", media_type="image/png"),
            "",
        ]

    def test_quoted_no_match_error(self, tmp_path: Path) -> None:
        """Quoted syntax with no match → error string."""
        tool = make_tool(tmp_path)
        result = tool._expand_media_refs('show !!"no such file.png"')
        assert result == [
            "show ",
            "!!no such file.png[Error: no image found in the workspace]",
            "",
        ]

    def test_quoted_and_unquoted_mixed(self, tmp_path: Path) -> None:
        """Mix of quoted and unquoted refs in the same prompt."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "simple.png").write_bytes(b"s")
        (root / "has space.png").write_bytes(b"sp")
        result = tool._expand_media_refs('first !!simple.png then !!"has space.png" end')
        assert result == [
            "first ",
            MediaContent(data=b"s", media_type="image/png"),
            " then ",
            MediaContent(data=b"sp", media_type="image/png"),
            " end",
        ]

    def test_quoted_document_hint(self, tmp_path: Path) -> None:
        """Quoted syntax with document match → hint string."""
        tool = make_tool(tmp_path)
        root = tool.workspace._root
        (root / "my report.pdf").write_bytes(b"%PDF-fake")
        result = tool._expand_media_refs('read !!"my report.pdf"')
        assert result == [
            "read ",
            "!!my report.pdf[=> Use workspace_read tool]",
            "",
        ]

    def test_expand_media_refs_raises_before_observer_called(self) -> None:
        """M2: Calling _expand_media_refs before observer() raises RuntimeError."""
        tool = WorkspaceTool(read_only=True)
        with pytest.raises(RuntimeError):
            tool._expand_media_refs("!!photo.png")
