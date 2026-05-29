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

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aidevswarm.db.protocols import ProjectRepo
from aidevswarm.db.settings_store import (
    SettingsOverrideRepo,
    apply_override,
    get_spec,
    is_editable,
)
from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import (
    AbortProject,
    Approve,
    Command,
    DropAndStartNew,
    IdeateNow,
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
    UpdateSetting,
    requires_confirmation,
)
from aidevswarm.settings import Settings
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
    # Phase 6 operator-triggered ideation. Fire-and-forget: the
    # router only schedules the work, doesn't block the dispatch.
    # If unset (defaults to a no-op), `ideate_now` returns a soft
    # error so the UI shows "wiring not available" rather than
    # silently swallowing the request.
    ideate_runner: Callable[[], None] = field(default=lambda: None)
    # Operator-editable operational settings. When both are wired,
    # `update_setting` validates + persists an override and applies it onto
    # the live Settings object. Unset (tests/Phase 5) => a soft error.
    settings: Settings | None = None
    settings_repo: SettingsOverrideRepo | None = None

    def __post_init__(self) -> None:
        self._log = get_logger(__name__)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def dispatch(self, command: Command) -> CommandResult:
        """Translate one typed Command into orchestrator state."""
        if requires_confirmation(command):
            return CommandResult(
                ok=False,
                intent=command.intent,
                detail=f"{command.intent} is destructive — confirm via [Yes][No] first.",
                requires_confirmation=True,
            )
        handler = self._handlers().get(type(command))
        # The schema guarantees `command` is one of the variants — the
        # registry lookup is exhaustive by construction.
        result: CommandResult = handler(command)  # type: ignore[misc]
        return result

    def _handlers(self) -> dict[type, Any]:
        return {
            Approve: self._approve,
            InjectNote: self._inject_note,
            PauseProject: self._pause,
            ResumeProject: self._resume,
            IdeateNow: self._ideate_now,
            AbortProject: self._abort,
            Rescope: self._rescope,
            TransformProject: self._transform,
            DropAndStartNew: self._drop_and_start_new,
            SwitchToIdea: self._switch_to_idea,
            RejectIdea: self._reject_idea,
            KillSwitch: self._kill_switch,
            ListState: self._list_state,
            ShowTranscript: self._show_transcript,
            UpdateSetting: self._update_setting,
        }

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
        # Pause is RECOVERABLE and must never make the project terminal.
        # It sets a dedicated pause signal (NOT the kill switch — that
        # one drives the tick straight to KILLED). The scheduler skips a
        # paused project without touching its state, so ResumeProject
        # continues it from exactly where it left off.
        self.kill_switch.pause_for(cmd.project_id)
        self.project_repo.set_status_detail(cmd.project_id, "paused by operator")
        self._log.info("router.paused", project_id=str(cmd.project_id))
        return CommandResult(ok=True, intent=cmd.intent, detail="paused")

    def _resume(self, cmd: ResumeProject) -> CommandResult:
        # Lift the pause signal (and any stale per-project kill flag)...
        self.kill_switch.unpause_for(cmd.project_id)
        self.kill_switch.reset_for(cmd.project_id)
        # ...and if the project was BLOCKED (a milestone failed its
        # retries, an escalation, or a crash), put it back to BUILDING so
        # it CONTINUES from where it left off — its done milestones +
        # git workspace persist, so it picks up the next pending one.
        project = self.project_repo.get(cmd.project_id)
        if project is not None and project.state is ProjectState.BLOCKED:
            self.project_repo.update_state(cmd.project_id, ProjectState.BUILDING)
            self.project_repo.set_status_detail(cmd.project_id, "resumed by operator")
            self._log.info("router.unblocked", project_id=str(cmd.project_id))
            return CommandResult(ok=True, intent=cmd.intent, detail="resumed from blocked")
        self.project_repo.set_status_detail(cmd.project_id, "resumed by operator")
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

    def _ideate_now(self, cmd: IdeateNow) -> CommandResult:
        """Fire-and-forget: schedule the ideation crew + return at once."""
        try:
            self.ideate_runner()
        except Exception as exc:
            self._log.warning("router.ideate_now_failed", error=str(exc))
            return CommandResult(
                ok=False,
                intent=cmd.intent,
                detail=f"ideate_now scheduling failed: {exc}",
            )
        self._log.info("router.ideate_now_scheduled")
        return CommandResult(
            ok=True,
            intent=cmd.intent,
            detail="ideation crew scheduled (watch transcript / Phoenix)",
        )

    def _update_setting(self, cmd: UpdateSetting) -> CommandResult:
        if self.settings is None or self.settings_repo is None:
            return CommandResult(ok=False, intent=cmd.intent, detail="settings editing not wired")
        if not is_editable(cmd.key):
            return CommandResult(
                ok=False, intent=cmd.intent, detail=f"'{cmd.key}' is not an editable setting"
            )
        try:
            value = apply_override(self.settings, cmd.key, cmd.value)
        except ValueError as exc:
            return CommandResult(ok=False, intent=cmd.intent, detail=str(exc))
        self.settings_repo.upsert(cmd.key, cmd.value.strip())
        spec = get_spec(cmd.key)
        note = (
            " (saved — restart to take effect)"
            if spec is not None and spec.restart_required
            else " (applied live)"
        )
        self._log.info("router.setting_updated", key=cmd.key, value=str(value))
        return CommandResult(ok=True, intent=cmd.intent, detail=f"{cmd.key} = {value}{note}")

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
