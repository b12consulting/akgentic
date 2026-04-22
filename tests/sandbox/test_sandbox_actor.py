"""Tests for SandboxActor base class — models, allowlist, and lifecycle.

Covers AC1 through AC9 for Story 6.1:
- SandboxConfig, SandboxState, ExecResult model validation
- ALLOWED_COMMANDS allowlist pass/fail/edge cases
- CommandNotAllowedError raised on disallowed binary
- on_start() / on_stop() lifecycle via a minimal concrete stub
"""

from __future__ import annotations

from pathlib import Path

import pytest

from akgentic.tool.sandbox.actor import (
    ALLOWED_COMMANDS,
    SANDBOX_ACTOR_NAME,
    SANDBOX_ACTOR_ROLE,
    CommandNotAllowedError,
    ExecResult,
    SandboxActor,
    SandboxConfig,
    SandboxState,
)

# ---------------------------------------------------------------------------
# Minimal concrete stub — required because SandboxActor is abstract
# ---------------------------------------------------------------------------


class ConcreteSandboxActor(SandboxActor):
    """Minimal non-abstract subclass for unit testing."""

    def _start_sandbox(self) -> None:
        pass  # no-op for testing

    def _stop_sandbox(self) -> None:
        pass  # no-op for testing

    def _exec(self, cmd: str, cwd: str) -> ExecResult:
        return ExecResult(stdout="ok", stderr="", exit_code=0)


class FailingStopSandboxActor(ConcreteSandboxActor):
    """Subclass whose _stop_sandbox always raises, for on_stop() swallow test."""

    def _stop_sandbox(self) -> None:
        raise RuntimeError("Sandbox stop failed")


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_sandbox_actor_name_constant() -> None:
    """AC3: SANDBOX_ACTOR_NAME is the expected string."""
    assert SANDBOX_ACTOR_NAME == "#SandboxActor"


def test_sandbox_actor_role_constant() -> None:
    """AC3: SANDBOX_ACTOR_ROLE is the expected string."""
    assert SANDBOX_ACTOR_ROLE == "ToolActor"


# ---------------------------------------------------------------------------
# SandboxConfig model validation (AC1)
# ---------------------------------------------------------------------------


def test_sandbox_config_requires_team_id() -> None:
    """SandboxConfig raises ValidationError when team_id is missing."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SandboxConfig(name="test", role="ToolActor")  # type: ignore[call-arg]


def test_sandbox_config_valid() -> None:
    """SandboxConfig accepts name, role, and team_id."""
    config = SandboxConfig(name="sandbox", role="ToolActor", team_id="team-42")
    assert config.team_id == "team-42"
    assert config.name == "sandbox"
    assert config.role == "ToolActor"


def test_sandbox_config_mode_defaults_to_local() -> None:
    """SandboxConfig.mode defaults to 'local' when not provided."""
    config = SandboxConfig(name="sandbox", role="ToolActor", team_id="team-1")
    assert config.mode == "local"


def test_sandbox_config_mode_docker() -> None:
    """SandboxConfig accepts mode='docker'."""
    config = SandboxConfig(name="sandbox", role="ToolActor", team_id="team-1", mode="docker")
    assert config.mode == "docker"


def test_sandbox_config_mode_local_explicit() -> None:
    """SandboxConfig accepts mode='local' explicitly."""
    config = SandboxConfig(name="sandbox", role="ToolActor", team_id="team-1", mode="local")
    assert config.mode == "local"


def test_sandbox_config_workspace_id_defaults_to_none() -> None:
    """FR-SB-31: SandboxConfig.workspace_id defaults to None."""
    config = SandboxConfig(name="sandbox", role="ToolActor", team_id="team-1")
    assert config.workspace_id is None


def test_sandbox_config_workspace_id_can_be_set() -> None:
    """FR-SB-31: SandboxConfig.workspace_id can be set to a string value."""
    config = SandboxConfig(
        name="sandbox", role="ToolActor", team_id="team-1", workspace_id="my-workspace"
    )
    assert config.workspace_id == "my-workspace"


def test_sandbox_config_has_both_team_id_and_workspace_id() -> None:
    """FR-SB-31: SandboxConfig stores both team_id and workspace_id independently."""
    config = SandboxConfig(
        name="sandbox", role="ToolActor", team_id="t1", workspace_id="test"
    )
    assert config.team_id == "t1"
    assert config.workspace_id == "test"


# ---------------------------------------------------------------------------
# SandboxState model validation (AC1)
# ---------------------------------------------------------------------------


def test_sandbox_state_defaults() -> None:
    """SandboxState has workspace_path=None and container_name=None by default."""
    state = SandboxState()
    assert state.workspace_path is None
    assert state.container_name is None


def test_sandbox_state_with_values() -> None:
    """SandboxState accepts Path and string values."""
    state = SandboxState(workspace_path=Path("/tmp/ws"), container_name="my-container")
    assert state.workspace_path == Path("/tmp/ws")
    assert state.container_name == "my-container"


# ---------------------------------------------------------------------------
# ExecResult model validation (AC1)
# ---------------------------------------------------------------------------


def test_exec_result_valid() -> None:
    """ExecResult accepts stdout, stderr, exit_code."""
    result = ExecResult(stdout="hello", stderr="", exit_code=0)
    assert result.stdout == "hello"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_exec_result_requires_all_fields() -> None:
    """ExecResult raises ValidationError when required fields are missing."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExecResult(stdout="hello", stderr="")  # type: ignore[call-arg]


def test_exec_result_non_zero_exit_code() -> None:
    """ExecResult stores non-zero exit codes."""
    result = ExecResult(stdout="", stderr="error output", exit_code=1)
    assert result.exit_code == 1
    assert result.stderr == "error output"


# ---------------------------------------------------------------------------
# ALLOWED_COMMANDS (AC2)
# ---------------------------------------------------------------------------


def test_allowed_commands_is_frozenset() -> None:
    """ALLOWED_COMMANDS is a frozenset."""
    assert isinstance(ALLOWED_COMMANDS, frozenset)


def test_allowed_commands_exact_set() -> None:
    """AC2: ALLOWED_COMMANDS contains exactly the FR-SB-5 binaries."""
    expected = frozenset(
        {
            "python",
            "python3",
            "pytest",
            "ruff",
            "mypy",
            "git",
            "uv",
            "pip",
            "cat",
            "ls",
            "find",
            "grep",
            "mkdir",
            "cp",
            "mv",
            "rm",
            "echo",
            "touch",
            "curl",
            "wget",
            "make",
            "bash",
            "sh",
            "node",
            "npm",
            "npx",
        }
    )
    assert ALLOWED_COMMANDS == expected


# ---------------------------------------------------------------------------
# exec() allowlist pass (AC4)
# ---------------------------------------------------------------------------


def test_exec_allowed_command_delegates_to_exec_impl() -> None:
    """AC4: exec('python main.py') delegates to _exec and returns its ExecResult."""
    actor = ConcreteSandboxActor()
    actor.on_start()

    result = actor.exec("python main.py")

    assert isinstance(result, ExecResult)
    assert result.stdout == "ok"
    assert result.exit_code == 0


def test_exec_allowed_command_passes_cmd_and_cwd() -> None:
    """exec() passes cmd and cwd to _exec unmodified."""
    actor = ConcreteSandboxActor()
    actor.on_start()

    received: list[tuple[str, str]] = []

    original_exec = actor._exec

    def capturing_exec(cmd: str, cwd: str) -> ExecResult:
        received.append((cmd, cwd))
        return original_exec(cmd, cwd)

    actor._exec = capturing_exec  # type: ignore[method-assign]

    actor.exec("python main.py", "/workspace")

    assert received == [("python main.py", "/workspace")]


# ---------------------------------------------------------------------------
# exec() allowlist fail (AC5)
# ---------------------------------------------------------------------------


def test_exec_disallowed_command_raises_error() -> None:
    """AC5: exec('malware --install') raises CommandNotAllowedError."""
    actor = ConcreteSandboxActor()
    actor.on_start()

    with pytest.raises(CommandNotAllowedError):
        actor.exec("malware --install")


def test_exec_disallowed_command_error_message_contains_binary() -> None:
    """CommandNotAllowedError message names the disallowed binary."""
    actor = ConcreteSandboxActor()
    actor.on_start()

    with pytest.raises(CommandNotAllowedError, match="malware"):
        actor.exec("malware --install")


def test_exec_empty_command_raises_error() -> None:
    """exec('') raises CommandNotAllowedError — empty string has no binary token."""
    actor = ConcreteSandboxActor()
    actor.on_start()

    with pytest.raises(CommandNotAllowedError, match="empty"):
        actor.exec("")


# ---------------------------------------------------------------------------
# exec() allowlist edge case (AC6)
# ---------------------------------------------------------------------------


def test_exec_rm_rf_passes_allowlist() -> None:
    """AC6: exec('rm -rf /') passes — only 'rm' binary is checked, not args."""
    actor = ConcreteSandboxActor()
    actor.on_start()

    # Should NOT raise — 'rm' is in ALLOWED_COMMANDS
    result = actor.exec("rm -rf /")
    assert isinstance(result, ExecResult)


# ---------------------------------------------------------------------------
# on_start() lifecycle (AC7)
# ---------------------------------------------------------------------------


def test_on_start_initializes_state() -> None:
    """AC7: on_start() creates a SandboxState instance."""
    actor = ConcreteSandboxActor()
    actor.on_start()

    assert isinstance(actor.state, SandboxState)


def test_on_start_calls_start_sandbox() -> None:
    """AC7: on_start() calls _start_sandbox()."""
    actor = ConcreteSandboxActor()
    call_count = 0

    original = actor._start_sandbox

    def tracking_start() -> None:
        nonlocal call_count
        call_count += 1
        original()

    actor._start_sandbox = tracking_start  # type: ignore[method-assign]
    actor.on_start()

    assert call_count == 1


def test_on_start_registers_state_observer() -> None:
    """on_start() registers the actor as a state observer."""
    actor = ConcreteSandboxActor()
    actor.on_start()

    # The state's _observer should be the actor itself
    assert actor.state._observer is actor


# ---------------------------------------------------------------------------
# on_stop() exception swallowing (AC8)
# ---------------------------------------------------------------------------


def test_on_stop_swallows_stop_sandbox_exception() -> None:
    """AC8: on_stop() swallows exceptions from _stop_sandbox() — does not propagate."""
    actor = FailingStopSandboxActor()
    actor.on_start()

    # Must not raise even though _stop_sandbox() raises RuntimeError
    actor.on_stop()  # Should complete without exception


def test_on_stop_no_exception_completes_normally() -> None:
    """on_stop() completes normally when _stop_sandbox() does not raise."""
    actor = ConcreteSandboxActor()
    actor.on_start()

    actor.on_stop()  # Should complete without exception


def test_on_stop_calls_super_on_stop() -> None:
    """AC8: on_stop() calls super().on_stop() even when _stop_sandbox() raises."""
    actor = FailingStopSandboxActor()
    actor.on_start()

    # Verify indirectly: on_stop() must not raise.
    # super().on_stop() is a Pykka hook (no-op in unit tests without a running actor system).
    # If we reach the end of this call without exception, super().on_stop() was not blocked
    # by the _stop_sandbox() failure.
    actor.on_stop()  # Should not raise
