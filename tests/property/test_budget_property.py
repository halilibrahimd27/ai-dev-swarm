"""Hypothesis property tests for :class:`DefaultTokenBudget`.

Invariants:
- ``can_spend(requested)`` is monotonic in ``requested``: if it's
  allowed at request size N, it's allowed at any size ≤ N.
- Once daily totals exceed the cap, no further spend is permitted
  for any milestone.
- Negative ``requested`` always raises ``ValueError``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from aidevswarm.settings import Settings
from aidevswarm.tools.budget import DefaultTokenBudget
from tests.unit.test_budget import FakeTokenLog

pytestmark = pytest.mark.property


def _settings(daily: int = 10_000, milestone: int = 5_000) -> Settings:
    return Settings(
        AIDEVSWARM_DAILY_TOKEN_BUDGET=daily,
        AIDEVSWARM_PER_MILESTONE_TOKEN_BUDGET=milestone,
    )


@given(requested=st.integers(min_value=-1000, max_value=-1))
def test_negative_requested_always_raises(requested: int) -> None:
    repo = FakeTokenLog()
    budget = DefaultTokenBudget(_settings(), repo)
    with pytest.raises(ValueError):
        budget.can_spend(milestone_id=None, requested=requested)


@given(
    daily=st.integers(min_value=1000, max_value=100_000),
    milestone=st.integers(min_value=500, max_value=50_000),
    requested=st.integers(min_value=0, max_value=200_000),
)
def test_can_spend_monotonic_in_requested(daily: int, milestone: int, requested: int) -> None:
    """If a big request is allowed, every smaller one is too."""
    assume(milestone <= daily)
    repo = FakeTokenLog()
    budget = DefaultTokenBudget(_settings(daily=daily, milestone=milestone), repo)
    if budget.can_spend(milestone_id=None, requested=requested):
        # any smaller request must also be allowed
        for smaller in (0, requested // 2, max(0, requested - 1)):
            assert budget.can_spend(milestone_id=None, requested=smaller)


@given(
    cap=st.integers(min_value=100, max_value=10_000),
    spent=st.integers(min_value=0, max_value=20_000),
)
def test_daily_cap_locks_out_when_exceeded(cap: int, spent: int) -> None:
    repo = FakeTokenLog()
    repo.record(
        project_id=None,
        milestone_id=None,
        role="r",
        model="m",
        input_tokens=spent,
        output_tokens=0,
        cost_usd=0.0,
    )
    budget = DefaultTokenBudget(_settings(daily=cap, milestone=10**9), repo)
    if spent > cap:
        # daily + requested > cap; even a zero request fails.
        assert budget.can_spend(milestone_id=None, requested=0) is False


@given(
    cap=st.integers(min_value=100, max_value=10_000),
    spent=st.integers(min_value=0, max_value=20_000),
    other_spent=st.integers(min_value=0, max_value=20_000),
)
def test_per_milestone_cap_does_not_leak_across_milestones(
    cap: int, spent: int, other_spent: int
) -> None:
    repo = FakeTokenLog()
    locked: UUID = uuid4()
    fresh: UUID = uuid4()
    repo.record(
        project_id=None,
        milestone_id=locked,
        role="r",
        model="m",
        input_tokens=spent,
        output_tokens=0,
        cost_usd=0.0,
    )
    repo.record(
        project_id=None,
        milestone_id=fresh,
        role="r",
        model="m",
        input_tokens=other_spent,
        output_tokens=0,
        cost_usd=0.0,
    )
    budget = DefaultTokenBudget(_settings(daily=10**9, milestone=cap), repo)
    if other_spent < cap:
        # An empty request is always feasible (0 doesn't add over cap).
        assert budget.can_spend(milestone_id=fresh, requested=0) is True
