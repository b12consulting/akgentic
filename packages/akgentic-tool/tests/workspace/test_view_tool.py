"""Tests for workspace_view tool — Story 5.11."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import akgentic.tool.workspace.tool as _tool_mod
from akgentic.tool.errors import RetriableError
from akgentic.tool.workspace.tool import WorkspaceTool, WorkspaceView
from akgentic.tool.workspace.workspace import Filesystem

try:
    from pydantic_ai.messages import BinaryContent
except ImportError:
    BinaryContent = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Helpers — mirror pattern from test_read_tool.py
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


def make_wired_read_tool(tmp_path: Path) -> tuple[WorkspaceTool, Filesystem]:
    """Build a WorkspaceTool(read_only=True) wired to a Filesystem rooted at tmp_path."""
    tid = uuid.uuid4()
    fs = Filesystem(str(tmp_path), str(tid))
    observer = make_observer(team_id=tid)
    tool = WorkspaceTool(read_only=True)
    with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
        tool.observer(observer)
    return tool, fs


# ---------------------------------------------------------------------------
# TestWorkspaceReadBytes
# ---------------------------------------------------------------------------


class TestWorkspaceReadBytes:
    """Tests for Filesystem.read_bytes() (AC: 3)."""

    def test_read_bytes_returns_raw_bytes(self, tmp_path: Path) -> None:
        """read_bytes returns exact bytes written to the file."""
        _, fs = make_wired_read_tool(tmp_path)
        (fs._root / "data.bin").write_bytes(b"\x00\x01\x02\x03")
        result = fs.read_bytes("data.bin")
        assert result == b"\x00\x01\x02\x03"

    def test_read_bytes_traversal_guard(self, tmp_path: Path) -> None:
        """read_bytes raises PermissionError for path traversal attempts."""
        _, fs = make_wired_read_tool(tmp_path)
        with pytest.raises(PermissionError):
            fs.read_bytes("../../etc/passwd")

    def test_read_bytes_not_found(self, tmp_path: Path) -> None:
        """read_bytes raises FileNotFoundError for non-existent files."""
        _, fs = make_wired_read_tool(tmp_path)
        with pytest.raises(FileNotFoundError):
            fs.read_bytes("nonexistent.bin")


# ---------------------------------------------------------------------------
# TestWorkspaceViewTool
# ---------------------------------------------------------------------------


class TestWorkspaceViewTool:
    """Tests for workspace_view tool callable (AC: 4, 10, 11)."""

    def _view_fn(self, tool: WorkspaceTool) -> object:
        """Extract workspace_view callable from tool."""
        return next(t for t in tool.get_tools() if t.__name__ == "workspace_view")

    def test_png_success(self, tmp_path: Path) -> None:
        """Valid PNG → BinaryContent(media_type='image/png')."""
        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "screenshot.png").write_bytes(b"fake-png-bytes")
        # Disable resize to avoid Pillow dependency in basic test
        tool.workspace_view = WorkspaceView(max_dimension=0)
        fn = self._view_fn(tool)
        result = fn("screenshot.png") # type: ignore[assignment]
        assert result.media_type == "image/png"
        assert result.data == b"fake-png-bytes"

    def test_jpeg_success(self, tmp_path: Path) -> None:
        """Valid JPEG → BinaryContent(media_type='image/jpeg')."""
        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "photo.jpg").write_bytes(b"fake-jpeg-bytes")
        tool.workspace_view = WorkspaceView(max_dimension=0)
        fn = self._view_fn(tool)
        result = fn("photo.jpg")  # type: ignore[assignment]
        assert result.media_type == "image/jpeg"
        assert result.data == b"fake-jpeg-bytes"

    def test_webp_success(self, tmp_path: Path) -> None:
        """Valid WebP → BinaryContent(media_type='image/webp')."""
        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "anim.webp").write_bytes(b"fake-webp-bytes")
        tool.workspace_view = WorkspaceView(max_dimension=0)
        fn = self._view_fn(tool)
        result = fn("anim.webp")  # type: ignore[assignment]
        assert result.media_type == "image/webp"

    def test_gif_success(self, tmp_path: Path) -> None:
        """Valid GIF → BinaryContent(media_type='image/gif')."""
        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "anim.gif").write_bytes(b"fake-gif-bytes")
        tool.workspace_view = WorkspaceView(max_dimension=0)
        fn = self._view_fn(tool)
        result = fn("anim.gif")  # type: ignore[assignment]
        assert result.media_type == "image/gif"

    def test_unsupported_format_raises_retriable_error(self, tmp_path: Path) -> None:
        """PDF extension → RetriableError (AC: 10)."""
        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "report.pdf").write_bytes(b"%PDF fake")
        tool.workspace_view = WorkspaceView(max_dimension=0)
        fn = self._view_fn(tool)
        with pytest.raises(RetriableError):
            fn("report.pdf")  # type: ignore[assignment]

    def test_unsupported_format_error_message_hint(self, tmp_path: Path) -> None:
        """RetriableError message for unsupported format includes workspace_read hint."""
        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "report.pdf").write_bytes(b"%PDF fake")
        tool.workspace_view = WorkspaceView(max_dimension=0)
        fn = self._view_fn(tool)
        with pytest.raises(RetriableError, match="workspace_read"):
            fn("report.pdf")  # type: ignore[assignment]

    def test_file_not_found_raises_retriable_error(self, tmp_path: Path) -> None:
        """Non-existent file → RetriableError."""
        tool, _ = make_wired_read_tool(tmp_path)
        tool.workspace_view = WorkspaceView(max_dimension=0)
        fn = self._view_fn(tool)
        with pytest.raises(RetriableError, match="File not found"):
            fn("ghost.png")  # type: ignore[assignment]

    def test_traversal_raises_retriable_error(self, tmp_path: Path) -> None:
        """Path traversal → RetriableError."""
        tool, _ = make_wired_read_tool(tmp_path)
        tool.workspace_view = WorkspaceView(max_dimension=0)
        fn = self._view_fn(tool)
        with pytest.raises(RetriableError):
            fn("../../secret.png")  # type: ignore[assignment]

    def test_workspace_view_false_not_in_tools(self, tmp_path: Path) -> None:
        """workspace_view=False → workspace_view not in get_tools() (AC: 11)."""
        tid = uuid.uuid4()
        fs = Filesystem(str(tmp_path), str(tid))
        observer = make_observer(team_id=tid)
        tool = WorkspaceTool(read_only=True, workspace_view=False)
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs):
            tool.observer(observer)
        names = [t.__name__ for t in tool.get_tools()]
        assert "workspace_view" not in names

    def test_workspace_view_true_in_tools(self, tmp_path: Path) -> None:
        """workspace_view=True → workspace_view in get_tools()."""
        tool, _ = make_wired_read_tool(tmp_path)
        names = [t.__name__ for t in tool.get_tools()]
        assert "workspace_view" in names


# ---------------------------------------------------------------------------
# TestWorkspaceViewResize (Pillow-dependent)
# ---------------------------------------------------------------------------


PIL = pytest.importorskip("PIL", reason="Pillow not installed")


def _make_png_bytes(width: int, height: int) -> bytes:
    """Create a real minimal PNG image of given dimensions."""
    import io

    from PIL import Image

    img = Image.new("RGB", (width, height), color=(128, 64, 32))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestWorkspaceViewResize:
    """Tests for image resizing and sidecar cache behaviour (AC: 5, 6, 7, 8)."""

    def test_max_dimension_zero_skips_resize_and_sidecar(self, tmp_path: Path) -> None:
        """max_dimension=0 → raw bytes returned, no sidecar written (AC: 8)."""
        tool, fs = make_wired_read_tool(tmp_path)
        raw = _make_png_bytes(2000, 2000)
        (fs._root / "big.png").write_bytes(raw)
        tool.workspace_view = WorkspaceView(max_dimension=0)
        fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_view")
        result = fn("big.png")
        assert result.data == raw
        # No sidecar should be created
        sidecars = list(fs._root.glob(".big.png.*"))
        assert sidecars == []

    def test_resize_creates_sidecar(self, tmp_path: Path) -> None:
        """Image > max_dimension → sidecar created (AC: 5)."""
        tool, fs = make_wired_read_tool(tmp_path)
        raw = _make_png_bytes(2000, 1000)
        (fs._root / "big.png").write_bytes(raw)
        tool.workspace_view = WorkspaceView(max_dimension=1568)
        fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_view")
        fn("big.png")
        sidecar = fs._root / ".big.png.1568.png"
        assert sidecar.exists()

    def test_sidecar_cache_hit(self, tmp_path: Path) -> None:
        """Second call hits sidecar — PIL.Image.open not called on second call (AC: 6)."""
        from PIL import Image

        tool, fs = make_wired_read_tool(tmp_path)
        raw = _make_png_bytes(2000, 1000)
        (fs._root / "big.png").write_bytes(raw)
        tool.workspace_view = WorkspaceView(max_dimension=1568)
        fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_view")
        # First call — creates sidecar
        fn("big.png")
        sidecar = fs._root / ".big.png.1568.png"
        assert sidecar.exists()
        sidecar_bytes = sidecar.read_bytes()

        # Second call — should hit sidecar
        open_call_count = 0
        original_open = Image.open

        def counting_open(fp: object, **kwargs: object) -> object:
            nonlocal open_call_count
            open_call_count += 1
            return original_open(fp, **kwargs)  # type: ignore[arg-type]

        with patch.object(Image, "open", side_effect=counting_open):
            result2 = fn("big.png")

        assert open_call_count == 0  # sidecar hit — no PIL open needed
        assert result2.data == sidecar_bytes

    def test_dimension_keyed_sidecar_isolation(self, tmp_path: Path) -> None:
        """max_dimension=800 creates different sidecar than max_dimension=1568 (AC: 7)."""
        tool, fs = make_wired_read_tool(tmp_path)
        raw = _make_png_bytes(2000, 2000)
        (fs._root / "big.png").write_bytes(raw)

        # Call with 1568
        tid = uuid.uuid4()
        fs2 = Filesystem(str(tmp_path), str(tid))
        obs2 = make_observer(team_id=tid)
        tool2 = WorkspaceTool(read_only=True, workspace_view=WorkspaceView(max_dimension=1568))
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs2):
            tool2.observer(obs2)
        (fs2._root / "big.png").write_bytes(raw)
        fn2 = next(t for t in tool2.get_tools() if t.__name__ == "workspace_view")
        fn2("big.png")

        # Call with 800
        tid3 = uuid.uuid4()
        fs3 = Filesystem(str(tmp_path), str(tid3))
        obs3 = make_observer(team_id=tid3)
        tool3 = WorkspaceTool(read_only=True, workspace_view=WorkspaceView(max_dimension=800))
        with patch("akgentic.tool.workspace.tool.get_workspace", return_value=fs3):
            tool3.observer(obs3)
        (fs3._root / "big.png").write_bytes(raw)
        fn3 = next(t for t in tool3.get_tools() if t.__name__ == "workspace_view")
        fn3("big.png")

        sidecar_1568 = fs2._root / ".big.png.1568.png"
        sidecar_800 = fs3._root / ".big.png.800.png"
        assert sidecar_1568.exists()
        assert sidecar_800.exists()
        # Different files (different sizes)
        assert sidecar_1568.stat().st_size != sidecar_800.stat().st_size

    def test_image_within_limit_no_resize(self, tmp_path: Path) -> None:
        """Image <= max_dimension → raw bytes, no sidecar created."""
        tool, fs = make_wired_read_tool(tmp_path)
        raw = _make_png_bytes(100, 100)
        (fs._root / "small.png").write_bytes(raw)
        tool.workspace_view = WorkspaceView(max_dimension=1568)
        fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_view")
        result = fn("small.png")
        assert result.data == raw
        sidecar = fs._root / ".small.png.1568.png"
        assert not sidecar.exists()

    def test_webp_resize_uses_webp_format(self, tmp_path: Path) -> None:
        """WebP image resized and sidecar contains valid WebP bytes (not JPEG)."""
        import io

        from PIL import Image

        # Build a minimal WebP image larger than 1568px
        img = Image.new("RGB", (2000, 2000), color=(10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format="WEBP")
        raw_webp = buf.getvalue()

        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "anim.webp").write_bytes(raw_webp)
        tool.workspace_view = WorkspaceView(max_dimension=1568)
        fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_view")
        result = fn("anim.webp")

        assert result.media_type == "image/webp"
        # Verify sidecar bytes are valid WebP (starts with RIFF...WEBP signature)
        sidecar = fs._root / ".anim.webp.1568.webp"
        assert sidecar.exists()
        sidecar_bytes = sidecar.read_bytes()
        assert sidecar_bytes[:4] == b"RIFF", "Sidecar must be WebP (RIFF), not JPEG (FFD8)"
        assert result.data == sidecar_bytes

    def test_jpeg_resize_uses_jpeg_format(self, tmp_path: Path) -> None:
        """JPEG image resized and sidecar contains valid JPEG bytes."""
        import io

        from PIL import Image

        img = Image.new("RGB", (2000, 2000), color=(50, 100, 150))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        raw_jpeg = buf.getvalue()

        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "photo.jpg").write_bytes(raw_jpeg)
        tool.workspace_view = WorkspaceView(max_dimension=1568)
        fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_view")
        result = fn("photo.jpg")

        assert result.media_type == "image/jpeg"
        sidecar = fs._root / ".photo.jpg.1568.jpg"
        assert sidecar.exists()
        # JPEG files start with FFD8
        assert sidecar.read_bytes()[:2] == b"\xff\xd8", "Sidecar must be JPEG"


# ---------------------------------------------------------------------------
# TestWorkspaceViewPillowAbsent
# ---------------------------------------------------------------------------


class TestWorkspaceViewPillowAbsent:
    """Tests for graceful Pillow-absent fallback (AC: 9)."""

    def test_pillow_absent_returns_raw_bytes_no_error(self, tmp_path: Path) -> None:
        """When Pillow is not installed, raw bytes returned with no ImportError raised."""
        _tool_mod._PILLOW_WARN_EMITTED = False  # reset one-time flag for test isolation
        tool, fs = make_wired_read_tool(tmp_path)
        raw = b"fake-png-data"
        (fs._root / "image.png").write_bytes(raw)
        tool.workspace_view = WorkspaceView(max_dimension=1568)
        fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_view")

        import sys

        with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
            result = fn("image.png")

        assert result.data == raw
        assert result.media_type == "image/png"

    def test_pillow_absent_no_sidecar_written(self, tmp_path: Path) -> None:
        """When Pillow absent, no sidecar file is created."""
        _tool_mod._PILLOW_WARN_EMITTED = False  # reset one-time flag for test isolation
        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "image.png").write_bytes(b"fake-png-data")
        tool.workspace_view = WorkspaceView(max_dimension=1568)
        fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_view")

        import sys

        with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
            fn("image.png")

        sidecars = list(fs._root.glob(".image.png.*"))
        assert sidecars == []

    def test_pillow_absent_logs_warning(self, tmp_path: Path) -> None:
        """When Pillow absent, a warning is logged once (one-time flag)."""
        import logging
        import sys

        _tool_mod._PILLOW_WARN_EMITTED = False  # reset one-time flag for test isolation
        tool, fs = make_wired_read_tool(tmp_path)
        (fs._root / "image.png").write_bytes(b"fake-png-data")
        tool.workspace_view = WorkspaceView(max_dimension=1568)
        fn = next(t for t in tool.get_tools() if t.__name__ == "workspace_view")

        with (
            patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}),
            patch.object(logging.getLogger("akgentic.tool.workspace.tool"), "warning") as mock_warn,
        ):
            fn("image.png")
            fn("image.png")  # second call — warning must NOT be repeated

        mock_warn.assert_called_once()  # only one warning total across both calls
        assert "Pillow" in mock_warn.call_args[0][0]
