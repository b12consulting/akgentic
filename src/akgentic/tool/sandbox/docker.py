"""DockerSandboxActor — persistent Docker container per team."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from akgentic.tool.sandbox.actor import ExecResult, SandboxActor

SANDBOX_IMAGE: str = "akgentic-sandbox:latest"
DOCKER_EXEC_TIMEOUT: int = 60


class DockerSandboxActor(SandboxActor):
    """Persistent Docker container sandbox per team.

    Manages a single Docker container named ``sandbox-{team_id}``.
    Container is started (or reused) on on_start(), stopped (not removed)
    on on_stop(). The host-side volume mount path uses
    ``{AKGENTIC_WORKSPACES_ROOT}/{workspace_id or team_id}`` so that when
    ``SandboxConfig.workspace_id`` is set, the mounted directory matches the
    one used by ``WorkspaceTool(workspace_id=...)``. The container name always
    uses ``team_id`` — containers are per-team execution resources.
    """

    def _start_sandbox(self) -> None:
        container_name = f"sandbox-{self.config.team_id}"
        if shutil.which("docker") is None:
            raise RuntimeError(
                "docker CLI not found on PATH — cannot start DockerSandboxActor"
            )
        base = os.environ.get("AKGENTIC_WORKSPACES_ROOT", "./workspaces")
        ws_name = self.config.workspace_id or self.config.team_id
        volume = f"{(Path(base) / ws_name).resolve()}:/workspace"
        # Check if container already exists (any state)
        check = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name={container_name}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
        )
        if container_name in check.stdout.splitlines():
            subprocess.run(
                ["docker", "start", container_name],
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "-v",
                    volume,
                    "-w",
                    "/workspace",
                    SANDBOX_IMAGE,
                    "sleep",
                    "infinity",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"docker run failed (exit {result.returncode}): {result.stderr.strip()}"
                )
        self.state.container_name = container_name
        self.state.notify_state_change()

    def _stop_sandbox(self) -> None:
        assert self.state.container_name is not None
        subprocess.run(
            ["docker", "stop", self.state.container_name],
            capture_output=True,
            text=True,
        )
        # Do NOT run docker rm — container filesystem preserved between restarts

    def _exec(self, cmd: str, cwd: str) -> ExecResult:
        assert self.state.container_name is not None
        effective_workdir = f"/workspace/{cwd}" if cwd else "/workspace"
        docker_cmd = [
            "docker",
            "exec",
            "-w",
            effective_workdir,
            self.state.container_name,
        ] + cmd.split()
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=DOCKER_EXEC_TIMEOUT,
        )
        return ExecResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
