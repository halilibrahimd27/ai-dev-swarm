"""Unit tests for :class:`AutoSplitPredictor`."""

from __future__ import annotations

from uuid import uuid4

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.schemas import (
    AcceptanceCriterion,
    Milestone,
    MilestoneSpec,
    Split,
)
from aidevswarm.settings import Settings
from tests.fakes import FakeMilestoneSessionRepo


def _settings(turns: int = 40, cost: float = 3.0) -> Settings:
    return Settings(
        AIDEVSWARM_AUTO_SPLIT_MAX_TURNS=turns,
        AIDEVSWARM_AUTO_SPLIT_MAX_COST_USD=cost,
    )


def _milestone(criteria_n: int = 2) -> Milestone:
    criteria = [
        AcceptanceCriterion(description=f"crit {i}", verifier="pytest") for i in range(criteria_n)
    ]
    return Milestone(
        project_id=uuid4(),
        ordinal=0,
        title="m",
        spec=MilestoneSpec(title="m", description="d", acceptance_criteria=criteria),
    )


def test_no_history_returns_none() -> None:
    repo = FakeMilestoneSessionRepo()
    predictor = AutoSplitPredictor(_settings(), repo)
    assert predictor.predict(_milestone()) is None


def test_under_thresholds_returns_none() -> None:
    repo = FakeMilestoneSessionRepo()
    m = _milestone()
    repo.record(milestone_id=m.id, role="Developer", session_id="s1", cost_usd=0.5, turns=10)
    predictor = AutoSplitPredictor(_settings(), repo)
    assert predictor.predict(m) is None


def test_too_many_turns_triggers_split() -> None:
    repo = FakeMilestoneSessionRepo()
    m = _milestone(criteria_n=4)
    repo.record(milestone_id=m.id, role="Developer", session_id="s1", cost_usd=0.5, turns=50)
    out = AutoSplitPredictor(_settings(turns=40), repo).predict(m)
    assert isinstance(out, Split)
    assert len(out.into) == 2
    # 4 criteria → 2+2 bisection.
    assert len(out.into[0].acceptance_criteria) == 2
    assert len(out.into[1].acceptance_criteria) == 2


def test_too_expensive_triggers_split() -> None:
    repo = FakeMilestoneSessionRepo()
    m = _milestone(criteria_n=3)
    repo.record(milestone_id=m.id, role="Tester", session_id="s2", cost_usd=4.5, turns=5)
    out = AutoSplitPredictor(_settings(cost=3.0), repo).predict(m)
    assert isinstance(out, Split)
    # 3 criteria → ((3+1)//2)=2, second half has 1.
    assert len(out.into[0].acceptance_criteria) == 2
    assert len(out.into[1].acceptance_criteria) == 1


def test_split_falls_back_to_description_when_few_criteria() -> None:
    repo = FakeMilestoneSessionRepo()
    m = _milestone(criteria_n=0)
    repo.record(milestone_id=m.id, role="Developer", session_id="s3", cost_usd=10.0, turns=5)
    out = AutoSplitPredictor(_settings(), repo).predict(m)
    assert isinstance(out, Split)
    assert len(out.into[0].acceptance_criteria) == 1
    assert "First half" in out.into[0].acceptance_criteria[0].description
    assert "Second half" in out.into[1].acceptance_criteria[0].description


def test_split_marks_children_with_auto_split_note() -> None:
    repo = FakeMilestoneSessionRepo()
    m = _milestone(criteria_n=2)
    repo.record(milestone_id=m.id, role="Developer", session_id="s4", cost_usd=99.0, turns=99)
    out = AutoSplitPredictor(_settings(), repo).predict(m)
    assert out is not None
    assert "[AUTO-SPLIT 1/2]" in out.into[0].technical_note
    assert "[AUTO-SPLIT 2/2]" in out.into[1].technical_note


def test_worst_role_wins() -> None:
    """If Developer is cheap but Tester is expensive, split still fires."""
    repo = FakeMilestoneSessionRepo()
    m = _milestone(criteria_n=4)
    repo.record(milestone_id=m.id, role="Developer", session_id="s1", cost_usd=0.1, turns=5)
    repo.record(milestone_id=m.id, role="Tester", session_id="s2", cost_usd=5.0, turns=10)
    out = AutoSplitPredictor(_settings(), repo).predict(m)
    assert isinstance(out, Split)
