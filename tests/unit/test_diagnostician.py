"""The self-healing Diagnostician.

The LLM kickoff (``_run``) is stubbed so these tests don't call CrewAI;
they pin the side-effects: a remediation steering note for the next
attempt + a boardroom decision, and the never-raises contract.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from aidevswarm.crews.diagnostician import Diagnostician
from aidevswarm.observability import TranscriptEntry
from aidevswarm.schemas import AcceptanceCriterion, Milestone, MilestoneSpec, Project, ProjectSpec
from aidevswarm.settings import Settings


class _Steering:
    def __init__(self) -> None:
        self.notes: list[tuple[UUID, str, str]] = []

    def add_note(self, project_id: UUID, body: str, *, author: str = "human") -> int:
        self.notes.append((project_id, body, author))
        return len(self.notes)

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        return []


class _Sink:
    def __init__(self) -> None:
        self.entries: list[TranscriptEntry] = []

    def publish(self, entry: TranscriptEntry) -> None:
        self.entries.append(entry)


def _project() -> Project:
    return Project(
        name="p",
        spec=ProjectSpec(
            title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
        ),
    )


def _milestone() -> Milestone:
    return Milestone(
        project_id=uuid4(),
        ordinal=0,
        title="Static client AST contract miner",
        spec=MilestoneSpec(
            title="m",
            description="d",
            acceptance_criteria=[AcceptanceCriterion(description="x", verifier="pytest")],
        ),
    )


def _diag(steering: _Steering, sink: _Sink, remediation: str | Exception) -> Diagnostician:
    d = Diagnostician(
        Settings(ANTHROPIC_API_KEY="sk-ant-x"), steering_repo=steering, transcript=sink
    )

    def _stub(_p: Any, _m: Any, _ctx: str) -> str:
        if isinstance(remediation, Exception):
            raise remediation
        return remediation

    d._run = _stub  # type: ignore[method-assign]
    return d


def test_diagnose_writes_steering_note_and_boardroom_decision() -> None:
    steering, sink = _Steering(), _Sink()
    d = _diag(steering, sink, "Remove the unused import `persist_call_sites` in validate_tests.py.")
    out = d.diagnose(_project(), _milestone(), "F401 imported but unused")
    assert out is not None
    # next attempt is steered
    assert len(steering.notes) == 1
    assert "Diagnostician" in steering.notes[0][1]
    assert "persist_call_sites" in steering.notes[0][1]
    assert steering.notes[0][2] == "diagnostician"
    # boardroom sees the reasoning
    assert len(sink.entries) == 1
    assert sink.entries[0].role == "Diagnostician"
    assert sink.entries[0].kind == "decision"


def test_diagnose_resilient_when_llm_raises() -> None:
    steering, sink = _Steering(), _Sink()
    d = _diag(steering, sink, RuntimeError("LLM down"))
    assert d.diagnose(_project(), _milestone(), "boom") is None
    assert steering.notes == []
    assert sink.entries == []


def test_diagnose_truncates_long_remediation() -> None:
    steering, sink = _Steering(), _Sink()
    d = _diag(steering, sink, "x" * 5000)
    out = d.diagnose(_project(), _milestone(), "boom")
    assert out is not None and len(out) <= 600


def test_diagnose_empty_remediation_is_noop() -> None:
    steering, sink = _Steering(), _Sink()
    d = _diag(steering, sink, "   ")  # whitespace-only → no usable remediation
    assert d.diagnose(_project(), _milestone(), "boom") is None
    assert steering.notes == []
    assert sink.entries == []
