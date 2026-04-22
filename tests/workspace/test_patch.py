"""Tests for parse_patch and apply_file_patch (Story 5.3)."""

from __future__ import annotations

from pathlib import Path

from akgentic.tool.workspace.edit import FilePatch, Hunk, apply_file_patch, parse_patch
from akgentic.tool.workspace.workspace import Filesystem

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,3 @@
 line1
-old_line
+new_line
 line3
"""

MULTI_FILE_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,2 +1,2 @@
-foo_old
+foo_new
 common
--- a/src/bar.py
+++ b/src/bar.py
@@ -1,2 +1,2 @@
-bar_old
+bar_new
 other
"""

ADD_FILE_DIFF = """\
--- /dev/null
+++ b/src/new_file.py
@@ -0,0 +1,2 @@
+first_line
+second_line
"""

DELETE_FILE_DIFF = """\
--- a/src/old_file.py
+++ /dev/null
@@ -1,2 +0,0 @@
-removed_line1
-removed_line2
"""

MULTI_HUNK_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,3 @@
 line1
-old1
+new1
 line3
@@ -5,3 +5,3 @@
 line5
-old2
+new2
 line7
"""


# ---------------------------------------------------------------------------
# parse_patch — single file
# ---------------------------------------------------------------------------


def test_parse_patch_single_file() -> None:
    patches = parse_patch(SIMPLE_DIFF)
    assert len(patches) == 1
    assert patches[0].path == "src/foo.py"
    assert len(patches[0].hunks) == 1
    hunk = patches[0].hunks[0]
    assert hunk.old_start == 1
    assert hunk.old_count == 3
    assert hunk.new_start == 1
    assert hunk.new_count == 3


def test_parse_patch_single_file_hunk_lines() -> None:
    patches = parse_patch(SIMPLE_DIFF)
    hunk = patches[0].hunks[0]
    assert " line1" in hunk.lines
    assert "-old_line" in hunk.lines
    assert "+new_line" in hunk.lines


# ---------------------------------------------------------------------------
# parse_patch — multi-file
# ---------------------------------------------------------------------------


def test_parse_patch_multi_file() -> None:
    patches = parse_patch(MULTI_FILE_DIFF)
    assert len(patches) == 2
    assert patches[0].path == "src/foo.py"
    assert patches[1].path == "src/bar.py"


def test_parse_patch_multi_file_paths() -> None:
    patches = parse_patch(MULTI_FILE_DIFF)
    assert patches[0].hunks[0].lines[0] == "-foo_old"
    assert patches[1].hunks[0].lines[0] == "-bar_old"


# ---------------------------------------------------------------------------
# parse_patch — hunk header parsing
# ---------------------------------------------------------------------------


def test_parse_patch_hunk_header_values() -> None:
    patches = parse_patch(SIMPLE_DIFF)
    hunk = patches[0].hunks[0]
    assert hunk.old_start == 1
    assert hunk.old_count == 3
    assert hunk.new_start == 1
    assert hunk.new_count == 3


def test_parse_patch_add_file_path() -> None:
    patches = parse_patch(ADD_FILE_DIFF)
    assert len(patches) == 1
    assert patches[0].path == "src/new_file.py"


def test_parse_patch_delete_file_path() -> None:
    patches = parse_patch(DELETE_FILE_DIFF)
    assert len(patches) == 1
    assert patches[0].path == "/dev/null"


def test_parse_patch_multi_hunk() -> None:
    patches = parse_patch(MULTI_HUNK_DIFF)
    assert len(patches) == 1
    assert len(patches[0].hunks) == 2
    assert patches[0].hunks[0].old_start == 1
    assert patches[0].hunks[1].old_start == 5


def test_parse_patch_hunk_header_missing_count_defaults_to_one() -> None:
    """Cover the branch where hunk header omits count (e.g. @@ -1 +1 @@)."""
    # Unified diff spec: "@@ -start +start @@" without count means count=1
    diff_no_count = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1 +1 @@
-old_line
+new_line
"""
    patches = parse_patch(diff_no_count)
    assert len(patches) == 1
    hunk = patches[0].hunks[0]
    assert hunk.old_start == 1
    assert hunk.old_count == 1   # defaulted from missing count
    assert hunk.new_start == 1
    assert hunk.new_count == 1   # defaulted from missing count


# ---------------------------------------------------------------------------
# apply_file_patch — update operation
# ---------------------------------------------------------------------------


def test_apply_file_patch_update(tmp_path: Path) -> None:
    fs = Filesystem(str(tmp_path), "ws")
    fs.write("foo.py", b"line1\nline2\nline3\n")
    patch = FilePatch(
        path="foo.py",
        hunks=[
            Hunk(
                old_start=2,
                old_count=1,
                new_start=2,
                new_count=1,
                lines=["-line2", "+new_line"],
            )
        ],
    )
    apply_file_patch(fs, patch)
    result = fs.read("foo.py").decode()
    assert "new_line" in result
    assert "line2" not in result


def test_apply_file_patch_update_only_targeted_lines(tmp_path: Path) -> None:
    fs = Filesystem(str(tmp_path), "ws")
    fs.write("foo.py", b"line1\nline2\nline3\n")
    patch = FilePatch(
        path="foo.py",
        hunks=[
            Hunk(
                old_start=2,
                old_count=1,
                new_start=2,
                new_count=1,
                lines=["-line2", "+replaced"],
            )
        ],
    )
    apply_file_patch(fs, patch)
    result = fs.read("foo.py").decode()
    assert "line1" in result
    assert "line3" in result
    assert "replaced" in result


# ---------------------------------------------------------------------------
# apply_file_patch — add operation (all-add patch)
# ---------------------------------------------------------------------------


def test_apply_file_patch_add_new_file(tmp_path: Path) -> None:
    fs = Filesystem(str(tmp_path), "ws")
    patch = FilePatch(
        path="new_file.py",
        hunks=[
            Hunk(
                old_start=0,
                old_count=0,
                new_start=1,
                new_count=2,
                lines=["+first_line", "+second_line"],
            )
        ],
    )
    apply_file_patch(fs, patch)
    result = fs.read("new_file.py").decode()
    assert "first_line" in result
    assert "second_line" in result
    # New files must end with a trailing newline (Unix convention)
    assert result.endswith("\n")


def test_apply_file_patch_empty_hunks_does_not_create_file(tmp_path: Path) -> None:
    """Guard against empty hunk list being treated as new-file (all() bug)."""
    fs = Filesystem(str(tmp_path), "ws")
    # Write an existing file first
    fs.write("existing.py", b"content\n")
    patch = FilePatch(path="existing.py", hunks=[])
    # Empty hunk list → is_new_file must be False, update path is taken,
    # splitlines on existing content with no hunks → file written back unchanged
    apply_file_patch(fs, patch)
    result = fs.read("existing.py").decode()
    assert "content" in result


# ---------------------------------------------------------------------------
# apply_file_patch — delete operation (handled by caller — /dev/null sentinel)
# ---------------------------------------------------------------------------


def test_apply_file_patch_delete_sentinel(tmp_path: Path) -> None:
    """apply_file_patch is NOT called for /dev/null path in tool.py.

    This test verifies that callers should check path == "/dev/null" before
    calling apply_file_patch. parse_patch correctly returns path="/dev/null"
    for delete diffs.
    """
    patches = parse_patch(DELETE_FILE_DIFF)
    assert patches[0].path == "/dev/null"
    # Callers (workspace_patch) check for /dev/null and call fs.delete() instead.


# ---------------------------------------------------------------------------
# apply_file_patch — multi-hunk patch applies all hunks in order
# ---------------------------------------------------------------------------


def test_apply_file_patch_multi_hunk(tmp_path: Path) -> None:
    fs = Filesystem(str(tmp_path), "ws")
    # 7 lines; two hunks each replace one line
    fs.write(
        "foo.py",
        b"line1\nold1\nline3\nline4\nline5\nold2\nline7\n",
    )
    patch = FilePatch(
        path="foo.py",
        hunks=[
            Hunk(
                old_start=2,
                old_count=1,
                new_start=2,
                new_count=1,
                lines=["-old1", "+new1"],
            ),
            Hunk(
                old_start=6,
                old_count=1,
                new_start=6,
                new_count=1,
                lines=["-old2", "+new2"],
            ),
        ],
    )
    apply_file_patch(fs, patch)
    result = fs.read("foo.py").decode()
    assert "new1" in result
    assert "new2" in result
    assert "old1" not in result
    assert "old2" not in result
    # Surrounding lines preserved
    assert "line1" in result
    assert "line3" in result
    assert "line7" in result
