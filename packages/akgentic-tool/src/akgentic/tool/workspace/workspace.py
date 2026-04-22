"""Workspace Protocol, Filesystem implementation, and get_workspace() factory.

Provides a secure, team-scoped filesystem backend for workspace tools.
All path operations validate that the resolved path stays within the workspace root
to prevent directory traversal attacks.

The workspace root is derived from the ``AKGENTIC_WORKSPACES_ROOT`` environment
variable (default: ``./workspaces``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class FileEntry(BaseModel):
    """Metadata for a single filesystem entry inside a workspace."""

    name: str
    is_dir: bool
    size: int  # bytes; 0 for directories


@runtime_checkable
class Workspace(Protocol):
    """Protocol that all workspace backends must satisfy."""

    def read(self, path: str) -> bytes: ...

    def read_bytes(self, path: str) -> bytes: ...

    def write(self, path: str, data: bytes) -> None: ...

    def delete(self, path: str) -> None: ...

    def list(self, path: str = "") -> list[FileEntry]: ...

    def mkdir(self, path: str) -> None: ...


class Filesystem:
    """Local filesystem backend for a single team workspace.

    All paths are anchored to ``<base_path>/<workspace_name>``.  Any attempt to
    escape that root (via ``../`` traversal or symlinks that resolve outside) is
    rejected with :exc:`PermissionError`.
    """

    def __init__(self, base_path: str, workspace_name: str) -> None:
        self._root = (Path(base_path) / workspace_name).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _validate_path(self, path: str) -> Path:
        """Resolve *path* relative to the workspace root and validate it.

        Uses :meth:`Path.is_relative_to` (Python 3.9+) for component-level
        comparison, which prevents false positives when a sibling workspace name
        begins with the same characters (e.g. ``team-1`` vs ``team-11``).

        Raises:
            PermissionError: if the resolved path escapes the workspace root.
        """
        resolved = (self._root / path).resolve()
        if not resolved.is_relative_to(self._root):
            raise PermissionError(f"Path '{path}' escapes workspace root")
        return resolved

    def read(self, path: str) -> bytes:
        """Return the contents of *path* as bytes.

        Raises:
            FileNotFoundError: if the file does not exist.
            PermissionError: if *path* escapes the workspace root.
        """
        resolved = self._validate_path(path)
        return resolved.read_bytes()

    def read_bytes(self, path: str) -> bytes:
        """Return the raw bytes of *path* with no decoding or pagination.

        Raises:
            FileNotFoundError: if the file does not exist.
            PermissionError: if *path* escapes the workspace root.
        """
        resolved = self._validate_path(path)
        return resolved.read_bytes()

    def write(self, path: str, data: bytes) -> None:
        """Write *data* to *path*, creating missing parent directories.

        Raises:
            PermissionError: if *path* escapes the workspace root.
        """
        resolved = self._validate_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(data)

    def delete(self, path: str) -> None:
        """Delete the file at *path*.

        Raises:
            FileNotFoundError: if the file does not exist.
            PermissionError: if *path* escapes the workspace root.
        """
        resolved = self._validate_path(path)
        resolved.unlink()

    def list(self, path: str = "") -> list[FileEntry]:
        """List immediate children of *path* (non-recursive).

        Returns directories first (alphabetically), then files (alphabetically).
        ``size`` is 0 for directories and the file byte count for regular files.

        Raises:
            PermissionError: if *path* escapes the workspace root.
        """
        resolved = self._validate_path(path) if path else self._root
        entries: list[FileEntry] = []
        dirs: list[FileEntry] = []
        files: list[FileEntry] = []
        for child in resolved.iterdir():
            if child.is_dir():
                dirs.append(FileEntry(name=child.name, is_dir=True, size=0))
            else:
                files.append(FileEntry(name=child.name, is_dir=False, size=child.stat().st_size))
        dirs.sort(key=lambda e: e.name)
        files.sort(key=lambda e: e.name)
        entries = dirs + files
        return entries

    def mkdir(self, path: str) -> None:
        """Create directory *path* and all missing parents within the workspace.

        Idempotent — calling on an existing directory is a no-op.

        Raises:
            PermissionError: if *path* escapes the workspace root.
        """
        resolved = self._validate_path(path)
        resolved.mkdir(parents=True, exist_ok=True)


def get_workspace(workspace_name: str) -> Filesystem:
    """Return a :class:`Filesystem` for *workspace_name* rooted at the configured base.

    The base path is read from the ``AKGENTIC_WORKSPACES_ROOT`` environment
    variable.  When the variable is unset the default ``./workspaces`` is used.

    Args:
        workspace_name: Team-scoped workspace directory name (e.g. ``"team-1"``).

    Returns:
        A :class:`Filesystem` anchored at ``<AKGENTIC_WORKSPACES_ROOT>/<workspace_name>``.
    """
    base_path = os.environ.get("AKGENTIC_WORKSPACES_ROOT", "./workspaces")
    return Filesystem(base_path=base_path, workspace_name=workspace_name)
