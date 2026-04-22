"""Tests for ExecTool — observer wiring, mode field, tool behaviour.

Covers AC1–AC13 for Story 6.4 (updated for Story 6.5, Story 8.4):
- SANDBOX_ACTOR_CLASSES dict (AC1)
- ExecTool fields including mode (AC2)
- observer() raises ValueError when orchestrator is None (AC3)
- observer() creates LocalSandboxActor with mode="local" (AC4)
- observer() creates DockerSandboxActor with mode="docker" (AC5)
- observer() reuses existing actor — no second createActor call (AC6)
- observer() raises KeyError on unknown mode (AC7)
- exec_command returns formatted stdout/stderr/exit_code (AC8)
- exec_command catches CommandNotAllowedError → error string (AC9)
- get_tools() returns [] when exec_command=False (AC10)
- Story 6.5: mode comes from ExecTool.mode field, not SANDBOX_MODE env var
- Story 8.4: bwrap/seatbelt keys in registry, auto-mode resolution, DeprecationWarning
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from akgentic.core.actor_address import ActorAddress

from akgentic.tool.sandbox.actor import (
    SANDBOX_ACTOR_NAME,
    CommandNotAllowedError,
    ExecResult,
    SandboxActor,
    SandboxConfig,
)
from akgentic.tool.sandbox.bwrap import BwrapSandboxActor
from akgentic.tool.sandbox.docker import DockerSandboxActor
from akgentic.tool.sandbox.local import LocalSandboxActor
from akgentic.tool.sandbox.seatbelt import SeatbeltSandboxActor
from akgentic.tool.sandbox.tool import SANDBOX_ACTOR_CLASSES, ExecTool, _resolve_auto_mode

# ---------------------------------------------------------------------------
# Mock observer infrastructure
# ---------------------------------------------------------------------------


class MockObserver:
    """Minimal ActorToolObserver stub for ExecTool unit tests."""

    def __init__(
        self,
        has_orchestrator: bool = True,
        existing_actor: ActorAddress | None = None,
    ) -> None:
        self.team_id = "team-test"
        self.myAddress = MagicMock(spec=ActorAddress)
        self.orchestrator = MagicMock(spec=ActorAddress) if has_orchestrator else None

        # Set up orchestrator proxy mock
        self._orch_proxy = MagicMock()
        if existing_actor is not None:
            self._orch_proxy.getChildrenOrCreate.return_value = existing_actor
        else:
            new_addr = MagicMock(spec=ActorAddress)
            self._orch_proxy.getChildrenOrCreate.return_value = new_addr
            self._new_actor_addr = new_addr

    def proxy_ask(
        self,
        actor: ActorAddress,
        actor_type: object = None,
        timeout: int | None = None,
    ) -> object:
        if actor is self.orchestrator:
            return self._orch_proxy
        return MagicMock()  # sandbox proxy

    def notify_event(self, event: object) -> None:
        pass


# ---------------------------------------------------------------------------
# AC1 — SANDBOX_ACTOR_CLASSES registry
# ---------------------------------------------------------------------------


def test_sandbox_actor_classes_has_local_key() -> None:
    """AC1: SANDBOX_ACTOR_CLASSES['local'] maps to LocalSandboxActor."""
    assert "local" in SANDBOX_ACTOR_CLASSES
    assert SANDBOX_ACTOR_CLASSES["local"] is LocalSandboxActor


def test_sandbox_actor_classes_has_docker_key() -> None:
    """AC1: SANDBOX_ACTOR_CLASSES['docker'] maps to DockerSandboxActor."""
    assert "docker" in SANDBOX_ACTOR_CLASSES
    assert SANDBOX_ACTOR_CLASSES["docker"] is DockerSandboxActor


def test_sandbox_actor_classes_is_mutable_dict() -> None:
    """AC1: SANDBOX_ACTOR_CLASSES is a regular dict (mutable — injection window)."""
    assert isinstance(SANDBOX_ACTOR_CLASSES, dict)


# ---------------------------------------------------------------------------
# AC2 — ExecTool field defaults (including mode)
# ---------------------------------------------------------------------------


def test_exec_tool_name_default() -> None:
    """AC2: ExecTool.name defaults to 'Exec'."""
    tool = ExecTool()
    assert tool.name == "Exec"


def test_exec_tool_description_default() -> None:
    """AC2: ExecTool.description is the expected string."""
    tool = ExecTool()
    assert tool.description == "Execute sandboxed shell commands in the team workspace"


def test_exec_tool_exec_command_default_is_true() -> None:
    """AC2: ExecTool.exec_command defaults to True."""
    tool = ExecTool()
    assert tool.exec_command is True


def test_exec_tool_mode_defaults_to_auto() -> None:
    """Story 6.5: ExecTool.mode defaults to 'auto'."""
    tool = ExecTool()
    assert tool.mode == "auto"


def test_exec_tool_mode_can_be_set_to_docker() -> None:
    """Story 6.5: ExecTool(mode='docker') stores mode='docker'."""
    tool = ExecTool(mode="docker")
    assert tool.mode == "docker"


# ---------------------------------------------------------------------------
# AC3 — observer() raises ValueError when orchestrator is None
# ---------------------------------------------------------------------------


def test_observer_raises_value_error_when_orchestrator_is_none() -> None:
    """AC3: observer() raises ValueError when observer.orchestrator is None."""
    tool = ExecTool()
    observer = MockObserver(has_orchestrator=False)

    with pytest.raises(ValueError, match="orchestrator"):
        tool.observer(observer)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC4 — observer() creates LocalSandboxActor when mode="local"
# ---------------------------------------------------------------------------


def test_observer_creates_local_sandbox_actor() -> None:
    """AC4: ExecTool(mode='local').observer() creates LocalSandboxActor."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")

    tool.observer(observer)  # type: ignore[arg-type]

    observer._orch_proxy.getChildrenOrCreate.assert_called_once()
    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    assert call_args[0][0] is LocalSandboxActor


def test_observer_creates_actor_with_correct_config() -> None:
    """AC4: SandboxConfig passed to createActor has name, role, team_id, and mode."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")

    tool.observer(observer)  # type: ignore[arg-type]

    call_kwargs = observer._orch_proxy.getChildrenOrCreate.call_args[1]
    config: SandboxConfig = call_kwargs["config"]
    assert config.name == SANDBOX_ACTOR_NAME
    assert config.role == "ToolActor"
    assert config.team_id == "team-test"
    assert config.mode == "local"


def test_observer_stores_sandbox_proxy() -> None:
    """AC4: observer() stores a non-None _sandbox_proxy after wiring."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")

    tool.observer(observer)  # type: ignore[arg-type]

    assert tool._sandbox_proxy is not None


# ---------------------------------------------------------------------------
# AC5 — observer() creates DockerSandboxActor when mode="docker"
# ---------------------------------------------------------------------------


def test_observer_creates_docker_sandbox_actor() -> None:
    """AC5: ExecTool(mode='docker').observer() creates DockerSandboxActor."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="docker")

    tool.observer(observer)  # type: ignore[arg-type]

    observer._orch_proxy.getChildrenOrCreate.assert_called_once()
    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    assert call_args[0][0] is DockerSandboxActor


def test_observer_creates_docker_actor_config_has_mode_docker() -> None:
    """Story 6.5: SandboxConfig for docker mode has mode='docker'."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="docker")

    tool.observer(observer)  # type: ignore[arg-type]

    call_kwargs = observer._orch_proxy.getChildrenOrCreate.call_args[1]
    config: SandboxConfig = call_kwargs["config"]
    assert config.mode == "docker"


# ---------------------------------------------------------------------------
# AC6 — observer() reuses existing actor — does NOT call createActor again
# ---------------------------------------------------------------------------


def test_observer_reuses_existing_actor() -> None:
    """AC6: getChildrenOrCreate is called and returns the existing actor."""
    existing_addr = MagicMock(spec=ActorAddress)
    observer = MockObserver(existing_actor=existing_addr)
    tool = ExecTool()

    tool.observer(observer)  # type: ignore[arg-type]

    observer._orch_proxy.getChildrenOrCreate.assert_called_once()


def test_observer_second_call_reuses_actor() -> None:
    """AC6: calling observer() a second time still calls getChildrenOrCreate."""
    # First call: no existing actor → getChildrenOrCreate creates one
    observer1 = MockObserver(existing_actor=None)
    tool = ExecTool()
    tool.observer(observer1)  # type: ignore[arg-type]
    assert observer1._orch_proxy.getChildrenOrCreate.call_count == 1

    # Second call: actor now exists — getChildrenOrCreate returns existing
    existing_addr = MagicMock(spec=ActorAddress)
    observer2 = MockObserver(existing_actor=existing_addr)
    tool.observer(observer2)  # type: ignore[arg-type]

    observer2._orch_proxy.getChildrenOrCreate.assert_called_once()


# ---------------------------------------------------------------------------
# AC7 — observer() raises KeyError on unknown mode value
# ---------------------------------------------------------------------------


def test_observer_raises_key_error_on_unknown_mode() -> None:
    """AC7: ExecTool(mode=...) with an unregistered mode → KeyError (fail-fast)."""
    observer = MockObserver(existing_actor=None)
    # Bypass Literal validation by using object.__setattr__
    tool = ExecTool()
    object.__setattr__(tool, "mode", "unknown-backend")

    with pytest.raises(KeyError):
        tool.observer(observer)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC8 — exec_command returns formatted stdout/stderr/exit_code
# ---------------------------------------------------------------------------


def test_exec_command_returns_formatted_output() -> None:
    """AC8: exec_command returns 'stdout:\\n...\\nstderr:\\n...\\nexit_code: 0'."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")
    tool.observer(observer)  # type: ignore[arg-type]

    # Replace proxy with a controlled mock
    mock_proxy = MagicMock(spec=SandboxActor)
    mock_proxy.exec.return_value = ExecResult(
        stdout="===== 5 passed =====", stderr="", exit_code=0
    )
    tool._sandbox_proxy = mock_proxy

    tools = tool.get_tools()
    assert len(tools) == 1
    result = tools[0](cmd="pytest tests/ -v")

    assert "exit_code: 0 (OK)" in result
    assert "5 passed" in result
    assert "stdout:" in result
    assert "stderr" in result


def test_exec_command_includes_stderr_in_output() -> None:
    """AC8: exec_command includes stderr in the returned string."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")
    tool.observer(observer)  # type: ignore[arg-type]

    mock_proxy = MagicMock(spec=SandboxActor)
    mock_proxy.exec.return_value = ExecResult(stdout="", stderr="SyntaxError", exit_code=1)
    tool._sandbox_proxy = mock_proxy

    tools = tool.get_tools()
    result = tools[0](cmd="python bad.py")

    assert "SyntaxError" in result
    assert "exit_code: 1" in result


# ---------------------------------------------------------------------------
# AC9 — exec_command catches CommandNotAllowedError → error string
# ---------------------------------------------------------------------------


def test_exec_command_catches_command_not_allowed_error() -> None:
    """AC9: CommandNotAllowedError is caught and returned as an error string — not raised."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")
    tool.observer(observer)  # type: ignore[arg-type]

    mock_proxy = MagicMock(spec=SandboxActor)
    mock_proxy.exec.side_effect = CommandNotAllowedError("malware not allowed")
    tool._sandbox_proxy = mock_proxy

    tools = tool.get_tools()
    result = tools[0](cmd="malware --install")

    assert "CommandNotAllowedError" in result
    assert not result.startswith("Traceback")  # must not have raised


def test_exec_command_catches_subprocess_error() -> None:
    """AC3 (Story 8.5): When sandbox proxy raises SubprocessError, exec_command
    returns an error string instead of crashing.
    """
    import subprocess

    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")
    tool.observer(observer)  # type: ignore[arg-type]

    mock_proxy = MagicMock(spec=SandboxActor)
    mock_proxy.exec.side_effect = subprocess.SubprocessError("Exception occurred in preexec_fn.")
    tool._sandbox_proxy = mock_proxy

    tools = tool.get_tools()
    result = tools[0](cmd="echo hello")

    assert "SandboxError" in result
    assert "SubprocessError" in result
    assert "preexec_fn" in result


def test_exec_command_catches_generic_exception() -> None:
    """AC3 (Story 8.5): When sandbox proxy raises any Exception, exec_command
    returns an error string instead of raising.
    """
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")
    tool.observer(observer)  # type: ignore[arg-type]

    mock_proxy = MagicMock(spec=SandboxActor)
    mock_proxy.exec.side_effect = RuntimeError("sandbox crashed")
    tool._sandbox_proxy = mock_proxy

    tools = tool.get_tools()
    result = tools[0](cmd="echo hello")

    assert "SandboxError" in result
    assert "RuntimeError" in result
    assert "sandbox crashed" in result


def test_exec_command_error_string_lists_allowed_commands() -> None:
    """AC9: error string contains the sorted list of ALLOWED_COMMANDS."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")
    tool.observer(observer)  # type: ignore[arg-type]

    mock_proxy = MagicMock(spec=SandboxActor)
    mock_proxy.exec.side_effect = CommandNotAllowedError("malware not in allowlist")
    tool._sandbox_proxy = mock_proxy

    tools = tool.get_tools()
    result = tools[0](cmd="malware --install")

    # Should list at least some allowed binaries
    for binary in ["pytest", "python"]:
        assert binary in result


# ---------------------------------------------------------------------------
# AC10 — get_tools() returns [] when exec_command=False
# ---------------------------------------------------------------------------


def test_get_tools_returns_empty_list_when_exec_command_disabled() -> None:
    """AC10: ExecTool(exec_command=False).get_tools() returns []."""
    tool = ExecTool(exec_command=False)
    assert tool.get_tools() == []


def test_get_tools_returns_one_callable_when_enabled() -> None:
    """get_tools() returns exactly one callable when exec_command=True."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")
    tool.observer(observer)  # type: ignore[arg-type]

    mock_proxy = MagicMock(spec=SandboxActor)
    tool._sandbox_proxy = mock_proxy

    tools = tool.get_tools()
    assert len(tools) == 1
    assert callable(tools[0])


# ---------------------------------------------------------------------------
# observer() return value — method chaining
# ---------------------------------------------------------------------------


def test_observer_returns_self() -> None:
    """observer() returns the ExecTool instance for method chaining."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool()

    result = tool.observer(observer)  # type: ignore[arg-type]

    assert result is tool


# ---------------------------------------------------------------------------
# Story 6.5: no SANDBOX_MODE env var dependency
# ---------------------------------------------------------------------------


def test_exec_tool_mode_not_affected_by_sandbox_mode_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 6.5: SANDBOX_MODE env var has no effect — mode is read from ExecTool.mode."""
    # Even if SANDBOX_MODE is set, ExecTool must use self.mode exclusively
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")  # explicit local

    tool.observer(observer)  # type: ignore[arg-type]

    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    # Despite env var, LocalSandboxActor must be chosen (mode="local")
    assert call_args[0][0] is LocalSandboxActor


# ---------------------------------------------------------------------------
# Story 6.6: workspace_id field on ExecTool and pass-through to SandboxConfig
# ---------------------------------------------------------------------------


def test_exec_tool_workspace_id_defaults_to_none() -> None:
    """FR-SB-32: ExecTool.workspace_id defaults to None."""
    tool = ExecTool()
    assert tool.workspace_id is None


def test_exec_tool_workspace_id_can_be_set() -> None:
    """FR-SB-32: ExecTool(workspace_id='test') stores workspace_id='test'."""
    tool = ExecTool(workspace_id="test")
    assert tool.workspace_id == "test"


def test_observer_passes_workspace_id_to_sandbox_config() -> None:
    """FR-SB-32: ExecTool.observer() passes workspace_id through to SandboxConfig."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(workspace_id="test")

    tool.observer(observer)  # type: ignore[arg-type]

    call_kwargs = observer._orch_proxy.getChildrenOrCreate.call_args[1]
    config: SandboxConfig = call_kwargs["config"]
    assert config.workspace_id == "test"


def test_observer_passes_workspace_id_none_to_sandbox_config() -> None:
    """FR-SB-32: ExecTool() (no workspace_id) passes workspace_id=None to SandboxConfig."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool()

    tool.observer(observer)  # type: ignore[arg-type]

    call_kwargs = observer._orch_proxy.getChildrenOrCreate.call_args[1]
    config: SandboxConfig = call_kwargs["config"]
    assert config.workspace_id is None


def test_observer_config_has_team_id_and_workspace_id_independently() -> None:
    """FR-SB-32: SandboxConfig gets both team_id and workspace_id, independently set."""
    observer = MockObserver(existing_actor=None)
    observer.team_id = "t1"
    tool = ExecTool(workspace_id="my-ws")

    tool.observer(observer)  # type: ignore[arg-type]

    call_kwargs = observer._orch_proxy.getChildrenOrCreate.call_args[1]
    config: SandboxConfig = call_kwargs["config"]
    assert config.team_id == "t1"
    assert config.workspace_id == "my-ws"


# ---------------------------------------------------------------------------
# Story 8.4 — registry extension: bwrap and seatbelt keys
# ---------------------------------------------------------------------------


def test_sandbox_actor_classes_has_bwrap_key() -> None:
    """AC1 (8.4): SANDBOX_ACTOR_CLASSES['bwrap'] maps to BwrapSandboxActor."""
    assert "bwrap" in SANDBOX_ACTOR_CLASSES
    assert SANDBOX_ACTOR_CLASSES["bwrap"] is BwrapSandboxActor


def test_sandbox_actor_classes_has_seatbelt_key() -> None:
    """AC1 (8.4): SANDBOX_ACTOR_CLASSES['seatbelt'] maps to SeatbeltSandboxActor."""
    assert "seatbelt" in SANDBOX_ACTOR_CLASSES
    assert SANDBOX_ACTOR_CLASSES["seatbelt"] is SeatbeltSandboxActor


# ---------------------------------------------------------------------------
# Story 8.4 — ExecTool mode field accepts new values
# ---------------------------------------------------------------------------


def test_exec_tool_mode_can_be_set_to_bwrap() -> None:
    """AC2 (8.4): ExecTool(mode='bwrap').mode == 'bwrap'."""
    tool = ExecTool(mode="bwrap")
    assert tool.mode == "bwrap"


def test_exec_tool_mode_can_be_set_to_seatbelt() -> None:
    """AC2 (8.4): ExecTool(mode='seatbelt').mode == 'seatbelt'."""
    tool = ExecTool(mode="seatbelt")
    assert tool.mode == "seatbelt"


def test_exec_tool_mode_can_be_set_to_auto() -> None:
    """AC3 (8.4): ExecTool(mode='auto').mode == 'auto'."""
    tool = ExecTool(mode="auto")
    assert tool.mode == "auto"


# ---------------------------------------------------------------------------
# Story 8.4 — SandboxConfig accepts new mode values
# ---------------------------------------------------------------------------


def test_sandbox_config_mode_accepts_bwrap() -> None:
    """AC2 (8.4): SandboxConfig(team_id='t', mode='bwrap') validates without error."""
    config = SandboxConfig(team_id="t", mode="bwrap")
    assert config.mode == "bwrap"


def test_sandbox_config_mode_accepts_seatbelt() -> None:
    """AC2 (8.4): SandboxConfig(team_id='t', mode='seatbelt') validates without error."""
    config = SandboxConfig(team_id="t", mode="seatbelt")
    assert config.mode == "seatbelt"


def test_sandbox_config_mode_accepts_auto() -> None:
    """AC3 (8.4): SandboxConfig(team_id='t', mode='auto') validates without error."""
    config = SandboxConfig(team_id="t", mode="auto")
    assert config.mode == "auto"


# ---------------------------------------------------------------------------
# Story 8.4 — observer() creates correct actor for bwrap and seatbelt modes
# ---------------------------------------------------------------------------


def test_observer_creates_bwrap_sandbox_actor() -> None:
    """AC4 (8.4): ExecTool(mode='bwrap').observer() creates BwrapSandboxActor."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="bwrap")

    tool.observer(observer)  # type: ignore[arg-type]

    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    assert call_args[0][0] is BwrapSandboxActor


def test_observer_creates_seatbelt_sandbox_actor() -> None:
    """AC5 (8.4): ExecTool(mode='seatbelt').observer() creates SeatbeltSandboxActor."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="seatbelt")

    tool.observer(observer)  # type: ignore[arg-type]

    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    assert call_args[0][0] is SeatbeltSandboxActor


# ---------------------------------------------------------------------------
# Story 8.4 — _resolve_auto_mode() probe order
# ---------------------------------------------------------------------------


def test_resolve_auto_mode_returns_bwrap_when_bwrap_on_path() -> None:
    """AC6 (8.4): _resolve_auto_mode() returns 'bwrap' when bwrap is on PATH."""
    with patch("akgentic.tool.sandbox.tool.shutil.which", return_value="/usr/bin/bwrap"):
        result = _resolve_auto_mode()
    assert result == "bwrap"


def test_resolve_auto_mode_returns_seatbelt_on_darwin_without_bwrap() -> None:
    """AC7 (8.4): _resolve_auto_mode() returns 'seatbelt' on Darwin when sandbox-exec works."""

    def which_side_effect(cmd: str) -> str | None:
        return {
            "bwrap": None,
            "sandbox-exec": "/usr/bin/sandbox-exec",
            "docker": None,
        }.get(cmd)

    mock_probe = MagicMock(returncode=0)
    with (
        patch("akgentic.tool.sandbox.tool.shutil.which", side_effect=which_side_effect),
        patch("akgentic.tool.sandbox.tool.platform.system", return_value="Darwin"),
        patch("akgentic.tool.sandbox.tool.subprocess.run", return_value=mock_probe),
    ):
        result = _resolve_auto_mode()
    assert result == "seatbelt"


def test_resolve_auto_mode_skips_seatbelt_when_probe_fails() -> None:
    """_resolve_auto_mode() falls through to docker/local when sandbox-exec probe fails."""

    def which_side_effect(cmd: str) -> str | None:
        return {
            "bwrap": None,
            "sandbox-exec": "/usr/bin/sandbox-exec",
            "docker": "/usr/bin/docker",
        }.get(cmd)

    mock_probe = MagicMock(returncode=71)  # Operation not permitted
    with (
        patch("akgentic.tool.sandbox.tool.shutil.which", side_effect=which_side_effect),
        patch("akgentic.tool.sandbox.tool.platform.system", return_value="Darwin"),
        patch("akgentic.tool.sandbox.tool.subprocess.run", return_value=mock_probe),
    ):
        result = _resolve_auto_mode()
    assert result == "docker"


def test_resolve_auto_mode_returns_docker_when_docker_on_path() -> None:
    """AC (8.4): _resolve_auto_mode() returns 'docker' when docker on PATH, no bwrap/seatbelt."""

    def which_side_effect(cmd: str) -> str | None:
        return {
            "bwrap": None,
            "sandbox-exec": None,
            "docker": "/usr/bin/docker",
        }.get(cmd)

    with (
        patch("akgentic.tool.sandbox.tool.shutil.which", side_effect=which_side_effect),
        patch("akgentic.tool.sandbox.tool._seatbelt_available", return_value=False),
    ):
        result = _resolve_auto_mode()
    assert result == "docker"


def test_resolve_auto_mode_returns_local_when_nothing_found() -> None:
    """AC8 (8.4): _resolve_auto_mode() returns 'local' when no backends found."""
    with (
        patch("akgentic.tool.sandbox.tool.shutil.which", return_value=None),
        patch("akgentic.tool.sandbox.tool._seatbelt_available", return_value=False),
    ):
        result = _resolve_auto_mode()
    assert result == "local"


# ---------------------------------------------------------------------------
# Story 8.4 — observer() auto-mode creates correct actor
# ---------------------------------------------------------------------------


def test_observer_auto_mode_creates_bwrap_actor() -> None:
    """AC6 (8.4): mode='auto' → _resolve_auto_mode='bwrap' → BwrapSandboxActor created."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="auto")

    with patch("akgentic.tool.sandbox.tool._resolve_auto_mode", return_value="bwrap"):
        tool.observer(observer)  # type: ignore[arg-type]

    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    assert call_args[0][0] is BwrapSandboxActor


def test_observer_auto_mode_creates_seatbelt_actor() -> None:
    """AC7 (8.4): mode='auto' → _resolve_auto_mode='seatbelt' → SeatbeltSandboxActor created."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="auto")

    with patch("akgentic.tool.sandbox.tool._resolve_auto_mode", return_value="seatbelt"):
        tool.observer(observer)  # type: ignore[arg-type]

    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    assert call_args[0][0] is SeatbeltSandboxActor


def test_observer_auto_mode_fallback_to_local_emits_deprecation_warning() -> None:
    """AC8 (8.4): mode='auto' fallback to 'local' emits DeprecationWarning."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="auto")

    with (
        patch("akgentic.tool.sandbox.tool._resolve_auto_mode", return_value="local"),
        pytest.warns(DeprecationWarning, match="no isolation backend found"),
    ):
        tool.observer(observer)  # type: ignore[arg-type]

    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    assert call_args[0][0] is LocalSandboxActor


def test_observer_auto_mode_config_uses_resolved_mode() -> None:
    """AC6 (8.4): SandboxConfig.mode stores resolved mode, not 'auto'."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="auto")

    with patch("akgentic.tool.sandbox.tool._resolve_auto_mode", return_value="bwrap"):
        tool.observer(observer)  # type: ignore[arg-type]

    call_kwargs = observer._orch_proxy.getChildrenOrCreate.call_args[1]
    config: SandboxConfig = call_kwargs["config"]
    assert config.mode == "bwrap"  # resolved mode stored, not "auto"


def test_observer_auto_mode_creates_docker_actor() -> None:
    """AC (8.4): mode='auto' → _resolve_auto_mode='docker' → DockerSandboxActor created."""
    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="auto")

    with patch("akgentic.tool.sandbox.tool._resolve_auto_mode", return_value="docker"):
        tool.observer(observer)  # type: ignore[arg-type]

    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    assert call_args[0][0] is DockerSandboxActor


def test_observer_local_mode_explicit_does_not_emit_deprecation_warning() -> None:
    """AC8/AC9 (8.4): ExecTool(mode='local') explicit — no DeprecationWarning emitted.

    DeprecationWarning must ONLY fire when mode='auto' falls back to 'local',
    not when mode='local' is explicitly requested by the caller.
    """
    import warnings

    observer = MockObserver(existing_actor=None)
    tool = ExecTool(mode="local")

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        tool.observer(observer)  # type: ignore[arg-type]  # must not raise

    call_args = observer._orch_proxy.getChildrenOrCreate.call_args
    assert call_args[0][0] is LocalSandboxActor
