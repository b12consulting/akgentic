"""Tests for LocalSandboxActor — subprocess-based sandbox execution.

Covers AC1 through AC7 for Story 6.2 (updated for Story 6.5):
- _start_sandbox() creates workspace directory and stores absolute path
- _start_sandbox() uses AKGENTIC_WORKSPACES_ROOT (default ./workspaces)
- _start_sandbox() is idempotent (calling twice does not raise)
- _stop_sandbox() is a no-op
- _exec() with empty cwd uses state.workspace_path directly
- _exec() with non-empty cwd uses state.workspace_path / cwd
- _exec() returns ExecResult with correct stdout, stderr, exit_code
- subprocess.TimeoutExpired propagates from _exec()
- exec() public method works end-to-end through _exec()

Story 8.1: Resource limits and environment stripping (AC: 1, 2, 3, 4, 5):
- preexec_fn is passed to subprocess.run (mock assert) — AC5
- env is stripped to minimal PATH (mock assert) — AC5
- env dict has exactly one key — AC5
- docstring contains isolation disclaimer — AC4 / AC5
- _make_preexec() callable sets RLIMIT_CPU, RLIMIT_AS, RLIMIT_FSIZE and calls setpgrp — AC1 / AC3
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from akgentic.tool.sandbox.actor import (
    ExecResult,
    SandboxConfig,
    SandboxState,
)
from akgentic.tool.sandbox.local import _SAFE_ENV_KEYS, LocalSandboxActor

# ---------------------------------------------------------------------------
# Helper factory
# ---------------------------------------------------------------------------


def make_actor(team_id: str = "team-test", workspace_id: str | None = None) -> LocalSandboxActor:
    """Create a LocalSandboxActor with config and state pre-initialized (no Pykka runtime)."""
    actor = LocalSandboxActor()
    actor.config = SandboxConfig(
        name="sandbox", role="ToolActor", team_id=team_id, workspace_id=workspace_id
    )
    actor.state = SandboxState()
    actor.state.observer(actor)
    return actor


# ---------------------------------------------------------------------------
# AC1: _start_sandbox() creates workspace and stores absolute path
# ---------------------------------------------------------------------------


def test_start_sandbox_creates_workspace_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: _start_sandbox() creates ./workspaces/team-1/ relative to CWD (default root)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")

    actor._start_sandbox()

    expected = tmp_path / "workspaces" / "team-1"
    assert expected.exists()
    assert expected.is_dir()


def test_start_sandbox_stores_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: _start_sandbox() stores resolved absolute path in state.workspace_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")

    actor._start_sandbox()

    assert actor.state.workspace_path is not None
    assert actor.state.workspace_path.is_absolute()
    assert actor.state.workspace_path == (tmp_path / "workspaces" / "team-1").resolve()


def test_start_sandbox_uses_custom_workspaces_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 6.5: _start_sandbox() uses AKGENTIC_WORKSPACES_ROOT when set."""
    custom_root = tmp_path / "my-custom-root"
    monkeypatch.setenv("AKGENTIC_WORKSPACES_ROOT", str(custom_root))
    actor = make_actor(team_id="team-1")

    actor._start_sandbox()

    expected = custom_root / "team-1"
    assert expected.exists()
    assert expected.is_dir()
    assert actor.state.workspace_path == expected.resolve()


def test_start_sandbox_calls_notify_state_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: _start_sandbox() calls state.notify_state_change() — verified via class-level patch."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")

    with patch("akgentic.tool.sandbox.actor.SandboxState.notify_state_change") as mock_notify:
        actor._start_sandbox()
        mock_notify.assert_called_once()


# ---------------------------------------------------------------------------
# AC2: _start_sandbox() is idempotent
# ---------------------------------------------------------------------------


def test_start_sandbox_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC2: Calling _start_sandbox() twice does not raise."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")

    actor._start_sandbox()
    actor._start_sandbox()  # Must not raise

    expected = tmp_path / "workspaces" / "team-1"
    assert expected.exists()


# ---------------------------------------------------------------------------
# AC3: _stop_sandbox() is a no-op
# ---------------------------------------------------------------------------


def test_stop_sandbox_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: _stop_sandbox() returns None and has no side effects."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    result = actor._stop_sandbox()

    assert result is None


def test_stop_sandbox_does_not_remove_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: _stop_sandbox() leaves the workspace directory intact."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    actor._stop_sandbox()

    expected = tmp_path / "workspaces" / "team-1"
    assert expected.exists()


# ---------------------------------------------------------------------------
# AC4: _exec() with empty cwd uses state.workspace_path
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.local.subprocess.run")
def test_exec_no_cwd_uses_workspace_path(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC4: _exec(cmd, cwd='') passes state.workspace_path as cwd to subprocess.run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)

    actor._exec("pytest tests/", "")

    call_kwargs = mock_run.call_args
    assert call_kwargs is not None
    assert call_kwargs.args[0] == ["pytest", "tests/"]
    assert call_kwargs.kwargs["cwd"] == str(actor.state.workspace_path)
    assert call_kwargs.kwargs["capture_output"] is True
    assert call_kwargs.kwargs["text"] is True
    assert call_kwargs.kwargs["timeout"] == 30
    assert call_kwargs.kwargs["preexec_fn"] is not None
    assert callable(call_kwargs.kwargs["preexec_fn"])
    env = call_kwargs.kwargs["env"]
    assert "PATH" in env
    unexpected = set(env) - _SAFE_ENV_KEYS
    assert not unexpected, f"Unexpected keys in env: {unexpected}"


# ---------------------------------------------------------------------------
# AC5: _exec() with non-empty cwd uses state.workspace_path / cwd
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.local.subprocess.run")
def test_exec_with_cwd_appends_to_workspace_path(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5: _exec(cmd, cwd='src') passes state.workspace_path / 'src' as cwd."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)

    actor._exec("pytest tests/", "src")

    expected_cwd = str(actor.state.workspace_path / "src")
    call_kwargs = mock_run.call_args
    assert call_kwargs is not None
    assert call_kwargs.args[0] == ["pytest", "tests/"]
    assert call_kwargs.kwargs["cwd"] == expected_cwd
    assert call_kwargs.kwargs["capture_output"] is True
    assert call_kwargs.kwargs["text"] is True
    assert call_kwargs.kwargs["timeout"] == 30
    assert call_kwargs.kwargs["preexec_fn"] is not None
    assert callable(call_kwargs.kwargs["preexec_fn"])
    env = call_kwargs.kwargs["env"]
    assert "PATH" in env
    unexpected = set(env) - _SAFE_ENV_KEYS
    assert not unexpected, f"Unexpected keys in env: {unexpected}"


# ---------------------------------------------------------------------------
# AC4 / AC5: _exec() returns ExecResult with correct fields
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.local.subprocess.run")
def test_exec_returns_exec_result(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_exec() returns ExecResult with stdout, stderr, exit_code from subprocess.run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    mock_run.return_value = MagicMock(stdout="test passed", stderr="warning", returncode=0)

    result = actor._exec("pytest tests/", "")

    assert isinstance(result, ExecResult)
    assert result.stdout == "test passed"
    assert result.stderr == "warning"
    assert result.exit_code == 0


@patch("akgentic.tool.sandbox.local.subprocess.run")
def test_exec_captures_non_zero_exit_code(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_exec() correctly captures non-zero exit codes from subprocess.run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    mock_run.return_value = MagicMock(stdout="", stderr="test failed", returncode=1)

    result = actor._exec("pytest tests/", "")

    assert result.exit_code == 1
    assert result.stderr == "test failed"


# ---------------------------------------------------------------------------
# AC6: subprocess.TimeoutExpired propagates
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.local.subprocess.run")
def test_exec_timeout_propagates(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: subprocess.TimeoutExpired from subprocess.run propagates out of _exec()."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["sleep", "999"], timeout=30)

    with pytest.raises(subprocess.TimeoutExpired):
        actor._exec("sleep 999", "")


# ---------------------------------------------------------------------------
# AC7: exec() public method works end-to-end
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.local.subprocess.run")
def test_public_exec_method_end_to_end(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC7: actor.exec() through inherited SandboxActor.exec() delegates to _exec()."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor.on_start()  # Use the full lifecycle (sets state via SandboxActor.on_start)

    mock_run.return_value = MagicMock(stdout="passed", stderr="", returncode=0)

    result = actor.exec("pytest tests/")

    assert isinstance(result, ExecResult)
    assert result.stdout == "passed"
    assert result.exit_code == 0
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Story 6.5: shared-filesystem invariant — WorkspaceTool and LocalSandboxActor
# resolve to the same absolute path for the same team_id
# ---------------------------------------------------------------------------


def test_workspace_tool_and_local_sandbox_actor_resolve_same_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 6.5 AC: WorkspaceTool (via get_workspace) and LocalSandboxActor share the same
    workspace root when AKGENTIC_WORKSPACES_ROOT is unset.

    Both tools must resolve to the same absolute path for team-1 to guarantee
    the shared-filesystem invariant (ADR-006).
    """
    from akgentic.tool.workspace.workspace import get_workspace

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)

    # WorkspaceTool path (via get_workspace factory)
    workspace = get_workspace("team-1")
    workspace_tool_root = workspace._root.resolve()

    # LocalSandboxActor path (via _start_sandbox())
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()
    sandbox_root = actor.state.workspace_path

    assert sandbox_root is not None
    assert workspace_tool_root == sandbox_root, (
        f"WorkspaceTool root ({workspace_tool_root}) != "
        f"LocalSandboxActor root ({sandbox_root})"
    )


def test_workspace_tool_and_local_sandbox_actor_resolve_same_path_custom_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 6.5 AC: Both tools use custom AKGENTIC_WORKSPACES_ROOT and resolve to same path."""
    from akgentic.tool.workspace.workspace import get_workspace

    custom_root = tmp_path / "shared-workspaces"
    monkeypatch.setenv("AKGENTIC_WORKSPACES_ROOT", str(custom_root))

    # WorkspaceTool path
    workspace = get_workspace("team-1")
    workspace_tool_root = workspace._root.resolve()

    # LocalSandboxActor path
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()
    sandbox_root = actor.state.workspace_path

    assert sandbox_root is not None
    assert workspace_tool_root == sandbox_root, (
        f"WorkspaceTool root ({workspace_tool_root}) != "
        f"LocalSandboxActor root ({sandbox_root})"
    )


# ---------------------------------------------------------------------------
# Story 6.6: workspace_id overrides team_id for workspace directory name
# ---------------------------------------------------------------------------


def test_start_sandbox_workspace_id_overrides_team_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-SB-33: When workspace_id is set, workspace path uses workspace_id, not team_id."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1", workspace_id="test")

    actor._start_sandbox()

    expected = tmp_path / "workspaces" / "test"
    assert expected.exists()
    assert expected.is_dir()
    assert actor.state.workspace_path == expected.resolve()


def test_start_sandbox_workspace_id_none_falls_back_to_team_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-SB-33: When workspace_id is None, workspace path uses team_id (unchanged default)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1", workspace_id=None)

    actor._start_sandbox()

    expected = tmp_path / "workspaces" / "team-1"
    assert expected.exists()
    assert actor.state.workspace_path == expected.resolve()


def test_exec_tool_and_workspace_tool_resolve_same_path_via_workspace_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-SB-35: ExecTool(workspace_id='test') and WorkspaceTool(workspace_id='test')
    resolve to the same absolute directory when AKGENTIC_WORKSPACES_ROOT is set.
    """
    from akgentic.tool.workspace.workspace import get_workspace

    monkeypatch.setenv("AKGENTIC_WORKSPACES_ROOT", str(tmp_path / "workspaces"))

    # WorkspaceTool path via get_workspace (mirrors WorkspaceReadTool.observer logic)
    workspace = get_workspace("test")
    workspace_tool_root = workspace._root.resolve()

    # LocalSandboxActor path with workspace_id="test"
    actor = make_actor(team_id="team-1", workspace_id="test")
    actor._start_sandbox()
    sandbox_root = actor.state.workspace_path

    assert sandbox_root is not None
    assert workspace_tool_root == sandbox_root, (
        f"WorkspaceTool root ({workspace_tool_root}) != "
        f"LocalSandboxActor root ({sandbox_root})"
    )


# ---------------------------------------------------------------------------
# Story 8.1: Resource limits and environment stripping (AC: 1, 2, 5)
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.local.subprocess.run")
def test_exec_passes_preexec_fn_to_subprocess(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5 (Story 8.1): _exec() passes a non-None callable preexec_fn to subprocess.run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)

    actor._exec("echo hello", "")

    call_kwargs = mock_run.call_args
    assert call_kwargs is not None
    preexec_fn = call_kwargs.kwargs.get("preexec_fn")
    assert preexec_fn is not None
    assert callable(preexec_fn)


@patch("akgentic.tool.sandbox.local.subprocess.run")
def test_exec_strips_env_to_safe_keys(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5 (Story 8.1): _exec() passes env with only safe keys to subprocess.run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)

    actor._exec("echo hello", "")

    call_kwargs = mock_run.call_args
    assert call_kwargs is not None
    env = call_kwargs.kwargs.get("env")
    assert isinstance(env, dict)
    assert "PATH" in env
    assert all(k in _SAFE_ENV_KEYS for k in env), f"Unexpected keys: {set(env) - _SAFE_ENV_KEYS}"


@patch("akgentic.tool.sandbox.local.subprocess.run")
def test_exec_env_excludes_secrets(
    mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5 (Story 8.1): env dict does not contain API keys or secrets."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)

    actor._exec("echo hello", "")

    call_kwargs = mock_run.call_args
    assert call_kwargs is not None
    env = call_kwargs.kwargs.get("env")
    assert isinstance(env, dict)
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_docstring_contains_isolation_disclaimer() -> None:
    """AC4 / AC5 (Story 8.1): LocalSandboxActor docstring contains isolation disclaimer."""
    assert LocalSandboxActor.__doc__ is not None
    assert "does NOT provide filesystem isolation" in LocalSandboxActor.__doc__


# ---------------------------------------------------------------------------
# Story 8.1: _make_preexec() unit tests — verify actual resource-limit logic
# ---------------------------------------------------------------------------


def test_make_preexec_returns_callable() -> None:
    """AC1 (Story 8.1): _make_preexec() returns a callable (not None)."""
    from akgentic.tool.sandbox.local import _make_preexec

    fn = _make_preexec()
    assert callable(fn)


def test_make_preexec_custom_defaults_returns_callable() -> None:
    """AC1 (Story 8.1): _make_preexec() with custom args returns a callable."""
    from akgentic.tool.sandbox.local import _make_preexec

    fn = _make_preexec(cpu_s=10, mem_mb=256, fsize_mb=50)
    assert callable(fn)


def test_make_preexec_fn_sets_resource_limits() -> None:
    """AC1 / AC3 (Story 8.1): The callable returned by _make_preexec() sets RLIMIT_CPU,
    RLIMIT_AS, and RLIMIT_FSIZE to the expected values, and calls os.setpgrp().

    We mock resource.setrlimit and os.setpgrp to avoid altering the test process's
    resource limits.
    """
    import resource as resource_module
    from unittest.mock import call, patch

    from akgentic.tool.sandbox.local import _make_preexec

    fn = _make_preexec(cpu_s=30, mem_mb=512, fsize_mb=100)

    with (
        patch.object(resource_module, "setrlimit") as mock_setrlimit,
        patch("os.setpgrp") as mock_setpgrp,
        patch("akgentic.tool.sandbox.local.sys") as mock_sys,
    ):
        mock_sys.platform = "linux"
        fn()

    mb = 1024**2
    expected_calls = [
        call(resource_module.RLIMIT_CPU, (30, 30)),
        call(resource_module.RLIMIT_AS, (512 * mb, 512 * mb)),
        call(resource_module.RLIMIT_FSIZE, (100 * mb, 100 * mb)),
    ]
    mock_setrlimit.assert_has_calls(expected_calls, any_order=False)
    mock_setpgrp.assert_called_once()


# ---------------------------------------------------------------------------
# Story 8.5: Darwin platform guard for RLIMIT_AS (AC: 1, 2)
# ---------------------------------------------------------------------------


def test_make_preexec_skips_rlimit_as_on_darwin() -> None:
    """AC1 (Story 8.5): On macOS (Darwin), RLIMIT_AS is NOT set,
    while RLIMIT_CPU and RLIMIT_FSIZE are still applied.
    """
    import resource as resource_module
    from unittest.mock import call, patch

    from akgentic.tool.sandbox.local import _make_preexec

    fn = _make_preexec(cpu_s=30, mem_mb=512, fsize_mb=100)

    with (
        patch.object(resource_module, "setrlimit") as mock_setrlimit,
        patch("os.setpgrp") as mock_setpgrp,
        patch("akgentic.tool.sandbox.local.sys") as mock_sys,
    ):
        mock_sys.platform = "darwin"
        fn()

    mb = 1024**2
    expected_calls = [
        call(resource_module.RLIMIT_CPU, (30, 30)),
        call(resource_module.RLIMIT_FSIZE, (100 * mb, 100 * mb)),
    ]
    mock_setrlimit.assert_has_calls(expected_calls, any_order=False)
    # Verify RLIMIT_AS was NOT set
    for c in mock_setrlimit.call_args_list:
        assert c[0][0] != resource_module.RLIMIT_AS, "RLIMIT_AS should not be set on Darwin"
    mock_setpgrp.assert_called_once()


def test_make_preexec_sets_rlimit_as_on_linux() -> None:
    """AC2 (Story 8.5): On Linux, all three limits (RLIMIT_CPU, RLIMIT_AS,
    RLIMIT_FSIZE) are set as before.
    """
    import resource as resource_module
    from unittest.mock import call, patch

    from akgentic.tool.sandbox.local import _make_preexec

    fn = _make_preexec(cpu_s=30, mem_mb=512, fsize_mb=100)

    with (
        patch.object(resource_module, "setrlimit") as mock_setrlimit,
        patch("os.setpgrp") as mock_setpgrp,
        patch("akgentic.tool.sandbox.local.sys") as mock_sys,
    ):
        mock_sys.platform = "linux"
        fn()

    mb = 1024**2
    expected_calls = [
        call(resource_module.RLIMIT_CPU, (30, 30)),
        call(resource_module.RLIMIT_AS, (512 * mb, 512 * mb)),
        call(resource_module.RLIMIT_FSIZE, (100 * mb, 100 * mb)),
    ]
    mock_setrlimit.assert_has_calls(expected_calls, any_order=False)
    assert mock_setrlimit.call_count == 3
    mock_setpgrp.assert_called_once()
