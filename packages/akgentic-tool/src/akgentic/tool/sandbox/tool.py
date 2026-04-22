"""ExecTool — ToolCard proxy for SandboxActor execution backend."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import warnings
from typing import Any, Callable, Literal

from pydantic import PrivateAttr

from akgentic.core.orchestrator import Orchestrator
from akgentic.tool.core import TOOL_CALL, BaseToolParam, Channels, ToolCard, _resolve
from akgentic.tool.event import ActorToolObserver
from akgentic.tool.sandbox.actor import (
    ALLOWED_COMMANDS,
    SANDBOX_ACTOR_NAME,
    CommandNotAllowedError,
    SandboxActor,
    SandboxConfig,
)
from akgentic.tool.sandbox.bwrap import BwrapSandboxActor
from akgentic.tool.sandbox.docker import DockerSandboxActor
from akgentic.tool.sandbox.local import LocalSandboxActor
from akgentic.tool.sandbox.seatbelt import SeatbeltSandboxActor

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# SANDBOX_ACTOR_CLASSES — mutable injection window for runtime registration
# ---------------------------------------------------------------------------

SANDBOX_ACTOR_CLASSES: dict[str, type[SandboxActor]] = {
    "local": LocalSandboxActor,
    "bwrap": BwrapSandboxActor,
    "seatbelt": SeatbeltSandboxActor,
    "docker": DockerSandboxActor,
    # "e2b": E2BSandboxActor  ← injected by akgentic-infra at runtime
}


# ---------------------------------------------------------------------------
# Auto-mode resolution
# ---------------------------------------------------------------------------


def _seatbelt_available() -> bool:
    """Return True if sandbox-exec is on PATH and actually works at runtime.

    macOS 15+ may block ``sandbox_apply`` even when ``sandbox-exec`` is present.
    A quick probe with ``(allow default)`` detects this at negligible cost.
    """
    if shutil.which("sandbox-exec") is None or platform.system() != "Darwin":
        return False
    try:
        result = subprocess.run(
            ["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _resolve_auto_mode() -> Literal["local", "bwrap", "seatbelt", "docker"]:
    """Probe the host and return the best available sandbox backend.

    Probe order:
    1. ``bwrap`` on PATH → ``"bwrap"`` (Linux bubblewrap)
    2. ``sandbox-exec`` on PATH + Darwin → ``"seatbelt"`` (macOS)
    3. ``docker`` on PATH → ``"docker"``
    4. fallback → ``"local"`` (no filesystem isolation)

    Returns:
        String key matching an entry in SANDBOX_ACTOR_CLASSES.
    """
    if shutil.which("bwrap") is not None:
        logger.debug("_resolve_auto_mode: selected bwrap")
        return "bwrap"
    if _seatbelt_available():
        logger.debug("_resolve_auto_mode: selected seatbelt")
        return "seatbelt"
    if shutil.which("docker") is not None:
        logger.debug("_resolve_auto_mode: selected docker")
        return "docker"
    logger.debug("_resolve_auto_mode: fallback to local (no isolation backend found)")
    return "local"


# ---------------------------------------------------------------------------
# Capability parameter model
# ---------------------------------------------------------------------------


class ExecCommand(BaseToolParam):
    """Execute a sandboxed shell command in the team workspace."""

    expose: set[Channels] = {TOOL_CALL}


# ---------------------------------------------------------------------------
# ExecTool ToolCard
# ---------------------------------------------------------------------------


class ExecTool(ToolCard):
    """ToolCard proxy that routes shell commands to the team's SandboxActor."""

    name: str = "Exec"
    description: str = "Execute sandboxed shell commands in the team workspace"
    exec_command: ExecCommand | bool = True
    mode: Literal["local", "bwrap", "seatbelt", "docker", "auto"] = "auto"
    workspace_id: str | None = None

    _sandbox_proxy: SandboxActor | None = PrivateAttr(default=None)

    def observer(self, observer: ActorToolObserver) -> "ExecTool":  # type: ignore[override]
        """Attach observer and set up the sandbox actor proxy.

        Resolves ``SANDBOX_ACTOR_CLASSES[self.mode]`` at call time (not import
        time) so that akgentic-infra can inject additional actor classes before
        any ExecTool is constructed (NFR-SB-7).  ``self.workspace_id`` is
        forwarded to ``SandboxConfig`` so the sandbox backend uses the same
        workspace directory as ``WorkspaceTool(workspace_id=...)``.

        Args:
            observer: Actor-aware observer providing orchestrator access.

        Returns:
            Self, for method chaining.

        Raises:
            ValueError: If observer.orchestrator is None.
            KeyError: If ``self.mode`` names an unregistered backend.
        """
        self._observer = observer
        if observer.orchestrator is None:
            raise ValueError("ExecTool requires access to the orchestrator.")

        # Resolve mode and emit warnings before getChildrenOrCreate
        effective_mode = _resolve_auto_mode() if self.mode == "auto" else self.mode
        if self.mode == "auto" and effective_mode == "local":
            warnings.warn(
                "ExecTool mode='auto': no isolation backend found (bwrap, sandbox-exec, "
                "docker). Falling back to LocalSandboxActor — no filesystem isolation.",
                DeprecationWarning,
                stacklevel=2,
            )
        # KeyError on unknown mode — intentional (fail-fast, NFR-SB-7)
        actor_class = SANDBOX_ACTOR_CLASSES[effective_mode]

        orchestrator_proxy = observer.proxy_ask(observer.orchestrator, Orchestrator)
        sandbox_addr = orchestrator_proxy.getChildrenOrCreate(
            actor_class,
            config=SandboxConfig(
                name=SANDBOX_ACTOR_NAME,
                role="ToolActor",
                team_id=str(observer.team_id),
                workspace_id=self.workspace_id,
                mode=effective_mode,
            ),
        )

        self._sandbox_proxy = observer.proxy_ask(sandbox_addr, SandboxActor)
        return self

    def get_tools(self) -> list[Callable[..., Any]]:
        """Return the exec_command tool callable when enabled."""
        tools: list[Callable[..., Any]] = []
        ec = _resolve(self.exec_command, ExecCommand)
        if ec is not None and TOOL_CALL in ec.expose:
            tools.append(self._exec_command_factory(ec))
        return tools

    def _exec_command_factory(self, params: ExecCommand) -> Callable[..., Any]:
        """Build the exec_command callable bound to the sandbox proxy."""
        assert self._sandbox_proxy is not None, "_sandbox_proxy must be set before get_tools()"
        sandbox_proxy = self._sandbox_proxy

        def exec_command(cmd: str, cwd: str = "") -> str:
            """Execute a sandboxed shell command in the team workspace.

            Args:
                cmd: Full command string. The binary (first token) must be in the allow-list.
                cwd: Subdirectory relative to workspace root. Defaults to workspace root.

            Returns:
                Combined stdout, stderr, and exit code summary as a string.
                On disallowed command: error string listing allowed commands.
            """
            try:
                result = sandbox_proxy.exec(cmd, cwd)
                status = "OK" if result.exit_code == 0 else "FAILED"
                return (
                    f"exit_code: {result.exit_code} ({status})"
                    f"\nstdout:\n{result.stdout}"
                    f"\nstderr (note: many tools write progress to stderr"
                    f" even on success):\n{result.stderr}"
                )
            except CommandNotAllowedError as e:
                return f"CommandNotAllowedError: {e}. Allowed commands: {sorted(ALLOWED_COMMANDS)}"
            except Exception as e:
                logger.warning("Sandbox exec failed: %s: %s", type(e).__name__, e)
                return f"SandboxError: {type(e).__name__}: {e}"

        allowed_str = ", ".join(sorted(ALLOWED_COMMANDS))
        base_doc = (exec_command.__doc__ or "") + f"\n\n            Allowed binaries: {allowed_str}"
        exec_command.__doc__ = params.format_docstring(base_doc)
        return exec_command
