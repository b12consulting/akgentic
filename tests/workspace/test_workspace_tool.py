"""Tests for WorkspaceTool — write and delete capabilities (Story 5.4)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from akgentic.tool.errors import RetriableError
from akgentic.tool.workspace.edit import EditItem
from akgentic.tool.workspace.tool import WorkspaceTool, _normalize_glob_pattern
from akgentic.tool.workspace.workspace import Filesystem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_observer(
    tmp_path: Path,
    team_id: uuid.UUID | None = None,
) -> tuple[MagicMock, Filesystem]:
    """Create a mock observer and matching Filesystem for tests."""
    tid = team_id or uuid.uuid4()
    observer = MagicMock()
    observer.orchestrator = MagicMock()
    observer.team_id = tid
    fs = Filesystem(str(tmp_path), str(tid))
    return observer, fs


def make_wired_tool(tmp_path: Path) -> tuple[WorkspaceTool, Filesystem]:
    """Create a WorkspaceTool wired to a real tmp Filesystem."""
    observer, fs = make_observer(tmp_path)
    with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
        tool = WorkspaceTool()
        tool.observer(observer)
    return tool, fs


# ---------------------------------------------------------------------------
# Task 2: observer() delegation (AC 1)
# ---------------------------------------------------------------------------


class TestObserverDelegation:
    def test_observer_sets_workspace(self, tmp_path: Path) -> None:
        """observer() via super() sets self.workspace correctly."""
        tool, fs = make_wired_tool(tmp_path)
        assert tool.workspace is fs

    def test_observer_returns_self(self, tmp_path: Path) -> None:
        """observer() returns self typed as WorkspaceTool."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool()
            result = tool.observer(observer)
        assert result is tool
        assert isinstance(result, WorkspaceTool)

    def test_observer_raises_when_orchestrator_none(self, tmp_path: Path) -> None:
        """Inherited guard: observer with None orchestrator raises ValueError."""
        observer = MagicMock()
        observer.orchestrator = None
        tool = WorkspaceTool()
        with pytest.raises(ValueError, match="orchestrator"):
            tool.observer(observer)


# ---------------------------------------------------------------------------
# Task 2 / Task 7: get_tools() count and names (AC 2)
# ---------------------------------------------------------------------------


class TestGetToolsDefault:
    def test_default_count_is_ten(self, tmp_path: Path) -> None:
        """By default, get_tools() returns all 11 tool callables."""
        tool, _ = make_wired_tool(tmp_path)
        tools = tool.get_tools()
        assert len(tools) == 11  # read, list, glob, grep, view, write, delete, edit, multi_edit, patch, mkdir

    def test_default_includes_all_read_tools(self, tmp_path: Path) -> None:
        tool, _ = make_wired_tool(tmp_path)
        names = [t.__name__ for t in tool.get_tools()]
        assert "workspace_read" in names
        assert "workspace_list" in names
        assert "workspace_glob" in names
        assert "workspace_grep" in names

    def test_default_includes_write_and_delete(self, tmp_path: Path) -> None:
        tool, _ = make_wired_tool(tmp_path)
        names = [t.__name__ for t in tool.get_tools()]
        assert "workspace_write" in names
        assert "workspace_delete" in names


# ---------------------------------------------------------------------------
# Task 3: workspace_write — new file (AC 3)
# ---------------------------------------------------------------------------


class TestWorkspaceWriteNewFile:
    def test_write_new_file_creates_it(self, tmp_path: Path) -> None:
        """workspace_write creates a new file with the given content."""
        tool, fs = make_wired_tool(tmp_path)
        write_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_write")
        result = write_fn("new_file.py", "print('hello')\n")
        assert result == "Written: new_file.py"
        assert (fs._root / "new_file.py").read_text(encoding="utf-8") == "print('hello')\n"

    def test_write_new_file_creates_parent_dirs(self, tmp_path: Path) -> None:
        """workspace_write creates missing parent directories."""
        tool, fs = make_wired_tool(tmp_path)
        write_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_write")
        result = write_fn("deep/nested/dir/file.py", "content\n")
        assert result == "Written: deep/nested/dir/file.py"
        assert (fs._root / "deep/nested/dir/file.py").exists()

    def test_write_new_file_returns_written_path(self, tmp_path: Path) -> None:
        tool, fs = make_wired_tool(tmp_path)
        write_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_write")
        result = write_fn("src/module.py", "# empty\n")
        assert result == "Written: src/module.py"


# ---------------------------------------------------------------------------
# Task 3: workspace_write — overwrite with line ending preservation (AC 4)
# ---------------------------------------------------------------------------


class TestWorkspaceWriteLineEndingPreservation:
    def test_write_preserves_crlf(self, tmp_path: Path) -> None:
        """Overwriting a CRLF file with LF content normalises to CRLF."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("existing.py", b"line1\r\nline2\r\n")
        write_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_write")
        result = write_fn("existing.py", "new_line1\nnew_line2\n")
        assert result == "Written: existing.py"
        written = fs.read("existing.py")
        assert b"\r\n" in written
        assert b"new_line1" in written

    def test_write_preserves_lf(self, tmp_path: Path) -> None:
        """Overwriting an LF file with LF content keeps LF."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("lf_file.py", b"line1\nline2\n")
        write_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_write")
        result = write_fn("lf_file.py", "new_line1\nnew_line2\n")
        assert result == "Written: lf_file.py"
        written = fs.read("lf_file.py")
        assert b"\r\n" not in written
        assert b"new_line1" in written

    def test_write_overwrite_no_crlf_difference(self, tmp_path: Path) -> None:
        """Overwrite where content already matches line endings writes correctly."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("same.py", b"a\nb\n")
        write_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_write")
        result = write_fn("same.py", "updated\n")
        assert result == "Written: same.py"
        assert b"updated" in fs.read("same.py")

    def test_write_non_utf8_existing_file_does_not_raise(self, tmp_path: Path) -> None:
        """Overwriting a non-UTF-8 binary file writes content as-is without raising."""
        tool, fs = make_wired_tool(tmp_path)
        # Write a file with non-UTF-8 bytes (Windows-1252 / Latin-1 encoded)
        fs.write("binary.dat", b"\xff\xfe binary garbage \x80\x81")
        write_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_write")
        result = write_fn("binary.dat", "replacement\n")
        assert result == "Written: binary.dat"
        assert b"replacement" in fs.read("binary.dat")


# ---------------------------------------------------------------------------
# Task 4: workspace_delete — success and not-found (AC 5, 6)
# ---------------------------------------------------------------------------


class TestWorkspaceDelete:
    def test_delete_existing_file(self, tmp_path: Path) -> None:
        """workspace_delete removes the file and returns confirmation."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("to_delete.py", b"# delete me\n")
        delete_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_delete")
        result = delete_fn("to_delete.py")
        assert result == "Deleted: to_delete.py"
        assert not (fs._root / "to_delete.py").exists()

    def test_delete_nonexistent_file_raises(self, tmp_path: Path) -> None:
        """workspace_delete raises RetriableError for missing files."""
        tool, fs = make_wired_tool(tmp_path)
        delete_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_delete")
        with pytest.raises(RetriableError, match="File not found: nonexistent.py"):
            delete_fn("nonexistent.py")

    def test_delete_returns_deleted_path(self, tmp_path: Path) -> None:
        """Returned string includes the path that was deleted."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("src/old.py", b"pass\n")
        delete_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_delete")
        result = delete_fn("src/old.py")
        assert result == "Deleted: src/old.py"


# ---------------------------------------------------------------------------
# Capability toggling (AC 7)
# ---------------------------------------------------------------------------


class TestCapabilityToggling:
    def test_workspace_delete_disabled_returns_ten_tools(self, tmp_path: Path) -> None:
        """WorkspaceTool(workspace_delete=False) exposes 10 tools."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_delete=False)
            tool.observer(observer)
        tools = tool.get_tools()
        names = [t.__name__ for t in tools]
        assert "workspace_delete" not in names
        assert "workspace_write" in names
        assert len(tools) == 10

    def test_workspace_write_disabled_returns_ten_tools(self, tmp_path: Path) -> None:
        """WorkspaceTool(workspace_write=False) exposes 10 tools."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_write=False)
            tool.observer(observer)
        tools = tool.get_tools()
        names = [t.__name__ for t in tools]
        assert "workspace_write" not in names
        assert "workspace_delete" in names
        assert len(tools) == 10

    def test_both_write_and_delete_disabled_returns_nine_tools(
        self, tmp_path: Path
    ) -> None:
        """WorkspaceTool(workspace_write=False, workspace_delete=False) returns 9 tools."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_write=False, workspace_delete=False)
            tool.observer(observer)
        tools = tool.get_tools()
        names = [t.__name__ for t in tools]
        assert "workspace_write" not in names
        assert "workspace_delete" not in names
        assert "workspace_read" in names
        assert len(tools) == 9

    def test_workspace_delete_false_count(self, tmp_path: Path) -> None:
        """Repeated get_tools() call count is stable."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_delete=False)
            tool.observer(observer)
        assert len(tool.get_tools()) == 10


# ---------------------------------------------------------------------------
# Security: path traversal raises PermissionError
# ---------------------------------------------------------------------------


class TestPathSecurity:
    def test_write_path_traversal_raises(self, tmp_path: Path) -> None:
        """workspace_write raises RetriableError for paths escaping workspace root."""
        tool, fs = make_wired_tool(tmp_path)
        write_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_write")
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            write_fn("../escape.py", "malicious content\n")

    def test_delete_path_traversal_raises(self, tmp_path: Path) -> None:
        """workspace_delete raises RetriableError for paths escaping workspace root."""
        tool, fs = make_wired_tool(tmp_path)
        delete_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_delete")
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            delete_fn("../escape.py")


# ---------------------------------------------------------------------------
# Story 5.5: workspace_edit
# ---------------------------------------------------------------------------


class TestWorkspaceEdit:
    def test_edit_exact_match_returns_diff(self, tmp_path: Path) -> None:
        """workspace_edit finds exact match, applies replacement, returns unified diff."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("main.py", b"old code\nmore code\n")
        edit_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_edit")
        result = edit_fn("main.py", "old code", "new code")
        assert "-old code" in result or "old code" in result
        assert b"new code" in fs.read("main.py")

    def test_edit_fuzzy_match_exercises_matcher(self, tmp_path: Path) -> None:
        """workspace_edit uses EditMatcher cascade — near-match is accepted."""
        tool, fs = make_wired_tool(tmp_path)
        # Write content with slight whitespace variation
        fs.write("main.py", b"def  foo():\n    pass\n")
        edit_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_edit")
        # EditMatcher should handle the double-space via normalisation strategies
        result = edit_fn("main.py", "def foo():\n    pass", "def bar():\n    pass")
        assert not result.startswith("[ERROR]"), f"Expected match but got: {result}"
        assert b"bar" in fs.read("main.py")

    def test_edit_not_found_returns_error(self, tmp_path: Path) -> None:
        """workspace_edit returns [ERROR] when old_string is not found."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("main.py", b"some content\n")
        edit_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_edit")
        result = edit_fn("main.py", "nonexistent string xyz", "replacement")
        assert result.startswith("[ERROR]")
        assert "main.py" in result

    def test_edit_replace_all_replaces_all_occurrences(self, tmp_path: Path) -> None:
        """workspace_edit with replace_all=True replaces all occurrences."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("main.py", b"foo\nfoo\nfoo\n")
        edit_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_edit")
        result = edit_fn("main.py", "foo", "bar", replace_all=True)
        content = fs.read("main.py").decode("utf-8")
        assert content.count("bar") == 3
        assert "foo" not in content
        assert isinstance(result, str)

    def test_edit_replace_all_not_found_returns_error(self, tmp_path: Path) -> None:
        """workspace_edit with replace_all=True returns [ERROR] when not found."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("main.py", b"hello world\n")
        edit_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_edit")
        result = edit_fn("main.py", "xyz not here", "replacement", replace_all=True)
        assert result.startswith("[ERROR]")

    def test_edit_preserves_crlf_line_endings(self, tmp_path: Path) -> None:
        """workspace_edit preserves CRLF line endings after replacement."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("main.py", b"old code\r\nmore code\r\n")
        edit_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_edit")
        edit_fn("main.py", "old code", "new code")
        content = fs.read("main.py")
        assert b"\r\n" in content
        assert b"new code" in content

    def test_edit_disabled_not_in_get_tools(self, tmp_path: Path) -> None:
        """WorkspaceTool(workspace_edit=False) excludes workspace_edit from get_tools()."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_edit=False)
            tool.observer(observer)
        names = [t.__name__ for t in tool.get_tools()]
        assert "workspace_edit" not in names
        assert len(tool.get_tools()) == 10


# ---------------------------------------------------------------------------
# Story 5.5: workspace_multi_edit
# ---------------------------------------------------------------------------


class TestWorkspaceMultiEdit:
    def test_multi_edit_sequential_success(self, tmp_path: Path) -> None:
        """workspace_multi_edit applies edits sequentially and returns combined diff."""
        from akgentic.tool.workspace.edit import EditItem

        tool, fs = make_wired_tool(tmp_path)
        fs.write("a.py", b"x = 1\n")
        fs.write("b.py", b"y = 2\n")
        multi_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_multi_edit")
        result = multi_fn([
            EditItem(path="a.py", old_string="x = 1", new_string="x = 10"),
            EditItem(path="b.py", old_string="y = 2", new_string="y = 20"),
        ])
        assert b"x = 10" in fs.read("a.py")
        assert b"y = 20" in fs.read("b.py")
        assert isinstance(result, str)
        assert not result.startswith("[ERROR]")

    def test_multi_edit_stops_on_failure(self, tmp_path: Path) -> None:
        """workspace_multi_edit stops on first failure; prior edits persist, later ones skipped."""
        from akgentic.tool.workspace.edit import EditItem

        tool, fs = make_wired_tool(tmp_path)
        fs.write("a.py", b"x = 1\n")
        fs.write("b.py", b"y = 2\n")
        fs.write("c.py", b"z = 3\n")
        multi_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_multi_edit")
        result = multi_fn([
            EditItem(path="a.py", old_string="x = 1", new_string="x = 10"),
            EditItem(path="b.py", old_string="DOES NOT EXIST", new_string="whatever"),
            EditItem(path="c.py", old_string="z = 3", new_string="z = 30"),
        ])
        assert b"x = 10" in fs.read("a.py")   # first edit applied
        assert b"y = 2" in fs.read("b.py")    # second not changed (just the error)
        assert b"z = 3" in fs.read("c.py")    # third never reached
        assert result.startswith("[ERROR]")

    def test_multi_edit_replace_all_in_item(self, tmp_path: Path) -> None:
        """workspace_multi_edit applies replace_all=True on a single EditItem."""
        from akgentic.tool.workspace.edit import EditItem

        tool, fs = make_wired_tool(tmp_path)
        fs.write("a.py", b"foo\nfoo\nfoo\n")
        multi_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_multi_edit")
        result = multi_fn([
            EditItem(path="a.py", old_string="foo", new_string="bar", replace_all=True),
        ])
        content = fs.read("a.py").decode("utf-8")
        assert content.count("bar") == 3
        assert "foo" not in content
        assert isinstance(result, str)
        assert not result.startswith("[ERROR]")

    def test_multi_edit_replace_all_not_found_returns_error(self, tmp_path: Path) -> None:
        """workspace_multi_edit with replace_all=True returns [ERROR] when not found."""
        from akgentic.tool.workspace.edit import EditItem

        tool, fs = make_wired_tool(tmp_path)
        fs.write("a.py", b"hello world\n")
        multi_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_multi_edit")
        result = multi_fn([
            EditItem(path="a.py", old_string="xyz not here", new_string="anything", replace_all=True),  # noqa: E501
        ])
        assert result.startswith("[ERROR]")
        assert "a.py" in result

    def test_multi_edit_empty_list_returns_no_changes(self, tmp_path: Path) -> None:
        """workspace_multi_edit with empty edits list returns '(no changes applied)'."""
        tool, _ = make_wired_tool(tmp_path)
        multi_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_multi_edit")
        result = multi_fn([])
        assert result == "(no changes applied)"

    def test_multi_edit_disabled_not_in_get_tools(self, tmp_path: Path) -> None:
        """WorkspaceTool(workspace_multi_edit=False) excludes workspace_multi_edit."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_multi_edit=False)
            tool.observer(observer)
        names = [t.__name__ for t in tool.get_tools()]
        assert "workspace_multi_edit" not in names
        assert len(tool.get_tools()) == 10


# ---------------------------------------------------------------------------
# Story 5.5: workspace_patch
# ---------------------------------------------------------------------------


class TestWorkspacePatch:
    def test_patch_add_new_file(self, tmp_path: Path) -> None:
        """workspace_patch creates a new file from --- /dev/null patch."""
        tool, fs = make_wired_tool(tmp_path)
        patch_text = (
            "--- /dev/null\n"
            "+++ b/new_file.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+line1\n"
            "+line2\n"
        )
        patch_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_patch")
        result = patch_fn(patch_text)
        assert "created: new_file.py" in result
        assert (fs._root / "new_file.py").exists()

    def test_patch_update_existing_file(self, tmp_path: Path) -> None:
        """workspace_patch applies hunks to an existing file and returns updated:."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("existing.py", b"line1\nold_line\nline3\n")
        patch_text = (
            "--- a/existing.py\n"
            "+++ b/existing.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-old_line\n"
            "+new_line\n"
            " line3\n"
        )
        patch_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_patch")
        result = patch_fn(patch_text)
        assert "updated: existing.py" in result
        assert b"new_line" in fs.read("existing.py")

    def test_patch_delete_file(self, tmp_path: Path) -> None:
        """workspace_patch deletes a file from +++ /dev/null patch."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("old_file.py", b"to be deleted\n")
        patch_text = (
            "--- a/old_file.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-to be deleted\n"
        )
        patch_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_patch")
        result = patch_fn(patch_text)
        assert "deleted: old_file.py" in result
        assert not (fs._root / "old_file.py").exists()

    def test_patch_apply_error_returns_error_string(self, tmp_path: Path) -> None:
        """workspace_patch returns [ERROR] when apply_file_patch raises an exception."""
        tool, fs = make_wired_tool(tmp_path)
        # Patch references a non-existent file — apply_file_patch will raise FileNotFoundError
        patch_text = (
            "--- a/missing.py\n"
            "+++ b/missing.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old line\n"
            "+new line\n"
        )
        patch_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_patch")
        result = patch_fn(patch_text)
        assert result.startswith("[ERROR]")
        assert "missing.py" in result

    def test_patch_empty_patch_text_returns_no_patches_applied(self, tmp_path: Path) -> None:
        """workspace_patch with empty/whitespace patch text returns '(no patches applied)'."""
        tool, _ = make_wired_tool(tmp_path)
        patch_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_patch")
        result = patch_fn("")
        assert result == "(no patches applied)"

    def test_patch_disabled_not_in_get_tools(self, tmp_path: Path) -> None:
        """WorkspaceTool(workspace_patch=False) excludes workspace_patch from get_tools()."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_patch=False)
            tool.observer(observer)
        names = [t.__name__ for t in tool.get_tools()]
        assert "workspace_patch" not in names
        assert len(tool.get_tools()) == 10


# ---------------------------------------------------------------------------
# Story 5.5: Capability toggling — default count and individual disabling
# ---------------------------------------------------------------------------


class TestCapabilityTogglingStory55:
    def test_default_count_is_eleven(self, tmp_path: Path) -> None:
        """By default, get_tools() returns all 11 tool callables."""
        tool, _ = make_wired_tool(tmp_path)
        assert len(tool.get_tools()) == 11

    def test_edit_disabled_count_is_ten(self, tmp_path: Path) -> None:
        """WorkspaceTool(workspace_edit=False) returns 10 tools."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_edit=False)
            tool.observer(observer)
        assert len(tool.get_tools()) == 10

    def test_multi_edit_disabled_count_is_ten(self, tmp_path: Path) -> None:
        """WorkspaceTool(workspace_multi_edit=False) returns 10 tools."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_multi_edit=False)
            tool.observer(observer)
        assert len(tool.get_tools()) == 10

    def test_patch_disabled_count_is_ten(self, tmp_path: Path) -> None:
        """WorkspaceTool(workspace_patch=False) returns 10 tools."""
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_patch=False)
            tool.observer(observer)
        assert len(tool.get_tools()) == 10

    def test_all_new_tools_present_by_default(self, tmp_path: Path) -> None:
        """workspace_edit, workspace_multi_edit, workspace_patch all in default get_tools()."""
        tool, _ = make_wired_tool(tmp_path)
        names = [t.__name__ for t in tool.get_tools()]
        assert "workspace_edit" in names
        assert "workspace_multi_edit" in names
        assert "workspace_patch" in names


# ---------------------------------------------------------------------------
# Story 5.6: Filesystem.mkdir()
# ---------------------------------------------------------------------------


class TestFilesystemMkdir:
    def test_mkdir_creates_nested_dirs(self, tmp_path: Path) -> None:
        tool, fs = make_wired_tool(tmp_path)
        fs.mkdir("src/utils/helpers")
        assert (fs._root / "src" / "utils" / "helpers").is_dir()

    def test_mkdir_is_idempotent(self, tmp_path: Path) -> None:
        tool, fs = make_wired_tool(tmp_path)
        (fs._root / "existing").mkdir()
        fs.mkdir("existing")  # must not raise
        assert (fs._root / "existing").is_dir()

    def test_mkdir_traversal_raises(self, tmp_path: Path) -> None:
        tool, fs = make_wired_tool(tmp_path)
        with pytest.raises(PermissionError):
            fs.mkdir("../../escape")


# ---------------------------------------------------------------------------
# Story 5.6: workspace_mkdir tool
# ---------------------------------------------------------------------------


class TestWorkspaceMkdir:
    def test_mkdir_creates_dir_and_returns_confirmation(self, tmp_path: Path) -> None:
        tool, fs = make_wired_tool(tmp_path)
        mkdir_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_mkdir")
        result = mkdir_fn("src/utils")
        assert result == "Created: src/utils"
        assert (fs._root / "src" / "utils").is_dir()

    def test_mkdir_traversal_raises(self, tmp_path: Path) -> None:
        tool, fs = make_wired_tool(tmp_path)
        mkdir_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_mkdir")
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            mkdir_fn("../../escape")

    def test_mkdir_disabled_not_in_get_tools(self, tmp_path: Path) -> None:
        observer, fs = make_observer(tmp_path)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool = WorkspaceTool(workspace_mkdir=False)
            tool.observer(observer)
        names = [t.__name__ for t in tool.get_tools()]
        assert "workspace_mkdir" not in names
        assert len(tool.get_tools()) == 10

    def test_default_count_is_eleven(self, tmp_path: Path) -> None:
        """By default, get_tools() returns all 11 tool callables."""
        tool, _ = make_wired_tool(tmp_path)
        assert len(tool.get_tools()) == 11


# ---------------------------------------------------------------------------
# Story 5.7: RetriableError wrapping in write tools
# ---------------------------------------------------------------------------


class TestRetriableErrorWorkspaceTool:
    def test_write_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_write raises RetriableError when backend.write raises PermissionError."""
        tool, fs = make_wired_tool(tmp_path)
        write_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_write")
        with patch.object(fs, "write", side_effect=PermissionError("escaped")):
            with pytest.raises(RetriableError, match="Path escapes workspace root"):
                write_fn("src/file.py", "content")

    def test_delete_file_not_found_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_delete raises RetriableError for non-existent files."""
        tool, fs = make_wired_tool(tmp_path)
        delete_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_delete")
        with pytest.raises(RetriableError, match="File not found: nonexistent.txt"):
            delete_fn("nonexistent.txt")

    def test_delete_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_delete raises RetriableError for path escaping workspace."""
        tool, fs = make_wired_tool(tmp_path)
        delete_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_delete")
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            delete_fn("../../escape")

    def test_edit_file_not_found_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_edit raises RetriableError for non-existent files."""
        tool, fs = make_wired_tool(tmp_path)
        edit_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_edit")
        with pytest.raises(RetriableError, match="File not found: nonexistent.txt"):
            edit_fn("nonexistent.txt", "x", "y")

    def test_edit_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_edit raises RetriableError when backend raises PermissionError."""
        tool, fs = make_wired_tool(tmp_path)
        edit_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_edit")
        with patch.object(fs, "read", side_effect=PermissionError("escaped")):
            with pytest.raises(RetriableError, match="Path escapes workspace root"):
                edit_fn("src/file.py", "old", "new")

    def test_multi_edit_file_not_found_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_multi_edit raises RetriableError for non-existent files."""
        tool, fs = make_wired_tool(tmp_path)
        multi_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_multi_edit")
        with pytest.raises(RetriableError, match="File not found: nonexistent.txt"):
            multi_fn([EditItem(path="nonexistent.txt", old_string="x", new_string="y")])

    def test_patch_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_patch raises RetriableError when PermissionError escapes inner handler."""
        tool, fs = make_wired_tool(tmp_path)
        patch_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_patch")
        # parse_patch is called before any per-patch try/except, so a PermissionError
        # raised there will be caught by the outer except PermissionError handler.
        with patch(
            "akgentic.tool.workspace.tool.parse_patch",
            side_effect=PermissionError("path escapes workspace root"),
        ):
            with pytest.raises(RetriableError, match="Path escapes workspace root"):
                patch_fn("--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n")

    def test_multi_edit_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_multi_edit raises RetriableError when backend raises PermissionError."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("src/file.py", b"old\n")
        multi_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_multi_edit")
        with patch.object(fs, "write", side_effect=PermissionError("escaped")):
            with pytest.raises(RetriableError, match="Path escapes workspace root"):
                multi_fn([EditItem(path="src/file.py", old_string="old", new_string="new")])

    def test_mkdir_permission_error_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_mkdir raises RetriableError for path escaping workspace."""
        tool, fs = make_wired_tool(tmp_path)
        mkdir_fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_mkdir")
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            mkdir_fn("../../escape")


# ---------------------------------------------------------------------------
# Story 7.1: read_only parameter — AC 1, 3, 4, 5, 6, 7
# ---------------------------------------------------------------------------


class TestReadOnlyParameter:
    """Tests for WorkspaceTool.read_only field (story 7.1 ACs 3, 4, 5, 6, 7)."""

    def test_name_is_workspace(self) -> None:
        """AC 6: WorkspaceTool(read_only=True).name == 'Workspace'."""
        assert WorkspaceTool(read_only=True).name == "Workspace"

    def test_name_default_is_workspace(self) -> None:
        """AC 6: WorkspaceTool().name == 'Workspace'."""
        assert WorkspaceTool().name == "Workspace"

    def test_read_only_default_is_false(self) -> None:
        """AC 1: Default read_only is False."""
        assert WorkspaceTool().read_only is False

    def test_read_only_true_get_tools_returns_five_tools(self, tmp_path: Path) -> None:
        """AC 3: read_only=True → 5 read tools: read, list, glob, grep, view."""
        observer, fs = make_observer(tmp_path)
        tool = WorkspaceTool(read_only=True)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        tools = tool.get_tools()
        assert len(tools) == 5
        names = [t.__name__ for t in tools]
        assert "workspace_read" in names
        assert "workspace_list" in names
        assert "workspace_glob" in names
        assert "workspace_grep" in names
        assert "workspace_view" in names
        # No write tools
        assert "workspace_write" not in names
        assert "workspace_delete" not in names
        assert "workspace_edit" not in names
        assert "workspace_multi_edit" not in names
        assert "workspace_patch" not in names
        assert "workspace_mkdir" not in names

    def test_read_only_false_get_tools_returns_eleven_tools(self, tmp_path: Path) -> None:
        """AC 4: read_only=False (default) → 11 tools: 5 read + 6 write."""
        tool, fs = make_wired_tool(tmp_path)
        tools = tool.get_tools()
        assert len(tools) == 11
        names = [t.__name__ for t in tools]
        # Read tools
        assert "workspace_read" in names
        assert "workspace_list" in names
        assert "workspace_glob" in names
        assert "workspace_grep" in names
        assert "workspace_view" in names
        # Write tools
        assert "workspace_write" in names
        assert "workspace_delete" in names
        assert "workspace_edit" in names
        assert "workspace_multi_edit" in names
        assert "workspace_patch" in names
        assert "workspace_mkdir" in names

    def test_model_dump_roundtrip_read_only_true(self) -> None:
        """AC 5: WorkspaceTool(read_only=True).model_dump() round-trips."""
        original = WorkspaceTool(read_only=True)
        dumped = original.model_dump()
        restored = WorkspaceTool.model_validate(dumped)
        assert restored.read_only is True
        assert restored.name == "Workspace"

    def test_model_dump_roundtrip_read_only_false(self) -> None:
        """AC 5: WorkspaceTool(read_only=False).model_dump() round-trips."""
        original = WorkspaceTool(read_only=False)
        dumped = original.model_dump()
        restored = WorkspaceTool.model_validate(dumped)
        assert restored.read_only is False
        assert restored.name == "Workspace"

    def test_read_only_in_model_dump(self) -> None:
        """AC 5: read_only field appears in model_dump() output."""
        dumped = WorkspaceTool(read_only=True).model_dump()
        assert "read_only" in dumped
        assert dumped["read_only"] is True

    def test_observer_raises_when_orchestrator_none_references_workspacetool(
        self, tmp_path: Path
    ) -> None:
        """AC 7: observer() raises ValueError referencing WorkspaceTool (not WorkspaceReadTool)."""
        observer = MagicMock()
        observer.orchestrator = None
        tool = WorkspaceTool(read_only=True)
        with pytest.raises(ValueError, match="WorkspaceTool"):
            tool.observer(observer)

    def test_workspace_property_raises_references_workspacetool(self) -> None:
        """AC 7: workspace property RuntimeError references WorkspaceTool."""
        tool = WorkspaceTool(read_only=True)
        with pytest.raises(RuntimeError, match="WorkspaceTool"):
            _ = tool.workspace

    def test_read_only_true_overrides_write_capability_fields(self, tmp_path: Path) -> None:
        """read_only=True excludes write tools even when their capability fields are True."""
        observer, fs = make_observer(tmp_path)
        tool = WorkspaceTool(
            read_only=True,
            workspace_write=True,   # explicitly enabled
            workspace_delete=True,  # explicitly enabled
            workspace_edit=True,    # explicitly enabled
        )
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        names = [t.__name__ for t in tool.get_tools()]
        # read_only gate must override individual capability fields
        assert "workspace_write" not in names
        assert "workspace_delete" not in names
        assert "workspace_edit" not in names
        # Read tools still present
        assert "workspace_read" in names
        assert len(tool.get_tools()) == 5


# ---------------------------------------------------------------------------
# _normalize_glob_pattern — unit tests
# ---------------------------------------------------------------------------


class TestNormalizeGlobPattern:
    """Unit tests for _normalize_glob_pattern."""

    def test_valid_recursive_pattern_unchanged(self) -> None:
        """'**/*.py' is already valid — must not be modified."""
        assert _normalize_glob_pattern("**/*.py") == "**/*.py"

    def test_valid_prefixed_recursive_pattern_unchanged(self) -> None:
        """'src/**/*.ts' is already valid — must not be modified."""
        assert _normalize_glob_pattern("src/**/*.ts") == "src/**/*.ts"

    def test_bare_star_pattern_unchanged(self) -> None:
        """'*.py' has no '**' — must pass through unchanged."""
        assert _normalize_glob_pattern("*.py") == "*.py"

    def test_double_star_alone_unchanged(self) -> None:
        """'**' by itself is valid — must not be modified."""
        assert _normalize_glob_pattern("**") == "**"

    def test_embedded_double_star_no_slash_fixed(self) -> None:
        """'**.py' → '**/*.py': common LLM mistake that Python 3.12 rejects."""
        assert _normalize_glob_pattern("**.py") == "**/*.py"

    def test_embedded_double_star_in_subdir_fixed(self) -> None:
        """'src/**.py' → 'src/**/*.py'."""
        assert _normalize_glob_pattern("src/**.py") == "src/**/*.py"

    def test_embedded_double_star_mid_path_fixed(self) -> None:
        """'src/**.ts' → 'src/**/*.ts'."""
        assert _normalize_glob_pattern("src/**.ts") == "src/**/*.ts"

    def test_result_accepted_by_pathlib(self, tmp_path: Path) -> None:
        """Normalized pattern must not raise ValueError in pathlib.glob()."""
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "file.py").write_text("x")
        pattern = _normalize_glob_pattern("**.py")
        # Must not raise
        matches = list(tmp_path.glob(pattern))
        assert any(m.name == "file.py" for m in matches)


# ---------------------------------------------------------------------------
# workspace_glob — integration tests
# ---------------------------------------------------------------------------


class TestWorkspaceGlob:
    """Integration tests for the workspace_glob tool callable."""

    def _glob_fn(self, tool: WorkspaceTool) -> object:
        return next(t for t in tool.get_tools() if t.__name__ == "workspace_glob")

    def test_valid_pattern_finds_files(self, tmp_path: Path) -> None:
        """workspace_glob returns matching files for a well-formed pattern."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("src/foo.py", b"x")
        fs.write("src/bar.py", b"y")
        glob_fn = self._glob_fn(tool)
        result = glob_fn("**/*.py")  # type: ignore[assignment]
        assert "src/foo.py" in result
        assert "src/bar.py" in result

    def test_invalid_pattern_double_star_no_slash_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        """workspace_glob normalizes '**.py' instead of crashing with ValueError."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("src/foo.py", b"x")
        glob_fn = self._glob_fn(tool)
        # Must not raise ValueError: '**' can only be an entire path component
        result = glob_fn("**.py")  # type: ignore[assignment]
        assert "src/foo.py" in result

    def test_no_match_returns_sentinel(self, tmp_path: Path) -> None:
        """workspace_glob returns 'No files found.' when nothing matches."""
        tool, _ = make_wired_tool(tmp_path)
        glob_fn = self._glob_fn(tool)
        assert glob_fn("**/*.nonexistent") == "No files found."  # type: ignore[assignment]

    def test_path_escape_raises_retriable_error(self, tmp_path: Path) -> None:
        """workspace_glob raises RetriableError when path escapes workspace root."""
        tool, _ = make_wired_tool(tmp_path)
        glob_fn = self._glob_fn(tool)
        with pytest.raises(RetriableError, match="Path escapes workspace root"):
            glob_fn("**/*.py", path="../../escape")  # type: ignore[assignment]

    def test_brace_expansion_with_embedded_double_star(self, tmp_path: Path) -> None:
        """workspace_glob handles '**.{py,ts}' — brace expand then normalize."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("src/foo.py", b"x")
        fs.write("src/bar.ts", b"y")
        glob_fn = self._glob_fn(tool)
        result = glob_fn("**.{py,ts}")  # type: ignore[assignment]
        assert "src/foo.py" in result
        assert "src/bar.ts" in result

    def test_glob_with_path_argument_returns_relative_paths(self, tmp_path: Path) -> None:
        """workspace_glob with path='subdir' returns relative paths without ValueError."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("subdir/file.py", b"content")
        fs.write("other/ignore.py", b"other")
        glob_fn = self._glob_fn(tool)
        result = glob_fn("**/*.py", path="subdir")  # type: ignore[assignment]
        assert "subdir/file.py" in result
        assert "other/ignore.py" not in result

    def test_glob_with_path_traversal_rejected(self, tmp_path: Path) -> None:
        """workspace_glob rejects path arguments that escape the workspace root."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("legit/file.py", b"ok")
        glob_fn = self._glob_fn(tool)
        with pytest.raises(RetriableError):
            glob_fn("**/*.py", path="../escape")  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Regression: workspace_grep with path argument (Story 14.1, AC #3)
# ---------------------------------------------------------------------------


class TestWorkspaceGrepRegression:
    """Regression tests for workspace_grep with path argument (ADR-021)."""

    def _grep_fn(self, tool: WorkspaceTool) -> object:
        return next(t for t in tool.get_tools() if t.__name__ == "workspace_grep")

    def test_grep_with_path_argument_returns_relative_paths(self, tmp_path: Path) -> None:
        """workspace_grep with path='subdir' returns relative paths without ValueError."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("subdir/file.py", b"search_term_here\n")
        fs.write("other/ignore.py", b"no match\n")
        grep_fn = self._grep_fn(tool)
        result = grep_fn("search_term_here", path="subdir")  # type: ignore[assignment]
        assert "subdir/file.py" in result
        assert "search_term_here" in result
        assert "other/ignore.py" not in result

    def test_grep_with_path_traversal_rejected(self, tmp_path: Path) -> None:
        """workspace_grep rejects path arguments that escape the workspace root."""
        tool, fs = make_wired_tool(tmp_path)
        fs.write("legit/file.py", b"ok\n")
        grep_fn = self._grep_fn(tool)
        with pytest.raises(RetriableError):
            grep_fn("ok", path="../escape")  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Regression: Filesystem._root is absolute after construction (Story 14.1, AC #1)
# ---------------------------------------------------------------------------


class TestFilesystemRootAbsolute:
    """Regression test ensuring _root is always absolute after construction."""

    def test_filesystem_root_is_absolute_after_construction(self, tmp_path: Path) -> None:
        """Filesystem constructed with relative base_path has absolute _root."""
        # Use tmp_path to avoid creating directories in CWD
        relative_base = str(tmp_path / "rel")
        fs = Filesystem(relative_base, "workspace")
        assert fs._root.is_absolute()
