"""Unit tests for the milestone-sessions repository contract."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from aidevswarm.schemas import MilestoneSession
from tests.fakes import FakeMilestoneSessionRepo


def test_record_then_latest_for_returns_row() -> None:
    repo = FakeMilestoneSessionRepo()
    mid = uuid4()
    repo.record(
        milestone_id=mid,
        role="Developer",
        session_id="sess-1",
        cost_usd=0.12,
        turns=4,
    )
    latest = repo.latest_for(mid, "Developer")
    assert latest is not None
    assert latest.session_id == "sess-1"
    assert latest.cost_usd == 0.12
    assert latest.turns == 4


def test_latest_for_returns_most_recent() -> None:
    repo = FakeMilestoneSessionRepo()
    mid = uuid4()
    repo.record(milestone_id=mid, role="Developer", session_id="sess-1", cost_usd=0.1, turns=2)
    repo.record(milestone_id=mid, role="Developer", session_id="sess-2", cost_usd=0.2, turns=3)
    latest = repo.latest_for(mid, "Developer")
    assert latest is not None
    assert latest.session_id == "sess-2"


def test_latest_for_breaks_finished_at_tie_by_id() -> None:
    """On a finished_at TIE the NEWER row (higher id) wins — mirrors the real
    repo's `ORDER BY finished_at DESC, id DESC`. No time.sleep needed: the
    rows share an identical timestamp so only the id-tiebreaker decides."""
    repo = FakeMilestoneSessionRepo()
    mid = uuid4()
    same = datetime(2026, 1, 1, tzinfo=UTC)
    repo.rows.append(
        MilestoneSession(
            id=1,
            milestone_id=mid,
            role="Developer",
            session_id="old",
            cost_usd=0.1,
            turns=1,
            finished_at=same,
        )
    )
    repo.rows.append(
        MilestoneSession(
            id=2,
            milestone_id=mid,
            role="Developer",
            session_id="new",
            cost_usd=0.2,
            turns=2,
            finished_at=same,
        )
    )
    latest = repo.latest_for(mid, "Developer")
    assert latest is not None
    assert latest.session_id == "new"


def test_latest_for_filters_by_role() -> None:
    repo = FakeMilestoneSessionRepo()
    mid = uuid4()
    repo.record(milestone_id=mid, role="Developer", session_id="dev-1", cost_usd=0.1, turns=2)
    repo.record(milestone_id=mid, role="Tester", session_id="test-1", cost_usd=0.05, turns=1)
    assert repo.latest_for(mid, "Developer").session_id == "dev-1"  # type: ignore[union-attr]
    assert repo.latest_for(mid, "Tester").session_id == "test-1"  # type: ignore[union-attr]


def test_latest_for_returns_none_when_empty() -> None:
    repo = FakeMilestoneSessionRepo()
    assert repo.latest_for(uuid4(), "Developer") is None


def test_per_milestone_isolation() -> None:
    repo = FakeMilestoneSessionRepo()
    m1, m2 = uuid4(), uuid4()
    repo.record(milestone_id=m1, role="Developer", session_id="m1-dev", cost_usd=0.1, turns=2)
    repo.record(milestone_id=m2, role="Developer", session_id="m2-dev", cost_usd=0.1, turns=2)
    assert repo.latest_for(m1, "Developer").session_id == "m1-dev"  # type: ignore[union-attr]
    assert repo.latest_for(m2, "Developer").session_id == "m2-dev"  # type: ignore[union-attr]
