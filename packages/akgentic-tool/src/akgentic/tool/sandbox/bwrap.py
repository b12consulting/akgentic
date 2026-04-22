"""BwrapSandboxActor — Linux bubblewrap sandbox for filesystem-isolated command execution."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from akgentic.tool.sandbox.actor import ExecResult, SandboxActor
from akgentic.tool.sandbox.local import _make_preexec

logger = logging.getLogger(__name__)


class BwrapSandboxActor(SandboxActor):
    """Linux-only sandbox actor that uses bubblewrap (bwrap) for filesystem isolation.

    Requires ``bwrap`` to be installed on PATH (``apt install bubblewrap`` or
    ``dnf install bubblewrap``). Each ``_exec()`` invocation runs the command inside
    a fresh bubblewrap namespace where only the workspace directory is writable —
    ``/usr``, ``/lib*``, ``/tmp``, ``/dev``, and ``/proc`` are bound read-only or as
    virtual filesystems. Network access is disabled via ``--unshare-net``.

    Unlike ``LocalSandboxActor``, this actor provides genuine filesystem isolation:
    paths outside the namespace (e.g., ``/etc``, ``/home``, parent directories of the
    workspace) are invisible to the sandboxed process.
    """

    def _start_sandbox(self) -> None:
        """Start the bubblewrap sandbox.

        Checks that ``bwrap`` is available on PATH, then resolves the workspace
        directory (using ``workspace_id or team_id``) under ``AKGENTIC_WORKSPACES_ROOT``
        (defaulting to ``./workspaces``) and creates it if it does not yet exist.

        Raises:
            RuntimeError: If ``bwrap`` is not found on PATH.
        """
        if shutil.which("bwrap") is None:
            raise RuntimeError(
                "bwrap not found on PATH. Install with:\n"
                "  apt install bubblewrap   (Debian/Ubuntu)\n"
                "  dnf install bubblewrap   (Fedora/RHEL)"
            )
        base = os.environ.get("AKGENTIC_WORKSPACES_ROOT", "./workspaces")
        ws_name = self.config.workspace_id or self.config.team_id
        workspace_path = Path(base) / ws_name
        workspace_path.mkdir(parents=True, exist_ok=True)
        self.state.workspace_path = workspace_path.resolve()
        self.state.notify_state_change()
        logger.debug(
            "BwrapSandboxActor started: workspace=%s",
            self.state.workspace_path,
        )

    def _stop_sandbox(self) -> None:
        """Stop the bubblewrap sandbox.

        No-op: bubblewrap processes do not persist between ``_exec()`` calls —
        each invocation spawns and terminates its own namespace.
        """
        logger.debug("BwrapSandboxActor stopped.")

    def _exec(self, cmd: str, cwd: str) -> ExecResult:
        """Execute a command inside a bubblewrap namespace.

        Builds a ``bwrap`` command that mounts the workspace at ``/workspace``
        (read-write), bind-mounts ``/usr`` and ``/lib*`` read-only, provides
        virtual ``/tmp``, ``/dev``, and ``/proc``, and unshares the network and
        PID namespaces. The process is also subject to the same resource limits
        as ``LocalSandboxActor`` (via ``_make_preexec()``) and runs with a
        minimal PATH-only environment.

        Args:
            cmd: Full command string to execute (pre-validated by ``exec()``).
            cwd: Working directory inside the sandbox (relative to ``/workspace``).
                 Empty string means ``/workspace`` root.

        Returns:
            ExecResult with stdout, stderr, and exit_code from the process.
        """
        assert self.state.workspace_path is not None
        effective_cwd = f"/workspace/{cwd}" if cwd else "/workspace"
        bwrap_cmd: list[str] = [
            "bwrap",
            "--bind", str(self.state.workspace_path), "/workspace",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind-try", "/lib", "/lib",
            "--ro-bind-try", "/lib64", "/lib64",
            "--ro-bind-try", "/lib32", "/lib32",
            "--tmpfs", "/tmp",
            "--dev", "/dev",
            "--proc", "/proc",
            "--unshare-net",
            "--unshare-pid",
            "--die-with-parent",
            "--new-session",
            "--chdir", effective_cwd,
        ] + cmd.split()
        result = subprocess.run(
            bwrap_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            preexec_fn=_make_preexec(),
            env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
        return ExecResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
