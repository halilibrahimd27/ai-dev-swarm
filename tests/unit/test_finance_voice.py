"""The boardroom Finance/Cost voice.

Rule-based (no LLM) so it's deterministic + free; these tests pin the
thresholds and the publish wiring.
"""

from __future__ import annotations

from uuid import uuid4

from aidevswarm.crews.finance import FinanceVoice, _fmt_tokens
from aidevswarm.observability import DECISION_KIND, TranscriptEntry
from aidevswarm.schemas import Project, ProjectSpec
from aidevswarm.settings import Settings
from tests.fakes import InMemoryTokenLogRepo


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


def _settings() -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="sk-ant-x",
        AIDEVSWARM_PER_MILESTONE_TOKEN_BUDGET=100_000,
        AIDEVSWARM_DAILY_TOKEN_BUDGET=1_000_000,
    )


def test_fmt_tokens() -> None:
    assert _fmt_tokens(500) == "500"
    assert _fmt_tokens(1500) == "1.5k"
    assert _fmt_tokens(2_500_000) == "2.50M"


def test_on_plan_publishes_finance_decision() -> None:
    sink = _Sink()
    fv = FinanceVoice(_settings(), InMemoryTokenLogRepo(), sink)
    fv.on_plan(_project(), milestone_count=8)
    assert len(sink.entries) == 1
    e = sink.entries[0]
    assert e.role == "Finance"
    assert e.kind == DECISION_KIND
    assert "8 milestones" in e.text


def test_on_milestone_flags_hot_milestone() -> None:
    sink = _Sink()
    repo = InMemoryTokenLogRepo()
    mid = uuid4()
    # 90k of a 100k cap -> hot.
    repo.record(
        project_id=None,
        milestone_id=mid,
        role="Developer",
        model="m",
        input_tokens=90_000,
        output_tokens=0,
        cost_usd=1.0,
    )
    fv = FinanceVoice(_settings(), repo, sink)
    fv.on_milestone_done(_project(), mid, "Static client AST contract miner")
    assert len(sink.entries) == 1
    assert "ran hot" in sink.entries[0].text


def test_on_milestone_comfortable_when_cheap() -> None:
    sink = _Sink()
    repo = InMemoryTokenLogRepo()
    mid = uuid4()
    repo.record(
        project_id=None,
        milestone_id=mid,
        role="Developer",
        model="m",
        input_tokens=5_000,
        output_tokens=0,
        cost_usd=0.05,
    )
    fv = FinanceVoice(_settings(), repo, sink)
    fv.on_milestone_done(_project(), mid, "tiny milestone")
    assert "Comfortably within budget" in sink.entries[0].text


def test_finance_voice_no_publisher_is_safe() -> None:
    fv = FinanceVoice(_settings(), InMemoryTokenLogRepo(), None)
    fv.on_plan(_project(), 3)  # must not raise
