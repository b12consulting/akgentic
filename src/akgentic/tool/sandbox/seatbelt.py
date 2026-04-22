"""SeatbeltSandboxActor — macOS Apple Seatbelt sandbox for policy-based filesystem isolation."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path

from akgentic.tool.sandbox.actor import ExecResult, SandboxActor

logger = logging.getLogger(__name__)

_SEATBELT_POLICY: str = """\
; akgentic-seatbelt.sb — write-restricted workspace sandbox
; Security model: allow all reads, restrict writes to workspace + tmpdir only.
; The command allowlist (ALLOWED_COMMANDS) is the primary security boundary.
(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow sysctl-read)

; allow all filesystem reads (tools need broad read access on macOS)
(allow file-read*)

; writes restricted to workspace, tmpdir, and /dev/null
(allow file-write* (literal "/dev/null"))
(allow file-write* (subpath "{workspace}"))
(allow file-write* (subpath "{tmpdir}"))

(allow network*)
(allow ipc-posix-shm)
(allow mach-lookup)
"""


class SeatbeltSandboxActor(SandboxActor):
    """macOS-only sandbox actor that uses Apple Seatbelt (``sandbox-exec``) for isolation.

    Requires ``sandbox-exec`` to be on PATH — it ships with macOS but is
    deprecated since macOS 10.15 Catalina and may be removed in a future
    macOS release. This actor is intended for macOS developer workstations
    only.

    Each ``_exec()`` invocation writes a write-restricted SBPL policy to a
    temporary ``.sb`` file that allows all reads but restricts writes to the
    workspace directory and tmpdir. Network access is allowed. The temp file
    is deleted in a ``finally`` block after the subprocess completes.

    Unlike ``BwrapSandboxActor`` and ``LocalSandboxActor``, no ``preexec_fn``
    or env-stripping is applied: ``resource.setrlimit`` behaves differently on
    macOS and the Seatbelt policy handles the primary threat model.
    """

    def _start_sandbox(self) -> None:
        """Start the Seatbelt sandbox.

        Checks that ``sandbox-exec`` is available on PATH **and** that
        ``sandbox_apply`` actually works at runtime (macOS 15+ blocks it),
        then resolves the workspace directory (using ``workspace_id or
        team_id``) under ``AKGENTIC_WORKSPACES_ROOT`` (defaulting to
        ``./workspaces``) and creates it if it does not yet exist. Emits a
        ``DeprecationWarning`` noting that ``sandbox-exec`` is deprecated
        since macOS 10.15 Catalina.

        Raises:
            RuntimeError: If ``sandbox-exec`` is not found on PATH or if
                sandbox_apply is blocked by the OS (macOS 15+).
        """
        if shutil.which("sandbox-exec") is None:
            raise RuntimeError(
                "sandbox-exec not found on PATH. "
                "It ships with macOS but is absent on this system."
            )
        if platform.system() == "Darwin":
            probe = subprocess.run(
                ["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                capture_output=True,
                timeout=5,
            )
            if probe.returncode != 0:
                raise RuntimeError(
                    "sandbox-exec is on PATH but sandbox_apply is blocked "
                    f"(exit {probe.returncode}). Common causes: the calling process "
                    "is already sandboxed (e.g. running inside Claude Code) or a "
                    "future macOS removed sandbox-exec support. "
                    "Use mode='docker' or mode='local' instead."
                )
        base = os.environ.get("AKGENTIC_WORKSPACES_ROOT", "./workspaces")
        ws_name = self.config.workspace_id or self.config.team_id
        workspace_path = Path(base) / ws_name
        workspace_path.mkdir(parents=True, exist_ok=True)
        self.state.workspace_path = workspace_path.resolve()
        self.state.notify_state_change()
        logger.debug(
            "SeatbeltSandboxActor started: workspace=%s",
            self.state.workspace_path,
        )
        warnings.warn(
            "sandbox-exec is deprecated since macOS 10.15 Catalina and may be removed "
            "in a future macOS release. SeatbeltSandboxActor is for macOS developer "
            "workstations only.",
            DeprecationWarning,
            stacklevel=2,
        )

    def _stop_sandbox(self) -> None:
        """Stop the Seatbelt sandbox.

        No-op: ``sandbox-exec`` processes do not persist between ``_exec()``
        calls — each invocation spawns and terminates its own sandboxed process.
        """
        logger.debug("SeatbeltSandboxActor stopped.")

    def _exec(self, cmd: str, cwd: str) -> ExecResult:
        """Execute a command inside an Apple Seatbelt policy sandbox.

        Writes the deny-by-default SBPL policy to a temporary ``.sb`` file
        with the workspace path substituted, then invokes ``sandbox-exec -f
        <policy_file>`` with the given command. The temp file is deleted in a
        ``finally`` block.

        No ``preexec_fn`` or env-stripping is applied — ``resource.setrlimit``
        behaviour differs on macOS and the SBPL policy covers the threat model.

        Args:
            cmd: Full command string to execute (pre-validated by ``exec()``).
            cwd: Working directory for the sandboxed process. Falls back to the
                 workspace path when empty or not provided.

        Returns:
            ExecResult with stdout, stderr, and exit_code from the process.
        """
        assert self.state.workspace_path is not None
        ws = self.state.workspace_path
        effective_cwd = str(ws / cwd) if cwd else str(ws)
        logger.debug("SeatbeltSandboxActor exec: cmd=%r cwd=%s", cmd, effective_cwd)
        tmpdir = tempfile.gettempdir()
        policy = _SEATBELT_POLICY.replace(
            "{workspace}", str(self.state.workspace_path)
        ).replace("{tmpdir}", tmpdir)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sb", delete=False
        ) as policy_file:
            policy_file.write(policy)
            policy_path = policy_file.name
        try:
            result = subprocess.run(
                ["sandbox-exec", "-f", policy_path] + cmd.split(),
                capture_output=True,
                text=True,
                timeout=30,
                cwd=effective_cwd,
            )
            return ExecResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except FileNotFoundError:
            return ExecResult(
                stdout="",
                stderr=f"Working directory not found: {cwd or '.'}",
                exit_code=1,
            )
        finally:
            os.unlink(policy_path)
