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


pytestmark = pytest.mark.skipif(not _git_available(), reason="git binary not on PATH")


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
    log = subprocess.check_output(["git", "log", "--oneline"], cwd=ws.root, text=True)
    assert "workspace bootstrap" in log


def test_commits_carry_configured_author(tmp_path: Path) -> None:
    """Generated-repo commits must be authored by the operator's identity."""
    ws = Workspace(
        tmp_path / "author",
        author_name="halilibrahimd27",
        author_email="me@example.com",
    )
    ws.init()
    (ws.root / "f.txt").write_text("x", encoding="utf-8")
    ws.commit_all("feat: f")
    name = subprocess.check_output(
        ["git", "log", "-1", "--format=%an"], cwd=ws.root, text=True
    ).strip()
    email = subprocess.check_output(
        ["git", "log", "-1", "--format=%ae"], cwd=ws.root, text=True
    ).strip()
    assert name == "halilibrahimd27"
    assert email == "me@example.com"


def test_init_reapplies_identity_on_existing_workspace(tmp_path: Path) -> None:
    """A pre-existing workspace picks up the operator identity on re-init.

    Workspaces created before the operator set their author identity must
    not stay stuck with the old default — so init() re-applies it.
    """
    root = tmp_path / "preexisting"
    Workspace(root, author_name="old", author_email="old@local").init()
    # Re-open with a new identity (simulates the operator setting env vars).
    ws = Workspace(root, author_name="halilibrahimd27", author_email="me@example.com")
    ws.init()
    email = subprocess.check_output(["git", "config", "user.email"], cwd=ws.root, text=True).strip()
    assert email == "me@example.com"


# ---------------------------------------------------------------------------
# Remote / push
# ---------------------------------------------------------------------------


def test_set_remote_is_idempotent_and_has_remote(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "epsilon")
    ws.init()
    assert ws.has_remote() is False
    ws.set_remote("https://x-access-token@github.com/me/epsilon.git")
    assert ws.has_remote() is True
    # Calling again updates the URL rather than erroring on a duplicate.
    ws.set_remote("https://x-access-token@github.com/me/epsilon2.git")
    url = subprocess.check_output(
        ["git", "remote", "get-url", "origin"], cwd=ws.root, text=True
    ).strip()
    assert url.endswith("epsilon2.git")


def test_push_to_local_bare_remote(tmp_path: Path) -> None:
    """End-to-end: push lands commits in a bare 'remote' repo."""
    bare = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(bare)])
    ws = Workspace(tmp_path / "zeta")
    ws.init()
    (ws.root / "feature.txt").write_text("payload", encoding="utf-8")
    ws.commit_all("feat: add feature")
    ws.set_remote(f"file://{bare}")
    ws.push("main")
    # The bare repo now has the commit. Use --git-dir (not cwd=) so the
    # check works regardless of the host's `safe.bareRepository` setting
    # (with `=explicit`, git refuses to operate in a bare repo via cwd).
    log = subprocess.check_output(["git", "--git-dir", str(bare), "log", "--oneline"], text=True)
    assert "add feature" in log


def test_push_with_token_keeps_secret_out_of_git_config(tmp_path: Path) -> None:
    """The token must never be persisted in .git/config."""
    bare = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(bare)])
    ws = Workspace(tmp_path / "eta")
    ws.init()
    (ws.root / "f.txt").write_text("x", encoding="utf-8")
    ws.commit_all("feat: f")
    ws.set_remote(f"file://{bare}")
    ws.push("main", token="ghp_supersecret")
    config = (ws.root / ".git" / "config").read_text(encoding="utf-8")
    assert "ghp_supersecret" not in config
