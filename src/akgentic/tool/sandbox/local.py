"""LocalSandboxActor — subprocess-based sandbox for local filesystem execution."""

from __future__ import annotations

import logging
import os
import resource
import subprocess
import sys
from pathlib import Path
from typing import Callable

from akgentic.tool.sandbox.actor import ExecResult, SandboxActor

logger = logging.getLogger(__name__)

# Environment variables safe to pass through to sandboxed subprocesses.
# Keeps tools like git/xcode-select functional on macOS while stripping
# secrets, API keys, and shell customisation that could leak or interfere.
_SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "DEVELOPER_DIR",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "GIT_EXEC_PATH",
        "GIT_TEMPLATE_DIR",
    }
)


_MACOS_DEVELOPER_DIRS: tuple[str, ...] = (
    "/Library/Developer/CommandLineTools",
    "/Applications/Xcode.app/Contents/Developer",
)


def _make_sandbox_env() -> dict[str, str]:
    """Build a minimal env dict from the host, keeping only safe keys.

    Falls back to a hardcoded PATH if the host PATH is missing.
    On macOS, sets ``DEVELOPER_DIR`` when absent — this avoids the
    ``xcode-select`` symlink lookup at ``/var/select/developer_dir`` which
    is blocked when the calling process is already sandboxed (e.g. Claude Code).
    """
    env: dict[str, str] = {}
    for key in _SAFE_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    env.setdefault("PATH", "/usr/bin:/bin:/usr/local/bin")
    if sys.platform == "darwin" and "DEVELOPER_DIR" not in env:
        for candidate in _MACOS_DEVELOPER_DIRS:
            if Path(candidate).is_dir():
                env["DEVELOPER_DIR"] = candidate
                break
    return env


def _make_preexec(cpu_s: int = 30, mem_mb: int = 512, fsize_mb: int = 100) -> Callable[[], None]:
    """Return a preexec_fn callable that sets resource limits and new process group.

    Sets hard caps for:
    - ``RLIMIT_CPU``: CPU time in seconds
    - ``RLIMIT_AS``: Virtual address space in bytes (skipped on macOS/Darwin
      where it is not reliably enforceable)
    - ``RLIMIT_FSIZE``: Maximum file size in bytes

    Also calls ``os.setpgrp()`` to put the child process into a new process group,
    so that a timeout can kill the entire subtree.
    """

    def preexec() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        if sys.platform != "darwin":
            resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024**2, mem_mb * 1024**2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_mb * 1024**2, fsize_mb * 1024**2))
        os.setpgrp()  # new process group → timeout kills entire subtree

    return preexec


class LocalSandboxActor(SandboxActor):
    """Subprocess-based sandbox actor for local filesystem execution.

    Creates and manages a workspace directory under
    ``<AKGENTIC_WORKSPACES_ROOT>/{workspace_id or team_id}/`` (default root:
    ``./workspaces``). When ``SandboxConfig.workspace_id`` is set, that value is
    used as the directory name instead of ``team_id``, enabling directory sharing
    with ``WorkspaceTool(workspace_id=...)``. No Docker daemon required.

    This actor does NOT provide filesystem isolation — an allowed command can still
    read files outside the workspace. It is a development convenience only, not a
    production security boundary.
    """

    def _start_sandbox(self) -> None:
        base = os.environ.get("AKGENTIC_WORKSPACES_ROOT", "./workspaces")
        ws_name = self.config.workspace_id or self.config.team_id
        workspace_path = Path(base) / ws_name
        workspace_path.mkdir(parents=True, exist_ok=True)
        self.state.workspace_path = workspace_path.resolve()
        self.state.notify_state_change()
        logger.debug(
            "LocalSandboxActor started: workspace=%s (no filesystem isolation)",
            self.state.workspace_path,
        )

    def _stop_sandbox(self) -> None:
        logger.debug("LocalSandboxActor stopped.")

    def _exec(self, cmd: str, cwd: str) -> ExecResult:
        assert self.state.workspace_path is not None
        effective_cwd = self.state.workspace_path / cwd if cwd else self.state.workspace_path
        logger.debug("LocalSandboxActor exec: cmd=%r cwd=%s", cmd, effective_cwd)
        try:
            result = subprocess.run(
                cmd.split(),
                cwd=str(effective_cwd),
                capture_output=True,
                text=True,
                timeout=30,
                preexec_fn=_make_preexec(),
                env=_make_sandbox_env(),
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
