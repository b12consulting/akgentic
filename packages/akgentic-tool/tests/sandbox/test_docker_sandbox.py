"""Tests for DockerSandboxActor — persistent Docker container execution.

Covers AC1 through AC11 for Story 6.3 (updated for Story 6.5):
- AC1: _start_sandbox() runs docker run when container does not exist
- AC2: _start_sandbox() runs docker start when container already exists
- AC3: _start_sandbox() raises RuntimeError when docker CLI not on PATH
- AC4: _stop_sandbox() runs docker stop and does NOT run docker rm
- AC5: Exceptions from _stop_sandbox() swallowed by SandboxActor.on_stop() (base class)
- AC6: _exec("pytest tests/", cwd="src") builds docker exec -w /workspace/src
- AC7: _exec("pytest tests/", cwd="") builds docker exec -w /workspace
- AC8: state.container_name is set after _start_sandbox() (volume sharing by convention)
- AC9: SANDBOX_IMAGE == "akgentic-sandbox:latest"
- AC10: DOCKER_EXEC_TIMEOUT == 60
- AC11: 80%+ branch coverage
- Story 6.5: volume mount host path derived from AKGENTIC_WORKSPACES_ROOT
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
from akgentic.tool.sandbox.docker import (
    DOCKER_EXEC_TIMEOUT,
    SANDBOX_IMAGE,
    DockerSandboxActor,
)

# ---------------------------------------------------------------------------
# Helper factory
# ---------------------------------------------------------------------------


def make_actor(
    team_id: str = "team-test", workspace_id: str | None = None
) -> DockerSandboxActor:
    """Create a DockerSandboxActor with config and state pre-initialized (no Pykka runtime)."""
    actor = DockerSandboxActor()
    actor.config = SandboxConfig(
        name="sandbox", role="ToolActor", team_id=team_id, workspace_id=workspace_id
    )
    actor.state = SandboxState()
    actor.state.observer(actor)
    return actor


# ---------------------------------------------------------------------------
# AC9: SANDBOX_IMAGE constant
# ---------------------------------------------------------------------------


def test_sandbox_image_constant() -> None:
    """AC9: SANDBOX_IMAGE equals 'akgentic-sandbox:latest'."""
    assert SANDBOX_IMAGE == "akgentic-sandbox:latest"


# ---------------------------------------------------------------------------
# AC10: DOCKER_EXEC_TIMEOUT constant
# ---------------------------------------------------------------------------


def test_docker_exec_timeout_constant() -> None:
    """AC10: DOCKER_EXEC_TIMEOUT equals 60."""
    assert DOCKER_EXEC_TIMEOUT == 60


# ---------------------------------------------------------------------------
# AC1: _start_sandbox() runs docker run when container does not exist
# ---------------------------------------------------------------------------


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_creates_container_when_absent(
    mock_run: MagicMock, mock_which: MagicMock, mock_ensure: MagicMock
) -> None:
    """AC1: _start_sandbox() runs docker run -d when container does not exist."""
    # First call: docker ps -a → returns empty stdout (container not found)
    # Second call: docker run -d → returns success
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    assert actor.state.container_name == "sandbox-team-1"
    # Verify docker run was called (second call), not docker start
    second_call_args = mock_run.call_args_list[1][0][0]
    assert second_call_args[1] == "run"


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_docker_run_uses_correct_flags(
    mock_run: MagicMock,
    mock_which: MagicMock,
    mock_ensure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: docker run uses -d, --name, --network none, -v, -w, sleep infinity (default root)."""
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    run_call_args = mock_run.call_args_list[1][0][0]
    expected_volume = f"{Path('./workspaces/team-1').resolve()}:/workspace"
    assert run_call_args == [
        "docker",
        "run",
        "-d",
        "--name",
        "sandbox-team-1",
        "-v",
        expected_volume,
        "-w",
        "/workspace",
        SANDBOX_IMAGE,
        "sleep",
        "infinity",
    ]


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_docker_run_uses_custom_workspaces_root(
    mock_run: MagicMock,
    mock_which: MagicMock,
    mock_ensure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 6.5: docker run -v uses AKGENTIC_WORKSPACES_ROOT when set."""
    monkeypatch.setenv("AKGENTIC_WORKSPACES_ROOT", "/workspaces")
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    run_call_args = mock_run.call_args_list[1][0][0]
    volume_arg_idx = run_call_args.index("-v") + 1
    assert run_call_args[volume_arg_idx] == "/workspaces/team-1:/workspace"


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_docker_run_normalizes_trailing_slash_in_root(
    mock_run: MagicMock,
    mock_which: MagicMock,
    mock_ensure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 6.5: AKGENTIC_WORKSPACES_ROOT with trailing slash must NOT produce double-slash volume.

    e.g. '/workspaces/' must yield '/workspaces/team-1:/workspace', not '/workspaces//team-1:/workspace'.
    Path() normalizes this automatically.
    """
    monkeypatch.setenv("AKGENTIC_WORKSPACES_ROOT", "/workspaces/")
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    run_call_args = mock_run.call_args_list[1][0][0]
    volume_arg_idx = run_call_args.index("-v") + 1
    volume = run_call_args[volume_arg_idx]
    assert volume == "/workspaces/team-1:/workspace", (
        f"Volume mount must not contain double slash: got '{volume}'"
    )


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_sets_container_name_in_state(
    mock_run: MagicMock, mock_which: MagicMock, mock_ensure: MagicMock
) -> None:
    """AC1/AC8: After _start_sandbox(), state.container_name is set to 'sandbox-{team_id}'."""
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    assert actor.state.container_name == "sandbox-team-1"


# ---------------------------------------------------------------------------
# AC2: _start_sandbox() runs docker start when container already exists
# ---------------------------------------------------------------------------


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_reuses_existing_container(
    mock_run: MagicMock, mock_which: MagicMock, mock_ensure: MagicMock
) -> None:
    """AC2: _start_sandbox() runs docker start when container already exists (any state)."""
    # First call: docker ps -a → container name in stdout
    # Second call: docker start → returns success
    mock_run.side_effect = [
        MagicMock(stdout="sandbox-team-1\n", returncode=0),  # docker ps -a
        MagicMock(stdout="sandbox-team-1", returncode=0),  # docker start
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    second_call_args = mock_run.call_args_list[1][0][0]
    assert second_call_args[1] == "start"
    assert actor.state.container_name == "sandbox-team-1"


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_docker_start_uses_correct_args(
    mock_run: MagicMock, mock_which: MagicMock, mock_ensure: MagicMock
) -> None:
    """AC2: docker start is called with the correct container name."""
    mock_run.side_effect = [
        MagicMock(stdout="sandbox-team-1\n", returncode=0),  # docker ps -a
        MagicMock(stdout="sandbox-team-1", returncode=0),  # docker start
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    start_call_args = mock_run.call_args_list[1][0][0]
    assert start_call_args == ["docker", "start", "sandbox-team-1"]


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_no_false_positive_on_prefix_container_name(
    mock_run: MagicMock, mock_which: MagicMock, mock_ensure: MagicMock
) -> None:
    """AC1: container name prefix in stdout does not trigger false reuse.

    If 'sandbox-team-10' appears in stdout, checking for 'sandbox-team-1'
    must NOT match — exact line matching is required.
    """
    # docker ps -a stdout contains a prefix-matching name, not exact match
    mock_run.side_effect = [
        MagicMock(stdout="sandbox-team-10\n", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run (not docker start)
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    # Must run docker run, NOT docker start
    second_call_args = mock_run.call_args_list[1][0][0]
    assert second_call_args[1] == "run"
    assert actor.state.container_name == "sandbox-team-1"


# ---------------------------------------------------------------------------
# AC3: _start_sandbox() raises RuntimeError when docker not on PATH
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.docker.shutil.which", return_value=None)
def test_start_sandbox_raises_when_docker_not_on_path(mock_which: MagicMock) -> None:
    """AC3: _start_sandbox() raises RuntimeError when shutil.which('docker') returns None."""
    actor = make_actor()
    with pytest.raises(RuntimeError, match="docker"):
        actor._start_sandbox()


@patch("akgentic.tool.sandbox.docker.shutil.which", return_value=None)
def test_start_sandbox_runtime_error_message(mock_which: MagicMock) -> None:
    """AC3: RuntimeError message mentions docker CLI."""
    actor = make_actor()
    with pytest.raises(RuntimeError, match="docker CLI not found on PATH"):
        actor._start_sandbox()


# ---------------------------------------------------------------------------
# AC4: _stop_sandbox() runs docker stop and does NOT run docker rm
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_stop_sandbox_runs_docker_stop(mock_run: MagicMock) -> None:
    """AC4: _stop_sandbox() calls docker stop with the container name."""
    actor = make_actor(team_id="team-1")
    actor.state.container_name = "sandbox-team-1"
    mock_run.return_value = MagicMock(returncode=0)

    actor._stop_sandbox()

    assert mock_run.call_args_list[0][0][0] == ["docker", "stop", "sandbox-team-1"]


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_stop_sandbox_does_not_rm_container(mock_run: MagicMock) -> None:
    """AC4: _stop_sandbox() does NOT run docker rm — container preserved between restarts."""
    actor = make_actor(team_id="team-1")
    actor.state.container_name = "sandbox-team-1"
    mock_run.return_value = MagicMock(returncode=0)

    actor._stop_sandbox()

    # Verify docker rm was NOT called in any subprocess.run call
    for call_item in mock_run.call_args_list:
        assert "rm" not in call_item[0][0]


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_stop_sandbox_only_one_subprocess_call(mock_run: MagicMock) -> None:
    """AC4: _stop_sandbox() makes exactly one subprocess.run call (docker stop only)."""
    actor = make_actor(team_id="team-1")
    actor.state.container_name = "sandbox-team-1"
    mock_run.return_value = MagicMock(returncode=0)

    actor._stop_sandbox()

    assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# AC5: on_stop() swallows _stop_sandbox() exceptions (inherited behavior)
# ---------------------------------------------------------------------------


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_on_stop_swallows_stop_sandbox_exception(
    mock_run: MagicMock, mock_which: MagicMock, mock_ensure: MagicMock
) -> None:
    """AC5: on_stop() swallows exceptions raised by _stop_sandbox() — base class behavior."""
    # Start sandbox first to set container_name
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
        subprocess.CalledProcessError(1, "docker stop"),  # docker stop raises
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    # on_stop() should not raise even though docker stop fails
    actor.on_stop()  # Must not raise


# ---------------------------------------------------------------------------
# AC6: _exec() with cwd builds docker exec -w /workspace/src
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_exec_with_cwd_builds_correct_docker_command(mock_run: MagicMock) -> None:
    """AC6: _exec('pytest tests/', cwd='src') builds docker exec -w /workspace/src."""
    actor = make_actor(team_id="team-1")
    actor.state.container_name = "sandbox-team-1"
    mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

    actor._exec("pytest tests/", "src")

    expected_cmd = [
        "docker",
        "exec",
        "-w",
        "/workspace/src",
        "sandbox-team-1",
        "pytest",
        "tests/",
    ]
    mock_run.assert_called_once_with(
        expected_cmd,
        capture_output=True,
        text=True,
        timeout=DOCKER_EXEC_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# AC7: _exec() with empty cwd builds docker exec -w /workspace
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_exec_without_cwd_uses_workspace_root(mock_run: MagicMock) -> None:
    """AC7: _exec('pytest tests/', cwd='') builds docker exec -w /workspace (no trailing slash)."""
    actor = make_actor(team_id="team-1")
    actor.state.container_name = "sandbox-team-1"
    mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

    actor._exec("pytest tests/", "")

    expected_cmd = [
        "docker",
        "exec",
        "-w",
        "/workspace",
        "sandbox-team-1",
        "pytest",
        "tests/",
    ]
    mock_run.assert_called_once_with(
        expected_cmd,
        capture_output=True,
        text=True,
        timeout=DOCKER_EXEC_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# _exec() returns ExecResult with correct fields
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_exec_returns_exec_result_with_correct_fields(mock_run: MagicMock) -> None:
    """_exec() returns ExecResult with stdout, stderr, exit_code from mocked subprocess."""
    actor = make_actor(team_id="team-1")
    actor.state.container_name = "sandbox-team-1"
    mock_run.return_value = MagicMock(stdout="test passed", stderr="warning", returncode=0)

    result = actor._exec("pytest tests/", "")

    assert isinstance(result, ExecResult)
    assert result.stdout == "test passed"
    assert result.stderr == "warning"
    assert result.exit_code == 0


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_exec_captures_non_zero_exit_code(mock_run: MagicMock) -> None:
    """_exec() correctly captures non-zero exit codes."""
    actor = make_actor(team_id="team-1")
    actor.state.container_name = "sandbox-team-1"
    mock_run.return_value = MagicMock(stdout="", stderr="test failed", returncode=1)

    result = actor._exec("pytest tests/", "")

    assert result.exit_code == 1
    assert result.stderr == "test failed"


# ---------------------------------------------------------------------------
# _exec() timeout propagates
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_exec_timeout_propagates(mock_run: MagicMock) -> None:
    """_exec() propagates subprocess.TimeoutExpired — not swallowed."""
    actor = make_actor(team_id="team-1")
    actor.state.container_name = "sandbox-team-1"
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["docker", "exec"], timeout=60)

    with pytest.raises(subprocess.TimeoutExpired):
        actor._exec("pytest tests/", "")


# ---------------------------------------------------------------------------
# AC8: Integration assertion — state.container_name set after _start_sandbox()
# ---------------------------------------------------------------------------


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_integration_container_name_set_after_start(
    mock_run: MagicMock, mock_which: MagicMock, mock_ensure: MagicMock
) -> None:
    """AC8: After _start_sandbox(), state.container_name is set — volume mount ensures sharing."""
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    # Volume sharing is by design convention (-v workspaces/team-1:/workspace)
    # assert container_name is set (and thus the mount is active)
    assert actor.state.container_name == "sandbox-team-1"


# ---------------------------------------------------------------------------
# notify_state_change is called after _start_sandbox()
# ---------------------------------------------------------------------------


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_calls_notify_state_change(
    mock_run: MagicMock, mock_which: MagicMock, mock_ensure: MagicMock
) -> None:
    """_start_sandbox() calls state.notify_state_change() after setting container_name."""
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="team-1")

    with patch("akgentic.tool.sandbox.actor.SandboxState.notify_state_change") as mock_notify:
        actor._start_sandbox()
        mock_notify.assert_called_once()


# ---------------------------------------------------------------------------
# Story 6.6: workspace_id overrides team_id for volume mount; container name unchanged
# ---------------------------------------------------------------------------


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_workspace_id_overrides_volume_mount(
    mock_run: MagicMock,
    mock_which: MagicMock,
    mock_ensure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-SB-34: workspace_id='test' makes volume mount use 'test', not team_id."""
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="t1", workspace_id="test")
    actor._start_sandbox()

    run_call_args = mock_run.call_args_list[1][0][0]
    volume_arg_idx = run_call_args.index("-v") + 1
    assert run_call_args[volume_arg_idx] == f"{Path('./workspaces/test').resolve()}:/workspace"


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_container_name_uses_team_id_not_workspace_id(
    mock_run: MagicMock, mock_which: MagicMock, mock_ensure: MagicMock
) -> None:
    """FR-SB-34: container name is sandbox-{team_id}, NOT sandbox-{workspace_id}."""
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="t1", workspace_id="test")
    actor._start_sandbox()

    # Container name must be based on team_id, not workspace_id
    assert actor.state.container_name == "sandbox-t1"
    run_call_args = mock_run.call_args_list[1][0][0]
    name_idx = run_call_args.index("--name") + 1
    assert run_call_args[name_idx] == "sandbox-t1"


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_workspace_id_none_uses_team_id_for_volume(
    mock_run: MagicMock,
    mock_which: MagicMock,
    mock_ensure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-SB-34: workspace_id=None falls back to team_id for volume mount (unchanged default)."""
    monkeypatch.delenv("AKGENTIC_WORKSPACES_ROOT", raising=False)
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="team-1", workspace_id=None)
    actor._start_sandbox()

    run_call_args = mock_run.call_args_list[1][0][0]
    volume_arg_idx = run_call_args.index("-v") + 1
    assert run_call_args[volume_arg_idx] == f"{Path('./workspaces/team-1').resolve()}:/workspace"


# ---------------------------------------------------------------------------
# Story 6.7: _resolved_image() — AKGENTIC_SANDBOX_IMAGE override (AC #3)
# ---------------------------------------------------------------------------


def test_resolved_image_defaults_to_sandbox_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: _resolved_image() returns SANDBOX_IMAGE when AKGENTIC_SANDBOX_IMAGE is unset."""
    monkeypatch.delenv("AKGENTIC_SANDBOX_IMAGE", raising=False)
    actor = make_actor()
    assert actor._resolved_image() == SANDBOX_IMAGE


def test_resolved_image_uses_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: _resolved_image() returns AKGENTIC_SANDBOX_IMAGE when set."""
    monkeypatch.setenv("AKGENTIC_SANDBOX_IMAGE", "ghcr.io/myorg/akgentic-sandbox:v1.2")
    actor = make_actor()
    assert actor._resolved_image() == "ghcr.io/myorg/akgentic-sandbox:v1.2"


@patch.object(DockerSandboxActor, "_ensure_image")
@patch("akgentic.tool.sandbox.docker.shutil.which", return_value="/usr/bin/docker")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_start_sandbox_docker_run_uses_env_override_image(
    mock_run: MagicMock,
    mock_which: MagicMock,
    mock_ensure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: docker run uses the AKGENTIC_SANDBOX_IMAGE value as the image name."""
    monkeypatch.setenv("AKGENTIC_SANDBOX_IMAGE", "ghcr.io/myorg/akgentic-sandbox:v1.2")
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker ps -a
        MagicMock(stdout="abc123", returncode=0),  # docker run
    ]
    actor = make_actor(team_id="team-1")
    actor._start_sandbox()

    run_call_args = mock_run.call_args_list[1][0][0]
    # image name sits between "-w /workspace" and the "sleep infinity" command
    assert "ghcr.io/myorg/akgentic-sandbox:v1.2" in run_call_args
    assert SANDBOX_IMAGE not in run_call_args


# ---------------------------------------------------------------------------
# Story 6.7: _ensure_image() — lazy auto-build (AC #1, #2, #3, #5)
# ---------------------------------------------------------------------------


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_ensure_image_env_override_skips_all_docker(
    mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: AKGENTIC_SANDBOX_IMAGE set → _ensure_image() runs no docker command at all."""
    monkeypatch.setenv("AKGENTIC_SANDBOX_IMAGE", "ghcr.io/myorg/akgentic-sandbox:v1.2")
    actor = make_actor()

    actor._ensure_image()

    assert mock_run.call_count == 0


@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_ensure_image_skips_build_when_image_present(
    mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2: image present (docker images -q non-empty) → only the images check, no docker build."""
    monkeypatch.delenv("AKGENTIC_SANDBOX_IMAGE", raising=False)
    mock_run.return_value = MagicMock(stdout="cached-id\n", returncode=0)
    actor = make_actor()

    actor._ensure_image()

    assert mock_run.call_count == 1
    images_call = mock_run.call_args_list[0][0][0]
    assert images_call == ["docker", "images", "-q", SANDBOX_IMAGE]
    # No docker build invoked
    for call_item in mock_run.call_args_list:
        assert "build" not in call_item[0][0]


@patch("akgentic.tool.sandbox.docker.importlib.resources.files")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_ensure_image_builds_when_absent(
    mock_run: MagicMock, mock_files: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: image absent → docker build -t {SANDBOX_IMAGE} is invoked from bundled Dockerfile."""
    monkeypatch.delenv("AKGENTIC_SANDBOX_IMAGE", raising=False)
    mock_files.return_value.joinpath.return_value.read_text.return_value = (
        "FROM python:3.12-slim\n"
    )
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker images -q → absent
        MagicMock(returncode=0),  # docker build → success
    ]
    actor = make_actor()

    actor._ensure_image()

    build_call = mock_run.call_args_list[1][0][0]
    assert build_call[:4] == ["docker", "build", "-t", SANDBOX_IMAGE]
    # Build output must NOT be captured (visible to the user — AC #1)
    build_kwargs = mock_run.call_args_list[1][1]
    assert "capture_output" not in build_kwargs


@patch("akgentic.tool.sandbox.docker.importlib.resources.files")
@patch("akgentic.tool.sandbox.docker.subprocess.run")
def test_ensure_image_build_failure_raises_runtime_error(
    mock_run: MagicMock, mock_files: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5: build failure (returncode != 0) → RuntimeError naming image + env-var hint."""
    monkeypatch.delenv("AKGENTIC_SANDBOX_IMAGE", raising=False)
    mock_files.return_value.joinpath.return_value.read_text.return_value = (
        "FROM python:3.12-slim\n"
    )
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0),  # docker images -q → absent
        MagicMock(returncode=1),  # docker build → failure
    ]
    actor = make_actor()

    with pytest.raises(RuntimeError) as exc_info:
        actor._ensure_image()

    message = str(exc_info.value)
    assert SANDBOX_IMAGE in message
    assert "AKGENTIC_SANDBOX_IMAGE" in message
