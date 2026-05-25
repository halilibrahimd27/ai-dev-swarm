"""Advance the one active project by exactly one state-machine step.

The orchestrator's main loop calls :meth:`Tick.advance_one_step` every
``settings.tick_seconds`` seconds. Resume across restarts is automatic
because every transition is persisted to Postgres before the function
returns.

This module orchestrates work but does NOT generate code. It delegates:

  * ideas        -> :class:`IdeationCrew`
  * milestone graph -> :class:`PlanningCrew`
  * milestone build -> :class:`BuildCrew` (+ Sandbox CI gate)
  * persistence  -> :class:`ProjectRepo` / :class:`MilestoneRepo`
  * notifications -> :class:`Telegram`
  * publish      -> :class:`GitHubTool`
"""

from __future__ import annotations

from dataclasses import dataclass

from aidevswarm.crews.protocols import BuildCrew, IdeationCrew, PlanningCrew
from aidevswarm.db.protocols import MilestoneRepo, ProjectRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.orchestrator.state_machine import (
    assert_legal_milestone,
    assert_legal_project,
)
from aidevswarm.schemas import (
    MilestoneState,
    Project,
    ProjectState,
)
from aidevswarm.settings import Settings
from aidevswarm.tools.protocols import (
    GitHubTool,
    KillSwitch,
    Sandbox,
    Telegram,
)
from aidevswarm.tools.workspace import WorkspaceManager


@dataclass
class TickDeps:
    """All collaborators the tick needs, bundled for ergonomic wiring."""

    settings: Settings
    project_repo: ProjectRepo
    milestone_repo: MilestoneRepo
    ideation_crew: IdeationCrew
    planning_crew: PlanningCrew
    build_crew: BuildCrew
    workspace_manager: WorkspaceManager
    sandbox: Sandbox
    telegram: Telegram
    github: GitHubTool
    kill_switch: KillSwitch


class Tick:
    """One orchestrator step. Stateless apart from its dependencies."""

    def __init__(self, deps: TickDeps) -> None:
        self._d = deps
        self._log = get_logger(__name__)

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def advance_one_step(self) -> Project | None:
        """Advance the one active project. Return it if anything happened."""
        if self._d.kill_switch.is_tripped():
            self._log.info("tick.kill_switch_tripped")
            return None

        active = self._d.project_repo.get_active()
        if active is not None:
            return self._advance_active(active)

        # No project building -> pick the next queued one if any.
        queued = self._d.project_repo.list_by_state(ProjectState.QUEUED)
        if not queued:
            return None
        return self._move(queued[0], ProjectState.PLANNING)

    # ------------------------------------------------------------------
    # Per-state handlers
    # ------------------------------------------------------------------

    def _advance_active(self, project: Project) -> Project | None:
        match project.state:
            case ProjectState.PLANNING:
                return self._plan(project)
            case ProjectState.AWAITING_APPROVAL:
                return None  # waits for an external approval event
            case ProjectState.BUILDING:
                return self._build_one_milestone(project)
            case ProjectState.INTEGRATION:
                return self._integrate(project)
            case _:
                return None

    def _plan(self, project: Project) -> Project:
        graph = self._d.planning_crew.run(project.id, project.spec)
        self._d.milestone_repo.create_many(project.id, graph.milestones)
        next_state = (
            ProjectState.AWAITING_APPROVAL
            if self._d.settings.require_approval
            else ProjectState.BUILDING
        )
        if next_state is ProjectState.AWAITING_APPROVAL:
            self._d.telegram.send(
                f"[ai-dev-swarm] project '{project.name}' awaits plan approval "
                f"({len(graph.milestones)} milestones)."
            )
        return self._move(project, next_state)

    def _build_one_milestone(self, project: Project) -> Project | None:
        milestone = self._d.milestone_repo.next_pending(project.id)
        if milestone is None:
            return self._move(project, ProjectState.INTEGRATION)

        self._d.milestone_repo.update_state(milestone.id, MilestoneState.BUILDING)
        workspace = self._d.workspace_manager.for_project(project.name)

        result = self._d.build_crew.run(
            milestone=milestone,
            workspace=workspace,
            sandbox=self._d.sandbox,
        )

        if not result.success:
            self._d.milestone_repo.record_attempt(milestone.id, success=False, commit_hash=None)
            self._log.info(
                "tick.milestone_failed",
                project=project.name,
                milestone=milestone.title,
                retry_count=milestone.retry_count,
                reason=result.failure_reason,
            )
            if milestone.retry_count + 1 >= self._d.settings.milestone_retry_limit:
                self._d.telegram.send(
                    f"[ai-dev-swarm] project '{project.name}' blocked on "
                    f"milestone '{milestone.title}'."
                )
                return self._move(project, ProjectState.BLOCKED)
            return project  # retry on next tick

        # Success: commit and record.
        if workspace.is_dirty():
            commit = workspace.commit_all(
                f"feat({_slug(milestone.title)}): {milestone.spec.description[:70]}"
            )
            commit_hash: str | None = commit.commit_hash
        else:
            commit_hash = result.commit_hash
        self._d.milestone_repo.record_attempt(milestone.id, success=True, commit_hash=commit_hash)
        return project  # stay in BUILDING; next tick picks the next milestone

    def _integrate(self, project: Project) -> Project:
        # Phase 0 integration is intentionally minimal: open the PR.
        if project.github_repo:
            try:
                pr_url = self._d.github.open_pr(
                    repo_url=project.github_repo,
                    branch="main",
                    title=f"Initial release: {project.name}",
                    body=project.spec.summary,
                )
                self._d.telegram.send(f"[ai-dev-swarm] '{project.name}' shipped: {pr_url}")
            except Exception as exc:
                self._log.warning("tick.publish_failed", error=str(exc))
        return self._move(project, ProjectState.DONE)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _move(self, project: Project, new_state: ProjectState) -> Project:
        assert_legal_project(project.state, new_state)
        updated = self._d.project_repo.update_state(project.id, new_state)
        self._log.info(
            "tick.transition",
            project=project.name,
            from_=project.state.value,
            to=new_state.value,
        )
        return updated


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-")[:32] or "milestone"


# Re-export so callers can move milestones without importing the module
# tree below.
__all__ = ["Tick", "TickDeps", "assert_legal_milestone"]
