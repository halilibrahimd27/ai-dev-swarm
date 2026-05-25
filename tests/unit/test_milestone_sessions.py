"""Unit tests for the milestone-sessions repository contract."""

from __future__ import annotations

import time
from uuid import uuid4

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
    time.sleep(0.01)
    repo.record(milestone_id=mid, role="Developer", session_id="sess-2", cost_usd=0.2, turns=3)
    latest = repo.latest_for(mid, "Developer")
    assert latest is not None
    assert latest.session_id == "sess-2"


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
