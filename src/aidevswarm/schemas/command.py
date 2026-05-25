"""Operator command schema — shared by web UI and Telegram bot.

Every action the human can take on a project funnels through ONE
typed :class:`Command` (a Pydantic discriminated union keyed by
``intent``). Phase 5 wires two surfaces to this:

  * the FastAPI ``/api/commands`` endpoint (web panel)
  * the Telegram bot's free-text Haiku intent parser

Both produce the same ``Command`` objects, dispatched by
``orchestrator.command_router.CommandRouter`` — so the business
logic lives in exactly one place.

Destructive variants carry a ``confirmed: bool = False`` flag. The
Telegram bot won't execute them until a separate ``[Yes][No]``
inline-keyboard callback flips it to True; the web UI does the same
through a confirm-dialog. Non-destructive variants
(``approve``, ``inject_note``, read-only queries) skip the
confirmation step entirely.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Shared Pydantic config — strict by default."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Non-destructive (execute immediately)
# ---------------------------------------------------------------------------


class Approve(_Base):
    """Move a project past ``awaiting_approval`` into ``building``."""

    intent: Literal["approve"] = "approve"
    project_id: UUID


class InjectNote(_Base):
    """Fire-and-forget steering note for a project + (optional) role.

    Written to ``steering_notes`` via ``SteeringRepo.add_note``; the
    next agent step for that role/project picks it up via the
    ``{{ steering_notes }}`` prompt slot. Never blocks.
    """

    intent: Literal["inject_note"] = "inject_note"
    project_id: UUID
    body: str = Field(min_length=1)
    role: str | None = None  # None == note visible to all roles
    author: str = "human"


class ListState(_Base):
    """Read-only: project + milestone state machine snapshot."""

    intent: Literal["list_state"] = "list_state"
    project_id: UUID | None = None  # None == all active projects


class ShowTranscript(_Base):
    """Read-only: tail of the live transcript for one project."""

    intent: Literal["show_transcript"] = "show_transcript"
    project_id: UUID
    limit: int = Field(default=50, ge=1, le=500)


# ---------------------------------------------------------------------------
# Lifecycle (non-destructive but visible)
# ---------------------------------------------------------------------------


class PauseProject(_Base):
    """Stop the scheduler from advancing this project; reversible."""

    intent: Literal["pause_project"] = "pause_project"
    project_id: UUID


class ResumeProject(_Base):
    """Inverse of pause_project."""

    intent: Literal["resume_project"] = "resume_project"
    project_id: UUID


# ---------------------------------------------------------------------------
# Destructive (require [Yes][No] confirmation)
# ---------------------------------------------------------------------------


class AbortProject(_Base):
    """Trip the per-project kill switch."""

    intent: Literal["abort_project"] = "abort_project"
    project_id: UUID
    reason: str = Field(default="operator abort", min_length=1)
    confirmed: bool = False


class Rescope(_Base):
    """Change the scope of the current project's spec; triggers a replan."""

    intent: Literal["rescope"] = "rescope"
    project_id: UUID
    new_scope: str = Field(min_length=1)
    confirmed: bool = False


class TransformProject(_Base):
    """Repurpose the project into something else; rescope + replan."""

    intent: Literal["transform_project"] = "transform_project"
    project_id: UUID
    new_direction: str = Field(min_length=1)
    confirmed: bool = False


class DropAndStartNew(_Base):
    """Abort the current project and request a fresh ideation pass."""

    intent: Literal["drop_and_start_new"] = "drop_and_start_new"
    project_id: UUID
    confirmed: bool = False


class SwitchToIdea(_Base):
    """Abort current, then start a specific backlogged idea by ID."""

    intent: Literal["switch_to_idea"] = "switch_to_idea"
    current_project_id: UUID
    new_idea_id: UUID
    confirmed: bool = False


class RejectIdea(_Base):
    """Discard a proposed idea, force the ideation crew to try again."""

    intent: Literal["reject_idea"] = "reject_idea"
    idea_id: UUID
    confirmed: bool = False


class KillSwitch(_Base):
    """Global emergency stop — trip the orchestrator-wide kill switch."""

    intent: Literal["kill_switch"] = "kill_switch"
    reason: str = Field(default="operator kill switch", min_length=1)
    confirmed: bool = False


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------


Command = Annotated[
    Approve
    | InjectNote
    | ListState
    | ShowTranscript
    | PauseProject
    | ResumeProject
    | AbortProject
    | Rescope
    | TransformProject
    | DropAndStartNew
    | SwitchToIdea
    | RejectIdea
    | KillSwitch,
    Field(discriminator="intent"),
]


DESTRUCTIVE_INTENTS: frozenset[str] = frozenset(
    {
        "abort_project",
        "rescope",
        "transform_project",
        "drop_and_start_new",
        "switch_to_idea",
        "reject_idea",
        "kill_switch",
    }
)


def requires_confirmation(command: BaseModel) -> bool:
    """True iff the command is destructive AND not yet confirmed."""
    intent = getattr(command, "intent", None)
    if intent not in DESTRUCTIVE_INTENTS:
        return False
    return not getattr(command, "confirmed", False)
