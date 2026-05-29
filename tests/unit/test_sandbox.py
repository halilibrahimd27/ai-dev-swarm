"""Unit tests for :class:`InMemorySandbox` and :class:`DockerSandbox`.

The Docker path is mocked at the ``subprocess.run`` boundary so no
docker daemon is involved.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from aidevswarm.tools.sandbox import (
    DockerSandbox,
    InMemorySandbox,
    SandboxRun,
    SubprocessSandbox,
    _source_dirs,
)


def test_in_memory_sandbox_default_passes(tmp_path: Path) -> None:
    res = InMemorySandbox().run_ci(str(tmp_path))
    assert res.passed is True
    assert res.exit_code == 0


def test_in_memory_sandbox_honours_fail_flag(tmp_path: Path) -> None:
    (tmp_path / "ci_status").write_text("fail")
    res = InMemorySandbox().run_ci(str(tmp_path))
    assert res.passed is False
    assert res.exit_code == 1


def test_in_memory_sandbox_ignores_unknown_flag(tmp_path: Path) -> None:
    (tmp_path / "ci_status").write_text("???")
    assert InMemorySandbox().run_ci(str(tmp_path)).passed is True


def test_docker_sandbox_run_ci_invokes_docker(tmp_path: Path) -> None:
    sandbox = DockerSandbox(image="test-image", timeout_seconds=30)
    fake_proc = mock.Mock()
    fake_proc.returncode = 0
    fake_proc.stdout = "ok"
    fake_proc.stderr = ""
    with mock.patch.object(subprocess, "run", return_value=fake_proc) as run:
        res = sandbox.run_ci(str(tmp_path))
    assert res.passed is True
    cmd: list[str] = run.call_args[0][0]
    assert cmd[0] == "docker"
    assert "--network=none" in cmd
    assert "test-image" in cmd
    assert any(str(tmp_path) in arg for arg in cmd)


def test_docker_sandbox_propagates_non_zero(tmp_path: Path) -> None:
    sandbox = DockerSandbox()
    fake_proc = mock.Mock()
    fake_proc.returncode = 1
    fake_proc.stdout = ""
    fake_proc.stderr = "boom"
    with mock.patch.object(subprocess, "run", return_value=fake_proc):
        res = sandbox.run_ci(str(tmp_path))
    assert res.passed is False
    assert res.exit_code == 1
    assert "boom" in res.stderr


def test_docker_sandbox_rejects_missing_workspace(tmp_path: Path) -> None:
    sandbox = DockerSandbox()
    with pytest.raises(FileNotFoundError):
        sandbox.run_ci(str(tmp_path / "nope"))


def test_sandbox_run_is_a_dataclass() -> None:
    r = SandboxRun(passed=True, stdout="ok", stderr="", exit_code=0)
    assert r.passed and r.exit_code == 0


# ---------------------------------------------------------------------------
# SubprocessSandbox — mypy source-dir detection (the layout-sensitive bit)
# ---------------------------------------------------------------------------


def test_source_dirs_prefers_src_layout(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    assert _source_dirs(tmp_path) == ["src"]


def test_source_dirs_picks_top_level_packages_excluding_tests(tmp_path: Path) -> None:
    """Reproduces a generated project's `mypy --strict apps packages`."""
    for pkg in ("apps", "packages"):
        d = tmp_path / pkg
        d.mkdir()
        (d / "mod.py").write_text("x = 1\n")
    for noise in ("tests", "alembic", "vendor"):
        d = tmp_path / noise
        d.mkdir()
        (d / "thing.py").write_text("x = 1\n")
    (tmp_path / ".hidden").mkdir()
    assert _source_dirs(tmp_path) == ["apps", "packages"]


def test_source_dirs_falls_back_to_dot(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    assert _source_dirs(tmp_path) == ["."]


def test_subprocess_sandbox_rejects_missing_workspace(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SubprocessSandbox().run_ci(str(tmp_path / "nope"))


def test_subprocess_sandbox_reports_venv_failure(tmp_path: Path) -> None:
    """A failed venv step yields a non-passing run (no real uv needed)."""
    sandbox = SubprocessSandbox(uv_binary="/nonexistent/uv-binary")
    fake_proc = mock.Mock()
    fake_proc.returncode = 127
    fake_proc.stdout = ""
    fake_proc.stderr = "uv: not found"
    with mock.patch.object(subprocess, "run", return_value=fake_proc):
        res = sandbox.run_ci(str(tmp_path))
    assert res.passed is False
    assert res.exit_code == 127
