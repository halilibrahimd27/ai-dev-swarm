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

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from aidevswarm.logging_config import get_logger

SANDBOX_IMAGE = "aidevswarm-sandbox:latest"

# Directories never treated as type-checked source (tests carry looser
# typing; the rest are tooling/vendored/build output).
_NON_SOURCE_DIRS = frozenset(
    {"tests", "test", "vendor", "alembic", "migrations", "build", "dist", "docs", "scripts"}
)


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
        proc = subprocess.run(
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


class SubprocessSandbox:
    """Run the real CI gate in a throwaway venv — no Docker required.

    The orchestrator container has no Docker socket, so ``DockerSandbox``
    can't run; the alternative used to be ``InMemorySandbox`` (CI = free
    pass), which meant generated tests never actually ran. This sandbox
    installs the generated project into an ephemeral ``uv`` venv and runs
    its gate (``ruff check`` + ``mypy --strict`` + ``pytest``), so broken
    code or failing tests genuinely block the milestone.

    It is LESS isolated than ``DockerSandbox`` (the gate runs in the
    orchestrator's own container, with network), which is an accepted
    trade-off for a single-operator local system: the gain is that tests
    are actually executed.
    """

    def __init__(self, timeout_seconds: int = 1800, uv_binary: str | None = None) -> None:
        self._timeout = timeout_seconds
        self._uv = uv_binary or shutil.which("uv") or "uv"
        self._log = get_logger(__name__)

    def run_ci(self, workspace_dir: str) -> SandboxRun:
        ws = Path(workspace_dir).resolve()
        if not ws.is_dir():
            raise FileNotFoundError(workspace_dir)
        venv_dir = Path(tempfile.mkdtemp(prefix="aidevswarm-ci-"))
        try:
            return self._run_gate(ws, venv_dir)
        except subprocess.TimeoutExpired as exc:
            return SandboxRun(
                passed=False,
                stdout="",
                stderr=f"CI timed out after {self._timeout}s: {exc}",
                exit_code=124,
            )
        finally:
            shutil.rmtree(venv_dir, ignore_errors=True)

    def _run_gate(self, ws: Path, venv_dir: Path) -> SandboxRun:
        logs: list[str] = []
        rc, out = self._sh([self._uv, "venv", str(venv_dir), "--python", sys.executable], ws)
        logs.append(_group("venv", out))
        if rc != 0:
            return _fail(logs, rc)

        bin_dir = venv_dir / "bin"
        logs.append(_group("install", self._install(ws, str(bin_dir / "python"))))

        for label, cmd in self._gate_steps(ws, bin_dir):
            rc, out = self._sh(cmd, ws, extra_path=str(bin_dir))
            logs.append(_group(label, out))
            if rc != 0:
                return _fail(logs, rc)
        return SandboxRun(passed=True, stdout="\n".join(logs), stderr="", exit_code=0)

    def _install(self, ws: Path, python: str) -> str:
        """Install the project (+dev extras) and the gate tools."""
        out = ""
        if (ws / "pyproject.toml").exists() or (ws / "setup.py").exists():
            rc, text = self._uv_pip(python, ws, "-e", ".[dev]")
            out += text
            if rc != 0:  # no [dev] extra (or it failed) — fall back to bare install
                _, text2 = self._uv_pip(python, ws, "-e", ".")
                out += text2
        # Ensure the gate tools exist even if the project didn't declare them.
        _, text3 = self._uv_pip(python, ws, "ruff", "mypy", "pytest", "hypothesis")
        return out + text3

    def _uv_pip(self, python: str, ws: Path, *args: str) -> tuple[int, str]:
        return self._sh([self._uv, "pip", "install", "--python", python, *args], ws)

    def _gate_steps(self, ws: Path, bin_dir: Path) -> list[tuple[str, list[str]]]:
        return [
            ("ruff", [str(bin_dir / "ruff"), "check", "."]),
            ("mypy", [str(bin_dir / "mypy"), "--strict", *_source_dirs(ws)]),
            # no:cacheprovider keeps the run hermetic; the project's
            # [tool.pytest] testpaths already scope collection.
            ("pytest", [str(bin_dir / "pytest"), "-q", "-p", "no:cacheprovider"]),
        ]

    def _sh(self, cmd: list[str], cwd: Path, *, extra_path: str | None = None) -> tuple[int, str]:
        # Untrusted generated code runs here (pytest imports the project's
        # conftest/build hooks). It must NOT inherit the orchestrator's
        # secrets — pass only a minimal, secret-free environment. True
        # network isolation still requires sandbox_mode=docker; see the
        # class docstring + THREAT_MODEL.
        env = _scrubbed_env()
        if extra_path:
            env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=self._timeout,
            env=env,
        )
        return proc.returncode, (proc.stdout + proc.stderr)


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


# Environment variables the CI subprocess is allowed to see. Everything
# else from the orchestrator's environment — crucially every secret
# (ANTHROPIC_API_KEY, GITHUB_TOKEN, TELEGRAM_*, POSTGRES_*, AIDEVSWARM_*)
# — is dropped, so untrusted generated code can't read them.
_SANDBOX_ENV_ALLOW = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USER",
        "LOGNAME",
        "SHELL",
        "SYSTEMROOT",  # Windows: many tools break without it
    }
)


def _scrubbed_env() -> dict[str, str]:
    """A minimal, secret-free environment for the CI subprocess."""
    env = {k: v for k, v in os.environ.items() if k in _SANDBOX_ENV_ALLOW}
    # Keep venvs hermetic and predictable regardless of the host shell.
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def _source_dirs(ws: Path) -> list[str]:
    """Best-effort set of dirs to type-check with ``mypy --strict``.

    A ``src/`` layout wins outright. Otherwise: every top-level directory
    that contains Python files and isn't tests/tooling/vendored. This
    reproduces what a typical generated project declares for mypy (e.g.
    ``mypy --strict apps packages``) without parsing its config.
    """
    if (ws / "src").is_dir():
        return ["src"]
    dirs: list[str] = []
    for child in sorted(ws.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in _NON_SOURCE_DIRS:
            continue
        if next(child.rglob("*.py"), None) is not None:
            dirs.append(child.name)
    return dirs or ["."]


def _group(label: str, body: str) -> str:
    return f"::group::{label}\n{body}\n::endgroup::"


def _fail(logs: Iterable[str], exit_code: int) -> SandboxRun:
    joined = "\n".join(logs)
    return SandboxRun(passed=False, stdout=joined, stderr=joined[-2000:], exit_code=exit_code)
