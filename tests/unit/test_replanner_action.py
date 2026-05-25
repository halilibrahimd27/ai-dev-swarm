"""Unit tests for the :class:`ReplannerAction` discriminated union."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from aidevswarm.schemas import (
    AcceptanceCriterion,
    Amend,
    Escalate,
    MilestoneSpec,
    Noop,
    ReplannerAction,
    Split,
)

# Pydantic v2 typed adapter for the discriminated union.
_ADAPTER = TypeAdapter(ReplannerAction)


def _spec(title: str) -> MilestoneSpec:
    return MilestoneSpec(
        title=title,
        description="d",
        acceptance_criteria=[AcceptanceCriterion(description="x", verifier="pytest")],
    )


def test_noop_round_trip() -> None:
    action: ReplannerAction = _ADAPTER.validate_python({"action": "noop"})
    assert isinstance(action, Noop)
    assert _ADAPTER.dump_python(action) == {"action": "noop"}


def test_amend_round_trip() -> None:
    mid = uuid4()
    payload = {
        "action": "amend",
        "milestone_id": str(mid),
        "patch": {"description": "tighter"},
    }
    action = _ADAPTER.validate_python(payload)
    assert isinstance(action, Amend)
    assert action.milestone_id == mid
    assert action.patch == {"description": "tighter"}


def test_split_requires_at_least_two_children() -> None:
    mid = uuid4()
    payload = {
        "action": "split",
        "milestone_id": str(mid),
        "into": [_spec("only one").model_dump()],
    }
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload)


def test_split_with_two_children_validates() -> None:
    mid = uuid4()
    action = _ADAPTER.validate_python(
        {
            "action": "split",
            "milestone_id": str(mid),
            "into": [_spec("first").model_dump(), _spec("second").model_dump()],
        }
    )
    assert isinstance(action, Split)
    assert len(action.into) == 2
    assert action.into[0].title == "first"


def test_escalate_requires_reason() -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"action": "escalate", "reason": "", "freeze": True})


def test_escalate_defaults_freeze_true() -> None:
    action = _ADAPTER.validate_python({"action": "escalate", "reason": "out of budget"})
    assert isinstance(action, Escalate)
    assert action.freeze is True
    assert action.reason == "out of budget"


def test_unknown_action_rejected() -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"action": "rename"})


def test_extra_fields_rejected_on_every_variant() -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"action": "noop", "rogue": 1})


def test_amend_with_unknown_top_level_field_rejected() -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(
            {
                "action": "amend",
                "milestone_id": str(uuid4()),
                "patch": {"x": 1},
                "rogue": True,
            }
        )
