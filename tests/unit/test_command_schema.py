"""Round-trip + discriminator tests for the Phase 5 Command bus.

A single ``TypeAdapter[Command]`` is the contract both the FastAPI
``/api/commands`` endpoint and the Telegram Haiku intent parser must
satisfy. These tests pin down:

  * Every vocabulary item parses correctly when given a well-formed
    payload + the right ``intent`` discriminator.
  * Unknown intents raise (the Haiku parser must not be able to
    smuggle a "delete the whole DB" intent past the schema).
  * Every destructive intent defaults ``confirmed`` to False, so the
    bot's confirmation flow is REQUIRED by construction.
  * ``requires_confirmation`` is the canonical predicate.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from aidevswarm.schemas import (
    DESTRUCTIVE_INTENTS,
    AbortProject,
    Approve,
    Command,
    DropAndStartNew,
    InjectNote,
    KillSwitch,
    ListState,
    PauseProject,
    RejectIdea,
    Rescope,
    ResumeProject,
    ShowTranscript,
    SwitchToIdea,
    TransformProject,
    requires_confirmation,
)

_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)


def _id() -> str:
    return str(uuid4())


@pytest.mark.parametrize(
    ("payload", "expected_cls"),
    [
        ({"intent": "approve", "project_id": _id()}, Approve),
        (
            {"intent": "inject_note", "project_id": _id(), "body": "tighter scope"},
            InjectNote,
        ),
        ({"intent": "list_state"}, ListState),
        ({"intent": "list_state", "project_id": _id()}, ListState),
        ({"intent": "show_transcript", "project_id": _id()}, ShowTranscript),
        ({"intent": "show_transcript", "project_id": _id(), "limit": 200}, ShowTranscript),
        ({"intent": "pause_project", "project_id": _id()}, PauseProject),
        ({"intent": "resume_project", "project_id": _id()}, ResumeProject),
        ({"intent": "abort_project", "project_id": _id()}, AbortProject),
        ({"intent": "rescope", "project_id": _id(), "new_scope": "make it tiny"}, Rescope),
        (
            {"intent": "transform_project", "project_id": _id(), "new_direction": "data tool"},
            TransformProject,
        ),
        ({"intent": "drop_and_start_new", "project_id": _id()}, DropAndStartNew),
        (
            {
                "intent": "switch_to_idea",
                "current_project_id": _id(),
                "new_idea_id": _id(),
            },
            SwitchToIdea,
        ),
        ({"intent": "reject_idea", "idea_id": _id()}, RejectIdea),
        ({"intent": "kill_switch"}, KillSwitch),
    ],
)
def test_every_intent_parses_to_the_right_variant(
    payload: dict[str, object], expected_cls: type
) -> None:
    parsed = _ADAPTER.validate_python(payload)
    assert isinstance(parsed, expected_cls)


def test_unknown_intent_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"intent": "delete_everything", "scope": "all"})


def test_missing_intent_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"project_id": _id()})


def test_inject_note_requires_nonempty_body() -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"intent": "inject_note", "project_id": _id(), "body": ""})


def test_show_transcript_limit_bounds() -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"intent": "show_transcript", "project_id": _id(), "limit": 9999})


def test_destructive_intents_default_to_unconfirmed() -> None:
    """The bot REQUIRES a [Yes][No] step before acting — enforce here."""
    for intent in DESTRUCTIVE_INTENTS:
        payload: dict[str, object] = {"intent": intent}
        # supply the required fields for each
        if intent == "abort_project":
            payload["project_id"] = _id()
        elif intent == "rescope":
            payload["project_id"] = _id()
            payload["new_scope"] = "x"
        elif intent == "transform_project":
            payload["project_id"] = _id()
            payload["new_direction"] = "x"
        elif intent == "drop_and_start_new":
            payload["project_id"] = _id()
        elif intent == "switch_to_idea":
            payload["current_project_id"] = _id()
            payload["new_idea_id"] = _id()
        elif intent == "reject_idea":
            payload["idea_id"] = _id()
        # kill_switch needs nothing
        parsed = _ADAPTER.validate_python(payload)
        assert parsed.confirmed is False  # type: ignore[union-attr]
        assert requires_confirmation(parsed) is True


def test_non_destructive_intents_never_require_confirmation() -> None:
    safe = [
        Approve(project_id=uuid4()),
        InjectNote(project_id=uuid4(), body="ok"),
        ListState(),
        ShowTranscript(project_id=uuid4()),
        PauseProject(project_id=uuid4()),
        ResumeProject(project_id=uuid4()),
    ]
    for cmd in safe:
        assert requires_confirmation(cmd) is False


def test_confirmed_destructive_intent_no_longer_blocks() -> None:
    cmd = AbortProject(project_id=uuid4(), confirmed=True)
    assert requires_confirmation(cmd) is False


def test_extra_fields_are_rejected() -> None:
    """extra='forbid' protects against silently dropped fields."""
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"intent": "approve", "project_id": _id(), "force": True})


def test_round_trip_through_model_dump() -> None:
    original = InjectNote(project_id=uuid4(), body="hello", role="PM")
    serialised = original.model_dump(mode="json")
    parsed = _ADAPTER.validate_python(serialised)
    assert isinstance(parsed, InjectNote)
    assert parsed == original
