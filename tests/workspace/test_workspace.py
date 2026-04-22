"""Tests for akgentic.tool.workspace.workspace module (Story 5.1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from akgentic.tool.workspace.workspace import (
    FileEntry,
    Filesystem,
    Workspace,
    get_workspace,
)

# ---------------------------------------------------------------------------
# FileEntry model
# ---------------------------------------------------------------------------


class TestFileEntry:
    def test_file_entry_fields(self) -> None:
        entry = FileEntry(name="main.py", is_dir=False, size=42)
        assert entry.name == "main.py"
        assert entry.is_dir is False
        assert entry.size == 42

    def test_file_entry_directory(self) -> None:
        entry = FileEntry(name="src", is_dir=True, size=0)
        assert entry.is_dir is True
        assert entry.size == 0


# ---------------------------------------------------------------------------
# Workspace Protocol — runtime_checkable
# ---------------------------------------------------------------------------


class TestWorkspaceProtocol:
    def test_filesystem_satisfies_workspace_protocol(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        assert isinstance(fs, Workspace)


# ---------------------------------------------------------------------------
# Filesystem construction
# ---------------------------------------------------------------------------


class TestFilesystemConstruction:
    def test_root_is_base_path_slash_workspace_name(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        assert fs._root == tmp_path / "team-1"

    def test_root_directory_is_created(self, tmp_path: Path) -> None:
        root = tmp_path / "team-1"
        assert not root.exists()
        Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        assert root.is_dir()

    def test_construction_is_idempotent(self, tmp_path: Path) -> None:
        """Creating Filesystem twice does not raise (exist_ok=True)."""
        Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        Filesystem(base_path=str(tmp_path), workspace_name="team-1")  # should not raise


# ---------------------------------------------------------------------------
# _validate_path
# ---------------------------------------------------------------------------


class TestValidatePath:
    def test_valid_relative_path_returns_resolved_path(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        result = fs._validate_path("foo/bar.txt")
        assert result == (tmp_path / "team-1" / "foo" / "bar.txt").resolve()

    def test_traversal_raises_permission_error(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        with pytest.raises(PermissionError, match="escapes workspace root"):
            fs._validate_path("../../etc/passwd")

    def test_traversal_via_double_dot_in_middle_raises(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        with pytest.raises(PermissionError):
            fs._validate_path("src/../../../../../../etc/hosts")

    def test_sibling_workspace_name_prefix_raises(self, tmp_path: Path) -> None:
        """Sibling workspace 'team-11' must not pass validation for workspace 'team-1'.

        A naive ``str.startswith`` check would incorrectly allow this because
        ``str('/workspaces/team-11').startswith('/workspaces/team-1')`` is True.
        """
        sibling = tmp_path / "team-11"
        sibling.mkdir()
        (sibling / "secret.txt").write_bytes(b"secret")
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        # Construct a path that resolves to the sibling workspace
        sibling_relative = "../team-11/secret.txt"
        with pytest.raises(PermissionError, match="escapes workspace root"):
            fs._validate_path(sibling_relative)

    def test_absolute_path_injection_raises(self, tmp_path: Path) -> None:
        """An absolute path supplied as the path argument must be rejected."""
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        with pytest.raises(PermissionError, match="escapes workspace root"):
            fs._validate_path("/etc/passwd")


# ---------------------------------------------------------------------------
# Filesystem.read
# ---------------------------------------------------------------------------


class TestFilesystemRead:
    def test_read_returns_bytes(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        root = tmp_path / "team-1"
        (root / "hello.txt").write_bytes(b"hello world")
        assert fs.read("hello.txt") == b"hello world"

    def test_read_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        with pytest.raises(FileNotFoundError):
            fs.read("nonexistent.txt")

    def test_read_validates_path(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        with pytest.raises(PermissionError):
            fs.read("../../etc/passwd")


# ---------------------------------------------------------------------------
# Filesystem.write
# ---------------------------------------------------------------------------


class TestFilesystemWrite:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        fs.write("output.txt", b"data")
        assert (tmp_path / "team-1" / "output.txt").read_bytes() == b"data"

    def test_write_creates_missing_parent_dirs(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        fs.write("src/subdir/file.py", b"# code")
        assert (tmp_path / "team-1" / "src" / "subdir" / "file.py").read_bytes() == b"# code"

    def test_write_overwrites_existing_file(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        fs.write("a.txt", b"first")
        fs.write("a.txt", b"second")
        assert (tmp_path / "team-1" / "a.txt").read_bytes() == b"second"

    def test_write_validates_path(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        with pytest.raises(PermissionError):
            fs.write("../../evil.txt", b"x")


# ---------------------------------------------------------------------------
# Filesystem.delete
# ---------------------------------------------------------------------------


class TestFilesystemDelete:
    def test_delete_removes_file(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        root = tmp_path / "team-1"
        target = root / "to_delete.txt"
        target.write_bytes(b"bye")
        fs.delete("to_delete.txt")
        assert not target.exists()

    def test_delete_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        with pytest.raises(FileNotFoundError):
            fs.delete("ghost.txt")

    def test_delete_validates_path(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        with pytest.raises(PermissionError):
            fs.delete("../../etc/passwd")


# ---------------------------------------------------------------------------
# Filesystem.list
# ---------------------------------------------------------------------------


class TestFilesystemList:
    def test_list_root_returns_file_entries(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        root = tmp_path / "team-1"
        (root / "file.txt").write_bytes(b"abc")
        (root / "subdir").mkdir()
        entries = fs.list("")
        names = [e.name for e in entries]
        assert "file.txt" in names
        assert "subdir" in names

    def test_list_dirs_come_before_files(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        root = tmp_path / "team-1"
        (root / "z_file.txt").write_bytes(b"z")
        (root / "a_dir").mkdir()
        entries = fs.list("")
        assert entries[0].is_dir is True
        assert entries[0].name == "a_dir"

    def test_list_file_size_matches_content(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        root = tmp_path / "team-1"
        (root / "data.bin").write_bytes(b"12345")
        entries = fs.list("")
        file_entry = next(e for e in entries if e.name == "data.bin")
        assert file_entry.size == 5

    def test_list_directory_size_is_zero(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        root = tmp_path / "team-1"
        sub = root / "mydir"
        sub.mkdir()
        (sub / "inner.txt").write_bytes(b"content")
        entries = fs.list("")
        dir_entry = next(e for e in entries if e.name == "mydir")
        assert dir_entry.size == 0

    def test_list_empty_directory(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        entries = fs.list("")
        assert entries == []

    def test_list_subdirectory(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        root = tmp_path / "team-1"
        sub = root / "src"
        sub.mkdir()
        (sub / "main.py").write_bytes(b"pass")
        entries = fs.list("src")
        assert len(entries) == 1
        assert entries[0].name == "main.py"

    def test_list_is_non_recursive(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        root = tmp_path / "team-1"
        nested = root / "a" / "b"
        nested.mkdir(parents=True)
        (nested / "deep.txt").write_bytes(b"deep")
        entries = fs.list("")
        # Should only see "a", not "b" or "deep.txt"
        assert len(entries) == 1
        assert entries[0].name == "a"

    def test_list_validates_path(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        with pytest.raises(PermissionError):
            fs.list("../../etc")

    def test_list_alphabetical_within_dirs_and_files(self, tmp_path: Path) -> None:
        fs = Filesystem(base_path=str(tmp_path), workspace_name="team-1")
        root = tmp_path / "team-1"
        (root / "z.txt").write_bytes(b"z")
        (root / "a.txt").write_bytes(b"a")
        (root / "m_dir").mkdir()
        (root / "b_dir").mkdir()
        entries = fs.list("")
        dir_names = [e.name for e in entries if e.is_dir]
        file_names = [e.name for e in entries if not e.is_dir]
        assert dir_names == ["b_dir", "m_dir"]
        assert file_names == ["a.txt", "z.txt"]


# ---------------------------------------------------------------------------
# get_workspace factory
# ---------------------------------------------------------------------------


class TestGetWorkspace:
    def test_get_workspace_default_root_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_workspace() uses ./workspaces when AKGENTIC_WORKSPACES_ROOT is not set."""
        monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
        with patch("akgentic.tool.workspace.workspace.Filesystem") as mock_fs_cls:
            mock_instance = MagicMock()
            mock_fs_cls.return_value = mock_instance
            result = get_workspace("team-1")
            mock_fs_cls.assert_called_once_with(base_path="./workspaces", workspace_name="team-1")
            assert result is mock_instance

    def test_get_workspace_custom_root_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_workspace() uses AKGENTIC_WORKSPACES_ROOT value when set."""
        monkeypatch.setenv("AKGENTIC_WORKSPACES_ROOT", "/workspaces")
        with patch("akgentic.tool.workspace.workspace.Filesystem") as mock_fs_cls:
            mock_instance = MagicMock()
            mock_fs_cls.return_value = mock_instance
            result = get_workspace("team-1")
            mock_fs_cls.assert_called_once_with(base_path="/workspaces", workspace_name="team-1")
            assert result is mock_instance

    def test_get_workspace_custom_tmp_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_workspace() passes any arbitrary AKGENTIC_WORKSPACES_ROOT value through."""
        monkeypatch.setenv("AKGENTIC_WORKSPACES_ROOT", "/tmp/test-workspaces")
        with patch("akgentic.tool.workspace.workspace.Filesystem") as mock_fs_cls:
            mock_instance = MagicMock()
            mock_fs_cls.return_value = mock_instance
            result = get_workspace("team-2")
            mock_fs_cls.assert_called_once_with(
                base_path="/tmp/test-workspaces", workspace_name="team-2"
            )
            assert result is mock_instance

    def test_get_workspace_workspace_name_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_workspace() passes workspace_name verbatim to Filesystem."""
        monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
        with patch("akgentic.tool.workspace.workspace.Filesystem") as mock_fs_cls:
            mock_instance = MagicMock()
            mock_fs_cls.return_value = mock_instance
            get_workspace("my-special-team")
            mock_fs_cls.assert_called_once_with(
                base_path="./workspaces", workspace_name="my-special-team"
            )
