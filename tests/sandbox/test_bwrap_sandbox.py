"""Tests for BwrapSandboxActor — Linux bubblewrap sandbox execution.

Covers Story 8.2 (AC: 1–8):
- _start_sandbox() raises RuntimeError when bwrap not on PATH (AC2)
- _start_sandbox() creates workspace directory (AC1)
- _start_sandbox() is idempotent (AC1 — mkdir exist_ok=True)
- _stop_sandbox() is a no-op (AC3)
- _exec() builds correct bwrap command with all required flags (AC4)
- _exec() passes preexec_fn callable to subprocess.run (AC5)
- _exec() passes minimal PATH-only env dict to subprocess.run (AC5)
- _exec() uses /workspace as cwd when cwd="" (AC4)
- _exec() appends cwd to /workspace when cwd is non-empty (AC4)
- _exec() returns ExecResult with correct fields (AC4)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from akgentic.tool.sandbox.actor import ExecResult, SandboxConfig, SandboxState
from akgentic.tool.sandbox.bwrap import BwrapSandboxActor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def actor(tmp_path: Path) -> BwrapSandboxActor:
    """Return a BwrapSandboxActor with workspace resolved to a temp directory.

    Uses ``BwrapSandboxActor.__new__`` to bypass Pykka's actor instantiation.
    Config and state are set directly, mirroring the pattern in test_local_sandbox.py.
    """
    a: BwrapSandboxActor = BwrapSandboxActor.__new__(BwrapSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()
    a.state.workspace_path = tmp_path
    return a


# ---------------------------------------------------------------------------
# AC2: _start_sandbox() raises RuntimeError when bwrap not on PATH
# ---------------------------------------------------------------------------


def test_start_sandbox_bwrap_not_on_path_raises_runtime_error() -> None:
    """AC2: _start_sandbox() raises RuntimeError with install hints when bwrap missing."""
    a: BwrapSandboxActor = BwrapSandboxActor.__new__(BwrapSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    with patch("akgentic.tool.sandbox.bwrap.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="bwrap not found"):
            a._start_sandbox()


def test_start_sandbox_bwrap_not_on_path_error_contains_apt_hint() -> None:
    """AC2: RuntimeError message includes 'apt install bubblewrap'."""
    a: BwrapSandboxActor = BwrapSandboxActor.__new__(BwrapSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    with patch("akgentic.tool.sandbox.bwrap.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="apt install bubblewrap"):
            a._start_sandbox()


def test_start_sandbox_bwrap_not_on_path_error_contains_dnf_hint() -> None:
    """AC2: RuntimeError message includes 'dnf install bubblewrap'."""
    a: BwrapSandboxActor = BwrapSandboxActor.__new__(BwrapSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    with patch("akgentic.tool.sandbox.bwrap.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="dnf install bubblewrap"):
            a._start_sandbox()


# ---------------------------------------------------------------------------
# AC1: _start_sandbox() creates workspace directory
# ---------------------------------------------------------------------------


def test_start_sandbox_creates_workspace_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: _start_sandbox() creates workspace under AKGENTIC_WORKSPACES_ROOT."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)

    a: BwrapSandboxActor = BwrapSandboxActor.__new__(BwrapSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    with (
        patch("akgentic.tool.sandbox.bwrap.shutil.which", return_value="/usr/bin/bwrap"),
        patch("akgentic.tool.sandbox.actor.SandboxState.notify_state_change"),
    ):
        a._start_sandbox()

    expected = tmp_path / "workspaces" / "test-team"
    assert expected.exists()
    assert expected.is_dir()


def test_start_sandbox_stores_resolved_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: _start_sandbox() stores resolved absolute path in state.workspace_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)

    a: BwrapSandboxActor = BwrapSandboxActor.__new__(BwrapSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    with (
        patch("akgentic.tool.sandbox.bwrap.shutil.which", return_value="/usr/bin/bwrap"),
        patch("akgentic.tool.sandbox.actor.SandboxState.notify_state_change"),
    ):
        a._start_sandbox()

    assert a.state.workspace_path is not None
    assert a.state.workspace_path.is_absolute()
    assert a.state.workspace_path == (tmp_path / "workspaces" / "test-team").resolve()


# ---------------------------------------------------------------------------
# AC1: _start_sandbox() is idempotent (exist_ok=True)
# ---------------------------------------------------------------------------


def test_start_sandbox_idempotent_existing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: Calling _start_sandbox() twice does not raise (idempotent mkdir)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)

    a: BwrapSandboxActor = BwrapSandboxActor.__new__(BwrapSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    with (
        patch("akgentic.tool.sandbox.bwrap.shutil.which", return_value="/usr/bin/bwrap"),
        patch("akgentic.tool.sandbox.actor.SandboxState.notify_state_change"),
    ):
        a._start_sandbox()
        a._start_sandbox()  # Must not raise

    expected = tmp_path / "workspaces" / "test-team"
    assert expected.exists()


# ---------------------------------------------------------------------------
# AC3: _stop_sandbox() is a no-op
# ---------------------------------------------------------------------------


def test_stop_sandbox_is_noop(actor: BwrapSandboxActor) -> None:
    """AC3: _stop_sandbox() returns None and makes no subprocess calls."""
    with patch("akgentic.tool.sandbox.bwrap.subprocess.run") as mock_run:
        result = actor._stop_sandbox()

    assert result is None
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# AC4: _exec() builds correct bwrap command with all required flags
# ---------------------------------------------------------------------------


def test_exec_builds_correct_bwrap_command(actor: BwrapSandboxActor) -> None:
    """AC4: _exec() passes a bwrap command list with all required flags to subprocess.run."""
    with patch("akgentic.tool.sandbox.bwrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        actor._exec("ls .", "")

        cmd_list: list[str] = mock_run.call_args[0][0]  # first positional arg

    assert cmd_list[0] == "bwrap"
    assert "--bind" in cmd_list
    assert "--ro-bind" in cmd_list
    assert "--ro-bind-try" in cmd_list
    assert "--tmpfs" in cmd_list
    assert "--dev" in cmd_list
    assert "--proc" in cmd_list
    assert "--unshare-net" in cmd_list
    assert "--unshare-pid" in cmd_list
    assert "--die-with-parent" in cmd_list
    assert "--new-session" in cmd_list
    assert "--chdir" in cmd_list
    assert "/workspace" in cmd_list
    assert "ls" in cmd_list


# ---------------------------------------------------------------------------
# AC4: --chdir argument handling for cwd
# ---------------------------------------------------------------------------


def test_exec_cwd_empty_uses_workspace_root(actor: BwrapSandboxActor) -> None:
    """AC4: When cwd='', --chdir is followed by '/workspace'."""
    with patch("akgentic.tool.sandbox.bwrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        actor._exec("ls .", "")

        cmd_list: list[str] = mock_run.call_args[0][0]

    chdir_idx = cmd_list.index("--chdir")
    assert cmd_list[chdir_idx + 1] == "/workspace"


def test_exec_cwd_nonempty_appends_to_workspace(actor: BwrapSandboxActor) -> None:
    """AC4: When cwd='subdir', --chdir is followed by '/workspace/subdir'."""
    with patch("akgentic.tool.sandbox.bwrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        actor._exec("ls .", "subdir")

        cmd_list: list[str] = mock_run.call_args[0][0]

    chdir_idx = cmd_list.index("--chdir")
    assert cmd_list[chdir_idx + 1] == "/workspace/subdir"


# ---------------------------------------------------------------------------
# AC5: resource limits and env stripping
# ---------------------------------------------------------------------------


def test_exec_passes_preexec_fn_to_subprocess(actor: BwrapSandboxActor) -> None:
    """AC5: _exec() passes a non-None callable preexec_fn to subprocess.run."""
    with patch("akgentic.tool.sandbox.bwrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        actor._exec("ls .", "")

        call_kwargs = mock_run.call_args
    assert call_kwargs is not None
    preexec_fn = call_kwargs.kwargs.get("preexec_fn")
    assert preexec_fn is not None
    assert callable(preexec_fn)


def test_exec_strips_env_to_minimal_path(actor: BwrapSandboxActor) -> None:
    """AC5: _exec() passes minimal PATH-only env dict to subprocess.run."""
    with patch("akgentic.tool.sandbox.bwrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        actor._exec("ls .", "")

        call_kwargs = mock_run.call_args
    assert call_kwargs is not None
    env = call_kwargs.kwargs.get("env")
    assert env == {"PATH": "/usr/bin:/bin:/usr/local/bin"}


# ---------------------------------------------------------------------------
# AC4: _exec() returns ExecResult with correct fields
# ---------------------------------------------------------------------------


def test_exec_returns_exec_result(actor: BwrapSandboxActor) -> None:
    """AC4: _exec() returns ExecResult with stdout, stderr, exit_code from subprocess.run."""
    with patch("akgentic.tool.sandbox.bwrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="out", stderr="err", returncode=0)
        result = actor._exec("ls .", "")

    assert isinstance(result, ExecResult)
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.exit_code == 0


def test_exec_returns_exec_result_nonzero_exit(actor: BwrapSandboxActor) -> None:
    """AC4: _exec() correctly captures non-zero exit codes."""
    with patch("akgentic.tool.sandbox.bwrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="error output", returncode=1)
        result = actor._exec("ls .", "")

    assert result.exit_code == 1
    assert result.stderr == "error output"
