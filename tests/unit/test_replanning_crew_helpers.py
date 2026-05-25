"""Pure-function coverage for :mod:`aidevswarm.crews.replanning.crew`.

The full crew kickoff path goes through CrewAI and a live LLM, so it
is intentionally not exercised under unit tests. These tests cover
the pure helpers — prompt loading, steering pull, session summary,
and the defensive JSON parser — which is what guards the
tick-thread against a malformed LLM reply.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from aidevswarm.crews.replanning.crew import (
    CrewaiReplanningCrew,
    _summarise_sessions,
)
from aidevswarm.schemas import (
    Amend,
    Escalate,
    MilestoneSession,
    Noop,
    Split,
)
from aidevswarm.settings import Settings


class _StubSteeringRepo:
    def __init__(self, notes: dict[tuple[UUID, str], list[str]]) -> None:
        self._notes = notes

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        return list(self._notes.get((project_id, role), []))


def _settings() -> Settings:
    return Settings(AIDEVSWARM_REQUIRE_APPROVAL=False)


def test_constructor_loads_both_prompt_templates() -> None:
    crew = CrewaiReplanningCrew(_settings())
    # Both Architect/PM prompt templates must be present + non-empty;
    # the {{ steering_notes }} slot is what makes the prompt safe to
    # render even with no notes.
    assert "{{ steering_notes" in crew._architect_template
    assert "{{ steering_notes" in crew._pm_template


def test_pull_returns_empty_when_no_steering_repo_attached() -> None:
    crew = CrewaiReplanningCrew(_settings(), steering_repo=None)
    assert crew._pull(uuid4(), "Architect") == []


def test_pull_delegates_to_attached_steering_repo() -> None:
    project_id = uuid4()
    steering = _StubSteeringRepo({(project_id, "PM"): ["focus on retries"]})
    crew = CrewaiReplanningCrew(_settings(), steering_repo=steering)
    assert crew._pull(project_id, "PM") == ["focus on retries"]
    assert crew._pull(project_id, "Architect") == []


def test_summarise_sessions_handles_empty_input() -> None:
    assert "none yet" in _summarise_sessions([])


def test_summarise_sessions_caps_at_six_entries() -> None:
    sessions = [
        MilestoneSession(
            milestone_id=uuid4(),
            role="Developer",
            session_id=f"sess-{i:02d}-abcdef",
            cost_usd=0.1 * i,
            turns=i,
        )
        for i in range(10)
    ]
    summary = _summarise_sessions(sessions)
    lines = [ln for ln in summary.splitlines() if ln.strip()]
    assert len(lines) == 6
    # tail-of-list: keeps last six (indices 4..9), drops the first four.
    assert "sess-03" not in summary
    assert "sess-04" in summary
    assert "sess-09" in summary


def test_parse_recovers_noop_from_garbage() -> None:
    """A malformed LLM reply must NEVER take the tick down."""

    class _RawHolder:
        raw = '{"action":"bogus","whatever":42}'

    assert isinstance(CrewaiReplanningCrew._parse(_RawHolder()), Noop)


def test_parse_lets_json_decode_errors_bubble() -> None:
    """Invalid JSON is a hard error — only schema mismatches Noop-recover."""

    class _RawHolder:
        raw: str = "not even json {{"

    with pytest.raises(json.JSONDecodeError):
        CrewaiReplanningCrew._parse(_RawHolder())


def test_parse_dispatches_to_each_action_variant() -> None:
    milestone_id = uuid4()

    class _Raw:
        def __init__(self, payload: str) -> None:
            self.raw = payload

    noop = CrewaiReplanningCrew._parse(_Raw('{"action":"noop"}'))
    assert isinstance(noop, Noop)

    amend = CrewaiReplanningCrew._parse(
        _Raw(
            '{"action":"amend","milestone_id":"'
            + str(milestone_id)
            + '","patch":{"description":"tighter scope"}}'
        )
    )
    assert isinstance(amend, Amend)
    assert amend.milestone_id == milestone_id

    split = CrewaiReplanningCrew._parse(
        _Raw(
            '{"action":"split","milestone_id":"' + str(milestone_id) + '","into":['
            '{"title":"a","description":"d","acceptance_criteria":['
            '{"description":"x","verifier":"pytest"}]},'
            '{"title":"b","description":"d","acceptance_criteria":['
            '{"description":"y","verifier":"pytest"}]}]}'
        )
    )
    assert isinstance(split, Split)
    assert len(split.into) == 2

    escalate = CrewaiReplanningCrew._parse(_Raw('{"action":"escalate","reason":"stuck"}'))
    assert isinstance(escalate, Escalate)
    assert escalate.reason == "stuck"


def test_parse_accepts_raw_dict_payload() -> None:
    """Some CrewAI versions hand back a dict on .raw instead of a JSON string."""

    class _Raw:
        def __init__(self) -> None:
            self.raw: dict[str, str] = {"action": "noop"}

    assert isinstance(CrewaiReplanningCrew._parse(_Raw()), Noop)
