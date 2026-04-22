"""Workspace edit utilities — EditMatcher, line ending helpers, unified diff patch.

All functionality required by WorkspaceTool.workspace_edit, workspace_multi_edit,
and workspace_patch lives here. This module has no dependency on tool.py or
ToolCard — it is pure algorithmic logic consumed by WorkspaceTool in tool.py.
"""

from __future__ import annotations

import codecs
import re
import textwrap
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from akgentic.tool.workspace.workspace import Workspace


@dataclass
class MatchResult:
    start: int  # byte offset in content where match begins
    end: int  # byte offset in content where match ends (exclusive)
    strategy: str  # name of winning strategy, e.g. "exact", "line_trimmed"


class EditMatcher:
    """7-strategy cascade for locating old_string in file content.

    Strategies are tried in order. The first match wins.
    All strategies return MatchResult(start, end, strategy) or None.
    """

    FUZZY_THRESHOLD: float = 0.85

    def find(self, content: str, old_string: str) -> MatchResult | None:
        for strategy in (
            self._exact,
            self._line_trimmed,
            self._whitespace_normalised,
            self._dedented,
            self._trimmed_boundary,
            self._escape_normalised,
            self._fuzzy,
        ):
            result = strategy(content, old_string)
            if result is not None:
                return result
        return None

    # --- Strategy 1: exact ---------------------------------------------------

    def _exact(self, content: str, old: str) -> MatchResult | None:
        idx = content.find(old)
        if idx == -1:
            return None
        return MatchResult(start=idx, end=idx + len(old), strategy="exact")

    # --- Strategy 2: line-trimmed --------------------------------------------

    def _line_trimmed(self, content: str, old: str) -> MatchResult | None:
        """Strip per-line leading/trailing whitespace from old, then exact-match."""
        stripped = "\n".join(line.strip() for line in old.splitlines())
        if stripped == old:
            return None  # no change — nothing new to try
        norm_content = "\n".join(line.strip() for line in content.splitlines())
        idx = norm_content.find(stripped)
        if idx == -1:
            return None
        # Remap idx back to original content
        return self._remap(content, old, idx, norm_content, strategy="line_trimmed")

    # --- Strategy 3: whitespace-normalised -----------------------------------

    def _whitespace_normalised(self, content: str, old: str) -> MatchResult | None:
        """Collapse internal whitespace runs to single space, then exact-match."""
        norm_old = re.sub(r"[ \t]+", " ", old)
        if norm_old == old:
            return None
        norm_content = re.sub(r"[ \t]+", " ", content)
        idx = norm_content.find(norm_old)
        if idx == -1:
            return None
        return self._remap(content, old, idx, norm_content, strategy="whitespace_normalised")

    # --- Strategy 4: dedented ------------------------------------------------

    def _dedented(self, content: str, old: str) -> MatchResult | None:
        """Dedent old_string, then exact-match against dedented content windows."""
        dedented_old = textwrap.dedent(old)
        if dedented_old == old:
            return None
        # Try matching dedented_old against lines in content with various indent levels
        idx = content.find(dedented_old)
        if idx != -1:
            return MatchResult(start=idx, end=idx + len(dedented_old), strategy="dedented")
        # Also try: dedent old, dedent content
        dedented_content = textwrap.dedent(content)
        idx = dedented_content.find(dedented_old)
        if idx == -1:
            return None
        return self._remap(content, old, idx, dedented_content, strategy="dedented")

    # --- Strategy 5: trimmed boundary ----------------------------------------

    def _trimmed_boundary(self, content: str, old: str) -> MatchResult | None:
        """Strip blank lines at edges of old_string, then exact-match."""
        stripped = old.strip("\n")
        if stripped == old:
            return None  # no blank edges
        result = self._exact(content, stripped)
        if result is None:
            return None
        return MatchResult(start=result.start, end=result.end, strategy="trimmed_boundary")

    # --- Strategy 6: escape-normalised ---------------------------------------

    def _escape_normalised(self, content: str, old: str) -> MatchResult | None:
        """Decode escape sequences in old_string, then exact-match."""
        try:
            decoded = codecs.decode(old.encode(), "unicode_escape")
            # In Python 3, codecs.decode with 'unicode_escape' returns str directly
            norm_old: str = (
                decoded if isinstance(decoded, str) else decoded.decode("utf-8", errors="replace")
            )
        except Exception:
            return None
        if norm_old == old:
            return None
        result = self._exact(content, norm_old)
        if result is not None:
            return MatchResult(start=result.start, end=result.end, strategy="escape_normalised")
        # Inverse: decode content, match original old
        try:
            decoded_content = codecs.decode(content.encode(), "unicode_escape")
            norm_content: str = (
                decoded_content
                if isinstance(decoded_content, str)
                else decoded_content.decode("utf-8", errors="replace")
            )
        except Exception:
            return None
        result = self._exact(norm_content, old)
        if result is None:
            return None
        return self._remap(content, old, result.start, norm_content, strategy="escape_normalised")

    # --- Strategy 7: fuzzy ---------------------------------------------------

    def _fuzzy(self, content: str, old: str) -> MatchResult | None:
        """SequenceMatcher fuzzy search; ratio must be >= FUZZY_THRESHOLD."""
        old_lines = old.splitlines()
        content_lines = content.splitlines()
        n = len(old_lines)
        if n == 0:
            return None
        best_ratio = 0.0
        best_start_line = -1
        for i in range(len(content_lines) - n + 1):
            window = content_lines[i : i + n]
            ratio = SequenceMatcher(None, old_lines, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start_line = i
        if best_ratio < self.FUZZY_THRESHOLD or best_start_line == -1:
            return None
        # Map line index back to byte offset
        start = sum(len(line) + 1 for line in content_lines[:best_start_line])
        end = start + sum(
            len(line) + 1 for line in content_lines[best_start_line : best_start_line + n]
        )
        # Trim trailing newline overshoot
        end = min(end, len(content))
        return MatchResult(start=start, end=end, strategy="fuzzy")

    # --- Remap helper --------------------------------------------------------

    def _remap(
        self,
        original: str,
        old: str,
        start_in_norm: int,
        norm_content: str,
        strategy: str,
    ) -> MatchResult | None:
        """Map a match offset in norm_content back to the original string.

        Approximation: find the nearest position in original that aligns with
        the normalised match start. Falls back to original line-count heuristic.
        """
        # Count lines before start_in_norm in norm_content
        lines_before = norm_content[:start_in_norm].count("\n")
        # Find same line offset in original
        orig_lines = original.splitlines(keepends=True)
        if lines_before >= len(orig_lines):
            return None
        orig_start = sum(len(line) for line in orig_lines[:lines_before])
        # Count lines in old to determine end
        old_line_count = len(old.splitlines())
        orig_end = sum(
            len(line) for line in orig_lines[lines_before : lines_before + old_line_count]
        )
        orig_end = orig_start + orig_end
        orig_end = min(orig_end, len(original))
        return MatchResult(start=orig_start, end=orig_end, strategy=strategy)


def detect_line_ending(content: str) -> str:
    """Detect dominant line ending in content.

    Returns ``"\\r\\n"`` if CRLF is dominant, ``"\\n"`` otherwise (including
    when content is empty or has no line endings).
    """
    crlf_count = content.count("\r\n")
    lf_count = content.count("\n") - crlf_count  # pure LF only
    return "\r\n" if crlf_count > lf_count else "\n"


def normalise_endings(content: str, line_ending: str) -> str:
    """Convert all line endings in content to line_ending.

    First normalises all CRLF to LF, then converts LF to the target
    line_ending. This prevents double-conversion (``\\r\\r\\n``).
    """
    normalised = content.replace("\r\n", "\n")
    if line_ending == "\r\n":
        return normalised.replace("\n", "\r\n")
    return normalised


class Hunk(BaseModel):
    """Single hunk from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]  # each line prefixed with '+', '-', or ' '


class FilePatch(BaseModel):
    """Parsed unified diff for a single file."""

    path: str
    hunks: list[Hunk]


class EditItem(BaseModel):
    """A single find-and-replace operation for workspace_multi_edit."""

    path: str
    old_string: str
    new_string: str
    replace_all: bool = False


_HUNK_HEADER: re.Pattern[str] = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_patch(patch_text: str) -> list[FilePatch]:
    """Parse a GNU unified diff string into a list of FilePatch objects.

    Supports single-file and multi-file diffs. Lines with ``--- /dev/null``
    or ``+++ /dev/null`` are preserved in the path field as-is; consumers
    must handle the sentinel.
    """
    patches: list[FilePatch] = []
    current_path: str | None = None
    current_hunks: list[Hunk] = []
    current_hunk_lines: list[str] = []
    current_hunk_header: tuple[int, int, int, int] | None = None

    def _flush_hunk() -> None:
        nonlocal current_hunk_lines, current_hunk_header
        if current_hunk_header is not None:
            os_, oc_, ns_, nc_ = current_hunk_header
            current_hunks.append(
                Hunk(
                    old_start=os_,
                    old_count=oc_,
                    new_start=ns_,
                    new_count=nc_,
                    lines=list(current_hunk_lines),
                )
            )

    def _flush_patch() -> None:
        nonlocal current_path, current_hunks, current_hunk_lines, current_hunk_header
        if current_path is not None:
            _flush_hunk()
            patches.append(FilePatch(path=current_path, hunks=list(current_hunks)))

    for line in patch_text.splitlines():
        if line.startswith("+++ "):
            _flush_patch()  # save previous file patch if any
            # path is after "+++ " prefix, strip optional "b/" git prefix
            raw_path = line[4:].strip()
            current_path = raw_path[2:] if raw_path.startswith("b/") else raw_path
            current_hunks = []
            current_hunk_lines = []
            current_hunk_header = None
        elif line.startswith("--- "):
            continue  # skip --- lines; path taken from +++ line
        else:
            m = _HUNK_HEADER.match(line)
            if m:
                if current_hunk_header is not None:
                    _flush_hunk()
                    current_hunk_lines = []
                os_ = int(m.group(1))
                oc_ = int(m.group(2)) if m.group(2) is not None else 1
                ns_ = int(m.group(3))
                nc_ = int(m.group(4)) if m.group(4) is not None else 1
                current_hunk_header = (os_, oc_, ns_, nc_)
            elif current_hunk_header is not None and (
                line.startswith("+") or line.startswith("-") or line.startswith(" ")
            ):
                current_hunk_lines.append(line)

    _flush_patch()
    return patches


def apply_file_patch(workspace: "Workspace", file_patch: FilePatch) -> None:
    """Read a workspace file, apply all hunks, write result back.

    Handles add (path="new_file", all hunks are additions),
    update (normal patch), and delete (path is sentinel "/dev/null" — but
    callers handle delete by checking file_patch.path against "/dev/null"
    BEFORE calling this function — see workspace_patch in tool.py).

    Args:
        workspace: Workspace backend (Filesystem instance in practice).
        file_patch: Parsed FilePatch with one or more Hunk objects.

    Raises:
        FileNotFoundError: If file does not exist and patch is not a pure add.
        PermissionError: If path escapes the workspace root.
    """
    # Determine if this is a new-file creation (all lines are additions).
    # Note: `all()` on an empty sequence returns True, so we guard against
    # empty hunk lists explicitly to avoid treating an empty patch as new-file.
    is_new_file = bool(file_patch.hunks) and all(
        all(patch_line.startswith("+") for patch_line in hunk.lines if patch_line)
        for hunk in file_patch.hunks
    )

    if is_new_file:
        new_content = "\n".join(
            line[1:] for hunk in file_patch.hunks for line in hunk.lines if line.startswith("+")
        )
        workspace.write(file_patch.path, (new_content + "\n").encode("utf-8"))
        return

    raw = workspace.read(file_patch.path)
    lines = raw.decode("utf-8").splitlines()
    offset = 0
    for hunk in file_patch.hunks:
        start = hunk.old_start - 1 + offset
        # Collect replacement lines (context + additions)
        new_lines: list[str] = []
        for patch_line in hunk.lines:
            if patch_line.startswith("+"):
                new_lines.append(patch_line[1:])
            elif patch_line.startswith(" "):
                new_lines.append(patch_line[1:])
            # Lines starting with '-' are dropped
        lines[start : start + hunk.old_count] = new_lines
        offset += len(new_lines) - hunk.old_count
    workspace.write(file_patch.path, ("\n".join(lines) + "\n").encode("utf-8"))
