"""Replanner action schema.

The Phase 4 replanner state machine ends each replanning pass with
exactly one ``ReplannerAction``. Discriminated unions on Pydantic v2
give us:

  * Cheap type narrowing in the tick code (``match action.action``).
  * Round-trippable through ``model_dump``/``model_validate``, so
    actions can be persisted (Phase 5) or sent over the wire.
  * Type-safe field sets per variant — ``Noop`` has no fields,
    ``Split`` carries the children, etc.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from aidevswarm.schemas.milestone import MilestoneSpec


class Noop(BaseModel):
    """No change — advance to the next milestone unchanged."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["noop"] = "noop"


class Amend(BaseModel):
    """Patch the next milestone's spec in place.

    ``patch`` is intentionally untyped at the schema layer; the tick
    applies it via ``MilestoneSpec.model_copy(update=patch)`` which
    raises on unknown keys.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["amend"] = "amend"
    milestone_id: UUID
    patch: dict[str, Any]


class Split(BaseModel):
    """Replace one milestone with multiple smaller ones.

    The children inherit the parent's ``ordinal`` (the first child
    takes it; subsequent children get the next ordinals, all later
    milestones shift down).
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["split"] = "split"
    milestone_id: UUID
    into: list[MilestoneSpec] = Field(min_length=2)


class Escalate(BaseModel):
    """Park the project in ``blocked`` and notify the operator."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["escalate"] = "escalate"
    reason: str = Field(min_length=1)
    freeze: bool = True


ReplannerAction = Annotated[
    Union[Noop, Amend, Split, Escalate], Field(discriminator="action")
]
