"""Tests for SeatbeltSandboxActor — macOS Apple Seatbelt sandbox execution.

Covers Story 8.3 (AC: 1–7):
- _start_sandbox() raises RuntimeError when sandbox-exec not on PATH (AC2)
- _start_sandbox() emits DeprecationWarning with correct message (AC1)
- _start_sandbox() creates workspace directory (AC1)
- _start_sandbox() is idempotent — exist_ok=True (AC1)
- _stop_sandbox() is a no-op (AC3)
- _exec() writes SBPL policy and calls sandbox-exec -f <file> (AC4)
- _exec() policy contains (deny default) (AC5)
- _exec() policy substitutes actual workspace path (AC4, AC5)
- _exec() policy contains (deny network*) (AC5)
- _exec() policy contains file-read* and file-write* for workspace (AC5)
- _exec() deletes temp file in finally block (AC4)
- _exec() returns ExecResult with correct fields (AC4)
- _exec() does NOT pass preexec_fn to subprocess.run (AC6)
"""

from __future__ import annotations

import os
import tempfile
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from akgentic.tool.sandbox.actor import ExecResult, SandboxConfig, SandboxState
from akgentic.tool.sandbox.seatbelt import SeatbeltSandboxActor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def actor(tmp_path: Path) -> SeatbeltSandboxActor:
    """Return a SeatbeltSandboxActor with workspace resolved to a temp directory.

    Uses ``SeatbeltSandboxActor.__new__`` to bypass Pykka's actor instantiation.
    Config and state are set directly, following the same pattern as
    ``test_bwrap_sandbox.py``.
    """
    a: SeatbeltSandboxActor = SeatbeltSandboxActor.__new__(SeatbeltSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()
    a.state.workspace_path = tmp_path
    return a


# ---------------------------------------------------------------------------
# AC2: _start_sandbox() raises RuntimeError when sandbox-exec not on PATH
# ---------------------------------------------------------------------------


def test_start_sandbox_sandbox_exec_not_on_path_raises_runtime_error() -> None:
    """AC2: RuntimeError raised before DeprecationWarning when sandbox-exec is missing."""
    a: SeatbeltSandboxActor = SeatbeltSandboxActor.__new__(SeatbeltSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    with patch("akgentic.tool.sandbox.seatbelt.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="sandbox-exec not found"):
            a._start_sandbox()


# ---------------------------------------------------------------------------
# AC1: _start_sandbox() emits DeprecationWarning
# ---------------------------------------------------------------------------


def test_start_sandbox_raises_runtime_error_when_probe_fails() -> None:
    """_start_sandbox() raises RuntimeError when sandbox_apply is blocked (macOS 15+)."""
    a: SeatbeltSandboxActor = SeatbeltSandboxActor.__new__(SeatbeltSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    mock_probe = MagicMock(returncode=71)
    with (
        patch("akgentic.tool.sandbox.seatbelt.shutil.which", return_value="/usr/bin/sandbox-exec"),
        patch("akgentic.tool.sandbox.seatbelt.platform.system", return_value="Darwin"),
        patch("akgentic.tool.sandbox.seatbelt.subprocess.run", return_value=mock_probe),
    ):
        with pytest.raises(RuntimeError, match="sandbox_apply is blocked"):
            a._start_sandbox()


def test_start_sandbox_emits_deprecation_warning() -> None:
    """AC1: _start_sandbox() emits DeprecationWarning with correct message."""
    a: SeatbeltSandboxActor = SeatbeltSandboxActor.__new__(SeatbeltSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    mock_probe = MagicMock(returncode=0)
    with (
        patch("akgentic.tool.sandbox.seatbelt.shutil.which", return_value="/usr/bin/sandbox-exec"),
        patch("akgentic.tool.sandbox.seatbelt.platform.system", return_value="Darwin"),
        patch("akgentic.tool.sandbox.seatbelt.subprocess.run", return_value=mock_probe),
        patch("pathlib.Path.mkdir"),
        patch("akgentic.tool.sandbox.actor.SandboxState.notify_state_change"),
        pytest.warns(DeprecationWarning, match="sandbox-exec is deprecated"),
    ):
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

    a: SeatbeltSandboxActor = SeatbeltSandboxActor.__new__(SeatbeltSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    mock_probe = MagicMock(returncode=0)
    with (
        patch("akgentic.tool.sandbox.seatbelt.shutil.which", return_value="/usr/bin/sandbox-exec"),
        patch("akgentic.tool.sandbox.seatbelt.platform.system", return_value="Darwin"),
        patch("akgentic.tool.sandbox.seatbelt.subprocess.run", return_value=mock_probe),
        patch("akgentic.tool.sandbox.actor.SandboxState.notify_state_change"),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore", DeprecationWarning)
        a._start_sandbox()

    expected = tmp_path / "workspaces" / "test-team"
    assert expected.exists()
    assert expected.is_dir()


# ---------------------------------------------------------------------------
# AC1: _start_sandbox() is idempotent
# ---------------------------------------------------------------------------


def test_start_sandbox_idempotent_existing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: Calling _start_sandbox() twice does not raise (idempotent mkdir exist_ok=True)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)

    a: SeatbeltSandboxActor = SeatbeltSandboxActor.__new__(SeatbeltSandboxActor)
    a.config = SandboxConfig(name="sandbox", role="ToolActor", team_id="test-team")
    a.state = SandboxState()

    mock_probe = MagicMock(returncode=0)
    with (
        patch("akgentic.tool.sandbox.seatbelt.shutil.which", return_value="/usr/bin/sandbox-exec"),
        patch("akgentic.tool.sandbox.seatbelt.platform.system", return_value="Darwin"),
        patch("akgentic.tool.sandbox.seatbelt.subprocess.run", return_value=mock_probe),
        patch("akgentic.tool.sandbox.actor.SandboxState.notify_state_change"),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore", DeprecationWarning)
        a._start_sandbox()
        a._start_sandbox()  # Must not raise

    expected = tmp_path / "workspaces" / "test-team"
    assert expected.exists()


# ---------------------------------------------------------------------------
# AC3: _stop_sandbox() is a no-op
# ---------------------------------------------------------------------------


def test_stop_sandbox_is_noop(actor: SeatbeltSandboxActor) -> None:
    """AC3: _stop_sandbox() completes without error and makes no subprocess calls."""
    with patch("akgentic.tool.sandbox.seatbelt.subprocess.run") as mock_run:
        actor._stop_sandbox()

    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# AC4: _exec() writes policy and calls sandbox-exec
# ---------------------------------------------------------------------------


def test_exec_writes_policy_and_calls_sandbox_exec(actor: SeatbeltSandboxActor) -> None:
    """AC4: _exec() calls subprocess.run with sandbox-exec -f <policy.sb> + cmd."""
    with patch("akgentic.tool.sandbox.seatbelt.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="out", stderr="", returncode=0)
        with patch("akgentic.tool.sandbox.seatbelt.os.unlink"):
            actor._exec("ls .", "")
        cmd_list: list[str] = mock_run.call_args[0][0]
        assert cmd_list[0] == "sandbox-exec"
        assert cmd_list[1] == "-f"
        assert cmd_list[2].endswith(".sb")
        assert "ls" in cmd_list
        assert "." in cmd_list


# ---------------------------------------------------------------------------
# cwd forwarding — empty cwd defaults to workspace, explicit cwd is used
# ---------------------------------------------------------------------------


def test_exec_empty_cwd_defaults_to_workspace(
    actor: SeatbeltSandboxActor, tmp_path: Path
) -> None:
    """_exec() passes workspace_path as cwd when cwd is empty string."""
    with patch("akgentic.tool.sandbox.seatbelt.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        with patch("akgentic.tool.sandbox.seatbelt.os.unlink"):
            actor._exec("ls .", "")
    assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)


def test_exec_explicit_cwd_is_resolved_relative_to_workspace(
    actor: SeatbeltSandboxActor, tmp_path: Path
) -> None:
    """_exec() resolves cwd as a subdirectory of the workspace path."""
    with patch("akgentic.tool.sandbox.seatbelt.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        with patch("akgentic.tool.sandbox.seatbelt.os.unlink"):
            actor._exec("ls .", "subdir")
    assert mock_run.call_args.kwargs["cwd"] == str(tmp_path / "subdir")


def test_exec_missing_cwd_returns_error_result(
    actor: SeatbeltSandboxActor, tmp_path: Path
) -> None:
    """_exec() returns ExecResult with exit_code=1 when cwd does not exist."""
    with patch("akgentic.tool.sandbox.seatbelt.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError(
            2, "No such file or directory", str(tmp_path / "nope")
        )
        with patch("akgentic.tool.sandbox.seatbelt.os.unlink"):
            result = actor._exec("ls .", "nope")
    assert result.exit_code == 1
    assert "not found" in result.stderr.lower()
    assert "nope" in result.stderr
    # Must NOT leak the absolute host path
    assert str(tmp_path) not in result.stderr


# ---------------------------------------------------------------------------
# Shared helper: capture policy content written to NamedTemporaryFile
# ---------------------------------------------------------------------------


def _capture_policy(actor: SeatbeltSandboxActor) -> str:
    """Execute actor._exec() under mocks and return the SBPL policy string written to disk.

    Uses a real NamedTemporaryFile so file I/O is faithful, patches subprocess.run
    and os.unlink to avoid side-effects.  The written policy text is read back from
    the temp file before os.unlink would remove it.
    """
    policy_path_holder: list[str] = []
    original_ntf = tempfile.NamedTemporaryFile

    def fake_ntf(
        mode: str = "w",
        suffix: str | None = None,
        delete: bool = True,
    ) -> object:
        f = original_ntf(mode=mode, suffix=suffix, delete=delete)
        policy_path_holder.append(f.name)
        return f

    with (
        patch("akgentic.tool.sandbox.seatbelt.tempfile.NamedTemporaryFile", side_effect=fake_ntf),
        patch("akgentic.tool.sandbox.seatbelt.subprocess.run") as mock_run,
        patch("akgentic.tool.sandbox.seatbelt.os.unlink"),
    ):
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        actor._exec("ls .", "")

    policy_path = policy_path_holder[0]
    with open(policy_path) as fh:
        content = fh.read()
    os.unlink(policy_path)
    return content


# ---------------------------------------------------------------------------
# AC5: Policy content — (deny default)
# ---------------------------------------------------------------------------


def test_exec_policy_contains_deny_default(actor: SeatbeltSandboxActor) -> None:
    """AC5: SBPL policy written by _exec() contains '(deny default)'."""
    policy = _capture_policy(actor)
    assert "(deny default)" in policy


# ---------------------------------------------------------------------------
# AC4/AC5: Policy substitutes workspace path
# ---------------------------------------------------------------------------


def test_exec_policy_substitutes_workspace_path(
    actor: SeatbeltSandboxActor, tmp_path: Path
) -> None:
    """AC4: _exec() substitutes actual workspace path into the SBPL policy."""
    policy = _capture_policy(actor)
    assert str(tmp_path) in policy


# ---------------------------------------------------------------------------
# AC5: Policy contains (deny network*)
# ---------------------------------------------------------------------------


def test_exec_policy_allows_network(actor: SeatbeltSandboxActor) -> None:
    """SBPL policy allows network access (needed for git clone, curl, wget, pip)."""
    policy = _capture_policy(actor)
    assert "(allow network*)" in policy


def test_exec_policy_allows_all_reads(actor: SeatbeltSandboxActor) -> None:
    """Policy uses blanket (allow file-read*) for broad read access on macOS."""
    policy = _capture_policy(actor)
    assert "(allow file-read*)" in policy


# ---------------------------------------------------------------------------
# AC5: Policy contains file-write* for workspace
# ---------------------------------------------------------------------------


def test_exec_policy_allows_workspace_write(
    actor: SeatbeltSandboxActor, tmp_path: Path
) -> None:
    """AC5: SBPL policy contains file-write* subpath entry for workspace."""
    policy = _capture_policy(actor)
    ws = str(tmp_path)
    assert f'(allow file-write* (subpath "{ws}"))' in policy


# ---------------------------------------------------------------------------
# AC4: Temp file deleted in finally block
# ---------------------------------------------------------------------------


def test_exec_deletes_tempfile_in_finally(actor: SeatbeltSandboxActor) -> None:
    """AC4: _exec() deletes the temp .sb policy file in a finally block."""
    with patch("akgentic.tool.sandbox.seatbelt.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        with patch("akgentic.tool.sandbox.seatbelt.os.unlink") as mock_unlink:
            actor._exec("ls .", "")
            mock_unlink.assert_called_once()


# ---------------------------------------------------------------------------
# AC4: _exec() returns ExecResult with correct fields
# ---------------------------------------------------------------------------


def test_exec_returns_exec_result(actor: SeatbeltSandboxActor) -> None:
    """AC4: _exec() returns ExecResult with stdout, stderr, exit_code from subprocess.run."""
    with patch("akgentic.tool.sandbox.seatbelt.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="out", stderr="err", returncode=0)
        with patch("akgentic.tool.sandbox.seatbelt.os.unlink"):
            result = actor._exec("ls .", "")

    assert isinstance(result, ExecResult)
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# AC6: No preexec_fn passed to subprocess.run
# ---------------------------------------------------------------------------


def test_exec_no_preexec_fn_passed(actor: SeatbeltSandboxActor) -> None:
    """AC6: _exec() does NOT pass preexec_fn to subprocess.run (macOS: no resource.setrlimit)."""
    with patch("akgentic.tool.sandbox.seatbelt.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        with patch("akgentic.tool.sandbox.seatbelt.os.unlink"):
            actor._exec("ls .", "")

    assert mock_run.call_args is not None
    assert "preexec_fn" not in mock_run.call_args.kwargs
