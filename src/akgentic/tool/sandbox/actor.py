"""SandboxActor — abstract base class for sandbox execution backends.

Defines models, the command allowlist, module constants, and the lifecycle/exec
contract. Concrete subclasses (LocalSandboxActor, DockerSandboxActor) provide
the execution backend by implementing _start_sandbox, _stop_sandbox, and _exec.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from akgentic.core.agent import Akgent, BaseConfig, BaseState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

SANDBOX_ACTOR_NAME: str = "#SandboxActor"
SANDBOX_ACTOR_ROLE: str = "ToolActor"

ALLOWED_COMMANDS: frozenset[str] = frozenset(
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


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class SandboxConfig(BaseConfig):
    """Configuration for a SandboxActor.

    Attributes:
        team_id: Identifier of the team that owns this sandbox.
        workspace_id: Optional workspace directory name override.  When ``None``
            (default), the workspace directory is named after ``team_id``.  When
            set, the named directory is used instead.  Docker container name
            always uses ``team_id`` — containers are per-team, not per-workspace.
        mode: Execution backend — ``"local"`` (subprocess), ``"bwrap"``
            (Linux bubblewrap), ``"seatbelt"`` (macOS Apple Seatbelt),
            ``"docker"`` (persistent container), or ``"auto"`` (automatic
            selection of the best available backend).  Defaults to ``"local"``.
    """

    team_id: str
    workspace_id: str | None = None
    mode: Literal["local", "bwrap", "seatbelt", "docker", "auto"] = "local"


class SandboxState(BaseState):
    """Runtime state for a SandboxActor.

    Attributes:
        workspace_path: Path to the workspace directory on the host, or None if
            the sandbox has not been started yet.
        container_name: Name of the Docker container, or None if not applicable.
    """

    workspace_path: Path | None = None
    container_name: str | None = None


class ExecResult(BaseModel):
    """Result of a sandbox command execution.

    Attributes:
        stdout: Captured standard output from the command.
        stderr: Captured standard error from the command.
        exit_code: Process exit code (0 indicates success).
    """

    stdout: str
    stderr: str
    exit_code: int


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CommandNotAllowedError(Exception):
    """Raised when exec() is called with a command binary not in ALLOWED_COMMANDS.

    Only the first token (binary name) of the command string is checked.
    Argument-level filtering is out of scope for the base class.
    """


# ---------------------------------------------------------------------------
# Abstract actor
# ---------------------------------------------------------------------------


class SandboxActor(Akgent[SandboxConfig, SandboxState], ABC):
    """Abstract sandbox actor. Concrete subclasses provide the execution backend.

    Responsibilities of this base class:
    - Initialize and manage SandboxState via on_start / on_stop lifecycle hooks.
    - Enforce the command allowlist before delegating to _exec.
    - Define the abstract interface (_start_sandbox, _stop_sandbox, _exec) that
      subclasses must implement.
    """

    def on_start(self) -> None:
        """Initialize SandboxState and start the sandbox backend.

        Registers the actor as a state observer (required for Pykka telemetry),
        then delegates to _start_sandbox() for backend-specific setup.
        """
        self.state = SandboxState()
        self.state.observer(self)
        self._start_sandbox()

    def on_stop(self) -> None:
        """Stop the sandbox backend, swallowing any exceptions.

        Calls _stop_sandbox() inside a try/except so that any backend error
        does not prevent super().on_stop() from running. Leaving Pykka actors
        in a broken state by raising in on_stop() is a critical failure mode
        that this pattern prevents.
        """
        try:
            self._stop_sandbox()
        except Exception:
            logger.warning(
                "SandboxActor._stop_sandbox() raised during on_stop — swallowing",
                exc_info=True,
            )
        super().on_stop()

    def exec(self, cmd: str, cwd: str = "") -> ExecResult:
        """Execute a command inside the sandbox after allowlist validation.

        Only the first whitespace-delimited token (the binary name) is checked
        against ALLOWED_COMMANDS. Argument-level filtering is out of scope.

        Args:
            cmd: Full command string to execute (e.g. "python main.py").
            cwd: Working directory inside the sandbox. Defaults to "".

        Returns:
            ExecResult with stdout, stderr, and exit_code from the backend.

        Raises:
            CommandNotAllowedError: If the command binary is not in ALLOWED_COMMANDS.
        """
        tokens = cmd.split()
        if not tokens:
            raise CommandNotAllowedError(
                "Command string is empty — no binary to validate against the allowlist."
            )
        binary = tokens[0]
        if binary not in ALLOWED_COMMANDS:
            raise CommandNotAllowedError(
                f"Command '{binary}' is not in the allowed commands list. "
                f"Allowed: {sorted(ALLOWED_COMMANDS)}"
            )
        return self._exec(cmd, cwd)

    # ------------------------------------------------------------------
    # Abstract methods — must be implemented by concrete subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def _start_sandbox(self) -> None:
        """Start the sandbox execution environment.

        Called from on_start() after SandboxState is initialized. Subclasses
        should provision any resources needed (e.g., create a temp directory,
        start a Docker container).
        """

    @abstractmethod
    def _stop_sandbox(self) -> None:
        """Stop and clean up the sandbox execution environment.

        Called from on_stop() inside a try/except. Subclasses should release
        resources (e.g., remove temp directory, stop a Docker container).
        May raise — the caller swallows all exceptions.
        """

    @abstractmethod
    def _exec(self, cmd: str, cwd: str) -> ExecResult:
        """Execute a pre-validated command inside the sandbox.

        Called by exec() after the allowlist check passes. Subclasses handle
        the actual process execution (subprocess, Docker exec API, etc.).

        Args:
            cmd: Full command string (already validated by exec()).
            cwd: Working directory inside the sandbox.

        Returns:
            ExecResult with captured stdout, stderr, and exit code.
        """
