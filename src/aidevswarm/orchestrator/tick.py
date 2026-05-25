"""Advance one project by exactly one state-machine step.

Phase 4 reshape: the primitive is ``Tick.advance_project(project)`` —
the scheduler decides WHICH project to advance. ``advance_one_step``
stays for Phase 0/1 single-project semantics + the smoke test.

Per-tick responsibilities:
  * ideas        -> :class:`IdeationCrew`
  * milestone graph -> :class:`PlanningCrew`
  * milestone build -> :class:`BuildCrew` (+ Sandbox CI gate)
  * replanner    -> :class:`ReplanningCrew` + :class:`AutoSplitPredictor`
  * persistence  -> :class:`ProjectRepo` / :class:`MilestoneRepo`
                    / :class:`MilestoneSessionRepo`
  * notifications -> :class:`Telegram`
  * publish      -> :class:`GitHubTool`
"""

from __future__ import annotations

from dataclasses import dataclass

from aidevswarm.crews.protocols import BuildCrew, IdeationCrew, PlanningCrew
from aidevswarm.crews.replanning.protocols import ReplanningCrew
from aidevswarm.db.protocols import MilestoneRepo, ProjectRepo
from aidevswarm.db.sessions import MilestoneSessionRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.consolidation import (
    build_consolidation_spec,
    should_insert_consolidation,
)
from aidevswarm.orchestrator.state_machine import (
    assert_legal_milestone,
    assert_legal_project,
)
from aidevswarm.schemas import (
    Amend,
    Escalate,
    Milestone,
    MilestoneState,
    Noop,
    Project,
    ProjectState,
    ReplannerAction,
    Split,
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
    session_repo: MilestoneSessionRepo
    ideation_crew: IdeationCrew
    planning_crew: PlanningCrew
    build_crew: BuildCrew
    replanning_crew: ReplanningCrew
    auto_split: AutoSplitPredictor
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
        """Single-project tick (Phase 0/1 entry; smoke tests use this)."""
        if self._d.kill_switch.is_tripped():
            self._log.info("tick.kill_switch_tripped")
            return None

        active = self._d.project_repo.get_active()
        if active is not None:
            return self.advance_project(active)

        # No project active -> pick the next queued one if any.
        queued = self._d.project_repo.list_by_state(ProjectState.QUEUED)
        if not queued:
            return None
        return self._move(queued[0], ProjectState.PLANNING)

    def advance_project(self, project: Project) -> Project | None:
        """Advance ``project`` by exactly one state-machine step.

        Phase 4 primitive. The scheduler pool calls this per worker.
        Per-project kill switch checked first.
        """
        if self._d.kill_switch.is_tripped():
            return None
        if self._d.kill_switch.is_tripped_for(project.id):
            self._log.info("tick.project_killed", project=project.name)
            return self._move(project, ProjectState.KILLED)
        return self._advance_active(project)

    # ------------------------------------------------------------------
    # Per-state handlers
    # ------------------------------------------------------------------

    def _advance_active(self, project: Project) -> Project | None:
        match project.state:
            case ProjectState.QUEUED:
                return self._move(project, ProjectState.PLANNING)
            case ProjectState.PLANNING:
                return self._plan(project)
            case ProjectState.AWAITING_APPROVAL:
                return None  # waits for an external approval event
            case ProjectState.BUILDING:
                return self._build_one_milestone(project)
            case ProjectState.REPLANNING:
                return self._replan(project)
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
            # Retry -> Phase 4 routes through the replanner.
            return self._move(project, ProjectState.REPLANNING)

        # Success: commit and record.
        if workspace.is_dirty():
            commit = workspace.commit_all(
                f"feat({_slug(milestone.title)}): {milestone.spec.description[:70]}"
            )
            commit_hash: str | None = commit.commit_hash
        else:
            commit_hash = result.commit_hash
        self._d.milestone_repo.record_attempt(milestone.id, success=True, commit_hash=commit_hash)
        # Phase 4: route to replanner so it can decide what (if anything)
        # to change about the upcoming milestone, and check the
        # consolidation cadence.
        return self._move(project, ProjectState.REPLANNING)

    def _replan(self, project: Project) -> Project | None:
        """Phase 4 replanning state.

        Inserts consolidation milestones at the right cadence, then
        runs the cheap AutoSplit heuristic, then (only if the
        heuristic didn't fire) the LLM-driven Replanner crew.
        """
        milestones = self._d.milestone_repo.list_for_project(project.id)

        # 1) Consolidation cadence.
        if should_insert_consolidation(milestones, every=self._d.settings.consolidation_every):
            last_done = _last_done(milestones)
            if last_done is not None:
                self._d.milestone_repo.insert_after(last_done.id, build_consolidation_spec())
                self._log.info(
                    "tick.consolidation_inserted",
                    project=project.name,
                    after=last_done.title,
                )

        # 2) Pick the next pending milestone (might be the consolidation
        #    we just inserted, or a regular one).
        next_milestone = self._d.milestone_repo.next_pending(project.id)
        if next_milestone is None:
            return self._move(project, ProjectState.INTEGRATION)

        # 3) Auto-split (cheap): if predicted over budget, mechanically
        #    bisect the milestone WITHOUT calling the LLM.
        cheap_split = self._d.auto_split.predict(next_milestone)
        if cheap_split is not None:
            return self._apply_action(project, cheap_split)

        # 4) Replanner crew (LLM): always Noop-on-error so we never
        #    take the project down because of a tracing or quota blip.
        recent_sessions = _recent_sessions(self._d.session_repo, milestones, limit=6)
        action = self._d.replanning_crew.run(
            project=project,
            next_milestone=next_milestone,
            recent_sessions=recent_sessions,
        )
        return self._apply_action(project, action)

    def _integrate(self, project: Project) -> Project:
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
    # Replanner action application
    # ------------------------------------------------------------------

    def _apply_action(self, project: Project, action: ReplannerAction) -> Project:
        match action:
            case Noop():
                self._log.info("replanner.noop", project=project.name)
                return self._move(project, ProjectState.BUILDING)
            case Amend():
                self._d.milestone_repo.update_spec(action.milestone_id, action.patch)
                self._log.info(
                    "replanner.amend",
                    project=project.name,
                    milestone=str(action.milestone_id),
                )
                return self._move(project, ProjectState.BUILDING)
            case Split():
                self._d.milestone_repo.replace_with(action.milestone_id, action.into)
                self._log.info(
                    "replanner.split",
                    project=project.name,
                    milestone=str(action.milestone_id),
                    children=len(action.into),
                )
                return self._move(project, ProjectState.BUILDING)
            case Escalate():
                self._d.telegram.send(
                    f"[ai-dev-swarm] project '{project.name}' escalated: {action.reason}"
                )
                self._log.info("replanner.escalate", project=project.name, reason=action.reason)
                return self._move(project, ProjectState.BLOCKED)

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


def _last_done(milestones: list[Milestone]) -> Milestone | None:
    done = [m for m in milestones if m.state is MilestoneState.DONE]
    if not done:
        return None
    return max(done, key=lambda m: m.ordinal)


def _recent_sessions(
    session_repo: MilestoneSessionRepo,
    milestones: list[Milestone],
    *,
    limit: int = 6,
) -> list:  # type: ignore[type-arg]
    """Latest per-role sessions across the most-recent ``limit`` milestones."""
    recent = sorted(milestones, key=lambda m: m.ordinal, reverse=True)[:limit]
    out = []
    for m in recent:
        for role in ("Developer", "Tester"):
            s = session_repo.latest_for(m.id, role)
            if s is not None:
                out.append(s)
    return out


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-")[:32] or "milestone"


__all__ = ["Tick", "TickDeps", "assert_legal_milestone"]
