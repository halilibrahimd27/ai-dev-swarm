"""Operator-command dispatcher.

Both the FastAPI ``/api/commands`` endpoint and the Telegram bot
funnel into ``CommandRouter.dispatch(command)``. The router is the
ONLY place that translates a ``Command`` into orchestrator state —
neither surface duplicates business logic.

The router does NOT block on long-running work. ``inject_note``
returns once the row is written; ``abort_project`` returns once the
kill switch is tripped. The scheduler picks the change up on the
next tick.

Destructive commands MUST be confirmed before reaching the router —
the surface layer (web confirm dialog, Telegram ``[Yes][No]``
inline keyboard) flips ``confirmed`` to True. The router enforces
this with a guard so a bug in either surface can't bypass the
confirmation step.
"""

from __future__ import annotations

from dataclasses import dataclass

from aidevswarm.db.protocols import ProjectRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import (
    AbortProject,
    Approve,
    Command,
    DropAndStartNew,
    InjectNote,
    KillSwitch,
    ListState,
    PauseProject,
    ProjectState,
    RejectIdea,
    Rescope,
    ResumeProject,
    ShowTranscript,
    SwitchToIdea,
    TransformProject,
    requires_confirmation,
)
from aidevswarm.steering import SteeringRepo
from aidevswarm.tools.protocols import KillSwitch as KillSwitchProto


class UnconfirmedDestructiveCommand(ValueError):
    """Raised when a destructive command lands at the router unconfirmed."""


@dataclass
class CommandResult:
    """Operator-facing reply describing what the router did."""

    ok: bool
    intent: str
    detail: str
    requires_confirmation: bool = False


@dataclass
class CommandRouter:
    """Shared dispatcher behind the web UI + Telegram bot."""

    project_repo: ProjectRepo
    steering_repo: SteeringRepo
    kill_switch: KillSwitchProto

    def __post_init__(self) -> None:
        self._log = get_logger(__name__)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def dispatch(self, command: Command) -> CommandResult:
        """Translate one typed Command into orchestrator state."""
        intent = command.intent

        # Guard: destructive intents must arrive confirmed.
        if requires_confirmation(command):
            return CommandResult(
                ok=False,
                intent=intent,
                detail=f"{intent} is destructive — confirm via [Yes][No] first.",
                requires_confirmation=True,
            )

        match command:
            case Approve():
                return self._approve(command)
            case InjectNote():
                return self._inject_note(command)
            case PauseProject():
                return self._pause(command)
            case ResumeProject():
                return self._resume(command)
            case AbortProject():
                return self._abort(command)
            case Rescope():
                return self._rescope(command)
            case TransformProject():
                return self._transform(command)
            case DropAndStartNew():
                return self._drop_and_start_new(command)
            case SwitchToIdea():
                return self._switch_to_idea(command)
            case RejectIdea():
                return self._reject_idea(command)
            case KillSwitch():
                return self._kill_switch(command)
            case ListState():
                return self._list_state(command)
            case ShowTranscript():
                return self._show_transcript(command)

    # ------------------------------------------------------------------
    # Non-destructive handlers
    # ------------------------------------------------------------------

    def _approve(self, cmd: Approve) -> CommandResult:
        project = self.project_repo.get(cmd.project_id)
        if project is None:
            return CommandResult(ok=False, intent=cmd.intent, detail="project not found")
        if project.state is not ProjectState.AWAITING_APPROVAL:
            return CommandResult(
                ok=False,
                intent=cmd.intent,
                detail=f"project is in {project.state.value}, not awaiting_approval",
            )
        self.project_repo.update_state(cmd.project_id, ProjectState.BUILDING)
        self._log.info("router.approved", project_id=str(cmd.project_id))
        return CommandResult(ok=True, intent=cmd.intent, detail="approved")

    def _inject_note(self, cmd: InjectNote) -> CommandResult:
        note_id = self.steering_repo.add_note(cmd.project_id, cmd.body, author=cmd.author)
        self._log.info(
            "router.note_injected",
            project_id=str(cmd.project_id),
            note_id=note_id,
            role=cmd.role,
        )
        return CommandResult(ok=True, intent=cmd.intent, detail=f"note #{note_id} delivered")

    def _pause(self, cmd: PauseProject) -> CommandResult:
        # Pause uses the per-project kill switch as the signal; the
        # scheduler skips tripped projects. A subsequent ResumeProject
        # calls reset_for to lift it. The project's stored state is
        # NOT changed (so it can resume into its current phase).
        self.kill_switch.trip_for(cmd.project_id, reason="paused by operator")
        self._log.info("router.paused", project_id=str(cmd.project_id))
        return CommandResult(ok=True, intent=cmd.intent, detail="paused")

    def _resume(self, cmd: ResumeProject) -> CommandResult:
        self.kill_switch.reset_for(cmd.project_id)
        self._log.info("router.resumed", project_id=str(cmd.project_id))
        return CommandResult(ok=True, intent=cmd.intent, detail="resumed")

    def _list_state(self, _cmd: ListState) -> CommandResult:
        # The actual project list is served by the GET /api/projects
        # endpoint; this is a no-op acknowledgement so the bot has a
        # consistent return-shape per command.
        return CommandResult(ok=True, intent=_cmd.intent, detail="see /api/projects")

    def _show_transcript(self, _cmd: ShowTranscript) -> CommandResult:
        return CommandResult(
            ok=True,
            intent=_cmd.intent,
            detail="see /sse/transcript/{project_id}",
        )

    # ------------------------------------------------------------------
    # Destructive handlers (only reached if confirmed=True)
    # ------------------------------------------------------------------

    def _abort(self, cmd: AbortProject) -> CommandResult:
        self.kill_switch.trip_for(cmd.project_id, reason=cmd.reason)
        self._log.info("router.aborted", project_id=str(cmd.project_id), reason=cmd.reason)
        return CommandResult(ok=True, intent=cmd.intent, detail="kill switch tripped")

    def _rescope(self, cmd: Rescope) -> CommandResult:
        # The actual rescope is a Steering note + a forced replan.
        # The replanner picks up the note on its next pass.
        note_id = self.steering_repo.add_note(
            cmd.project_id,
            f"[OPERATOR RESCOPE] {cmd.new_scope}",
            author="human",
        )
        return CommandResult(
            ok=True,
            intent=cmd.intent,
            detail=f"rescope note #{note_id} queued",
        )

    def _transform(self, cmd: TransformProject) -> CommandResult:
        note_id = self.steering_repo.add_note(
            cmd.project_id,
            f"[OPERATOR TRANSFORM] {cmd.new_direction}",
            author="human",
        )
        return CommandResult(
            ok=True,
            intent=cmd.intent,
            detail=f"transform note #{note_id} queued",
        )

    def _drop_and_start_new(self, cmd: DropAndStartNew) -> CommandResult:
        self.kill_switch.trip_for(cmd.project_id, reason="drop_and_start_new (operator)")
        # The ideation crew will pick a new project on the next tick.
        return CommandResult(ok=True, intent=cmd.intent, detail="current project dropped")

    def _switch_to_idea(self, cmd: SwitchToIdea) -> CommandResult:
        self.kill_switch.trip_for(
            cmd.current_project_id, reason=f"switching to idea {cmd.new_idea_id}"
        )
        return CommandResult(
            ok=True,
            intent=cmd.intent,
            detail=f"current project dropped; idea {cmd.new_idea_id} will be picked up",
        )

    def _reject_idea(self, cmd: RejectIdea) -> CommandResult:
        # We don't yet have an Idea repo — Phase 5 keeps this best-effort:
        # the rejection is logged for the ideation crew to read on
        # the next pass. A dedicated Idea row arrives in Phase 6.
        self._log.info("router.idea_rejected", idea_id=str(cmd.idea_id))
        return CommandResult(ok=True, intent=cmd.intent, detail="idea rejection logged")

    def _kill_switch(self, cmd: KillSwitch) -> CommandResult:
        self.kill_switch.trip(reason=cmd.reason)
        self._log.warning("router.kill_switch_tripped", reason=cmd.reason)
        return CommandResult(ok=True, intent=cmd.intent, detail="global kill switch tripped")


__all__ = ["CommandRouter", "CommandResult", "UnconfirmedDestructiveCommand"]
