"""Ephemeral docker-run CI sandbox for generated projects.

The generated code is untrusted: it is built and tested inside a
short-lived Docker container with no secrets mounted and no host
network. Phase 0 implements the simplest possible flow — ``docker run``
with the workspace bind-mounted read-only — and exposes a
:class:`Protocol`-compatible result.

The real Docker image is defined in ``docker/sandbox.Dockerfile``. If
Docker is unavailable, :class:`InMemorySandbox` lets tests run without
it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SANDBOX_IMAGE = "aidevswarm-sandbox:latest"


@dataclass(frozen=True)
class SandboxRun:
    """Outcome of one CI gate invocation."""

    passed: bool
    stdout: str
    stderr: str
    exit_code: int


class DockerSandbox:
    """Concrete :class:`aidevswarm.tools.protocols.Sandbox`."""

    def __init__(
        self,
        image: str = SANDBOX_IMAGE,
        timeout_seconds: int = 1800,
    ) -> None:
        self._image = image
        self._timeout = timeout_seconds

    def run_ci(self, workspace_dir: str) -> SandboxRun:
        ws_path = Path(workspace_dir).resolve()
        if not ws_path.is_dir():
            raise FileNotFoundError(workspace_dir)
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "-v",
            f"{ws_path}:/workspace:ro",
            "-w",
            "/workspace",
            self._image,
            "/usr/local/bin/run-ci.sh",
        ]
        proc = subprocess.run(  # noqa: S603
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=self._timeout,
        )
        return SandboxRun(
            passed=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
        )


class InMemorySandbox:
    """Substitutes for :class:`DockerSandbox` in tests / dry runs.

    Reads a flag file ``ci_status`` from the workspace if present
    (``pass`` / ``fail``) so smoke tests can simulate either outcome.
    """

    def __init__(self, docker_binary: str | None = None) -> None:
        # docker_binary is unused — kept for parity with DockerSandbox.
        self._docker = docker_binary or shutil.which("docker")

    def run_ci(self, workspace_dir: str) -> SandboxRun:
        flag_path = Path(workspace_dir) / "ci_status"
        if flag_path.is_file() and flag_path.read_text().strip() == "fail":
            return SandboxRun(passed=False, stdout="", stderr="forced fail", exit_code=1)
        return SandboxRun(passed=True, stdout="ok", stderr="", exit_code=0)
