"""Hypothesis property tests for the persistent git workspace.

Invariants:
- ``Workspace.init`` is idempotent: calling it any number of times on
  an existing workspace doesn't change ``head_commit()`` or
  ``commit_count()``.
- ``WorkspaceManager.for_project`` is path-stable for the same name.
- Name sanitisation strips slashes and spaces.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aidevswarm.tools.workspace import WorkspaceManager

pytestmark = pytest.mark.property

# Hypothesis writes files; bound the strategy aggressively.
project_names = st.text(
    alphabet=st.characters(
        whitelist_categories=["L", "N"],
        whitelist_characters="-_",
    ),
    min_size=1,
    max_size=20,
).filter(lambda s: s.strip())


@pytest.fixture(autouse=True)
def _skip_without_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git binary not on PATH")


@given(name=project_names)
@settings(max_examples=10, deadline=2000)
def test_workspace_init_is_idempotent(tmp_path_factory: pytest.TempPathFactory, name: str) -> None:
    base = tmp_path_factory.mktemp("ws_idem")
    mgr = WorkspaceManager(base)
    ws = mgr.for_project(name)
    head_after_first = ws.head_commit()
    count_after_first = ws.commit_count()
    # Re-initialise; nothing should change.
    ws.init()
    ws.init()
    assert ws.head_commit() == head_after_first
    assert ws.commit_count() == count_after_first


@given(name=project_names)
@settings(max_examples=10, deadline=2000)
def test_workspace_manager_is_path_stable_for_same_name(
    tmp_path_factory: pytest.TempPathFactory, name: str
) -> None:
    base = tmp_path_factory.mktemp("ws_stable")
    mgr = WorkspaceManager(base)
    first = mgr.for_project(name)
    second = mgr.for_project(name)
    assert first.root == second.root


@given(
    name=st.text(
        alphabet=st.characters(whitelist_categories=["L", "N"]),
        min_size=1,
        max_size=10,
    ).map(lambda s: s + "/with slashes ")
)
@settings(max_examples=5, deadline=2000)
def test_workspace_manager_sanitises_slashes_and_spaces(
    tmp_path_factory: pytest.TempPathFactory, name: str
) -> None:
    base = tmp_path_factory.mktemp("ws_sanitise")
    mgr = WorkspaceManager(base)
    ws = mgr.for_project(name)
    assert "/" not in ws.root.name
    assert " " not in ws.root.name


def test_workspace_manager_rejects_empty_name(tmp_path: Path) -> None:
    mgr = WorkspaceManager(tmp_path)
    with pytest.raises(ValueError):
        mgr.for_project("")
