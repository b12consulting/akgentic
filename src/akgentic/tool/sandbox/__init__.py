"""Sandbox submodule — sandboxed shell execution for SWE agents."""

from __future__ import annotations

from .actor import (
    ALLOWED_COMMANDS,
    SANDBOX_ACTOR_NAME,
    CommandNotAllowedError,
    ExecResult,
    SandboxActor,
    SandboxConfig,
    SandboxState,
)
from .bwrap import BwrapSandboxActor
from .docker import DockerSandboxActor
from .local import LocalSandboxActor
from .seatbelt import SeatbeltSandboxActor
from .tool import ExecTool

__all__ = [
    "ALLOWED_COMMANDS",
    "BwrapSandboxActor",
    "CommandNotAllowedError",
    "DockerSandboxActor",
    "ExecResult",
    "ExecTool",
    "LocalSandboxActor",
    "SANDBOX_ACTOR_NAME",
    "SandboxActor",
    "SandboxConfig",
    "SandboxState",
    "SeatbeltSandboxActor",
]
