"""Unit tests for the persistent git workspace tool.

Real ``git`` is invoked against a ``tmp_path`` directory — git is
ubiquitous in dev environments, and shelling out keeps the test honest
about how the workspace actually behaves in production.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aidevswarm.tools.workspace import GitError, Workspace, WorkspaceManager


def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(
    not _git_available(), reason="git binary not on PATH"
)


def test_init_is_idempotent(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "alpha")
    ws.init()
    assert ws.exists()
    first_head = ws.head_commit()
    # Calling init again must not create a new commit or wipe state.
    ws.init()
    assert ws.head_commit() == first_head


def test_commit_all_persists_files(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "beta")
    ws.init()
    (ws.root / "hello.txt").write_text("hi", encoding="utf-8")
    assert ws.is_dirty()
    result = ws.commit_all("feat: hello")
    assert len(result.commit_hash) == 40
    assert ws.is_dirty() is False
    assert ws.commit_count() == 2  # bootstrap + this commit


def test_commit_all_with_clean_tree_raises(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "gamma")
    ws.init()
    with pytest.raises(GitError):
        ws.commit_all("nothing to do")


def test_workspace_manager_returns_distinct_dirs(tmp_path: Path) -> None:
    mgr = WorkspaceManager(tmp_path / "ws")
    a = mgr.for_project("alpha")
    b = mgr.for_project("beta")
    assert a.root != b.root
    assert a.exists() and b.exists()


def test_workspace_manager_sanitises_name(tmp_path: Path) -> None:
    mgr = WorkspaceManager(tmp_path / "ws")
    weird = mgr.for_project("with spaces/and-slashes")
    assert "/" not in weird.root.name
    assert " " not in weird.root.name


def test_workspace_manager_rejects_empty(tmp_path: Path) -> None:
    mgr = WorkspaceManager(tmp_path / "ws")
    with pytest.raises(ValueError):
        mgr.for_project("   ")


def test_git_log_has_initial_bootstrap_commit(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "delta")
    ws.init()
    log = subprocess.check_output(
        ["git", "log", "--oneline"], cwd=ws.root, text=True
    )
    assert "workspace bootstrap" in log
