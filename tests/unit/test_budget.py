"""Unit tests for the token budget guard.

The guard delegates the ledger to a :class:`TokenLogRepo`; we drive it
with an in-memory fake so behaviour is deterministic and Postgres-free.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from aidevswarm.settings import Settings
from aidevswarm.tools.budget import (
    DefaultTokenBudget,
    SpendRecorder,
    UnlimitedTokenBudget,
    estimate_cost_usd,
)


class FakeTokenLog:
    def __init__(self) -> None:
        self._records: list[dict[str, object]] = []

    def record(
        self,
        *,
        project_id: UUID | None,
        milestone_id: UUID | None,
        role: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        self._records.append(
            {
                "project_id": project_id,
                "milestone_id": milestone_id,
                "role": role,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
            }
        )

    def daily_total_tokens(self) -> int:
        return sum(int(r["input_tokens"]) + int(r["output_tokens"]) for r in self._records)

    def milestone_total_tokens(self, milestone_id: UUID) -> int:
        return sum(
            int(r["input_tokens"]) + int(r["output_tokens"])
            for r in self._records
            if r["milestone_id"] == milestone_id
        )

    @property
    def records(self) -> list[dict[str, object]]:
        return list(self._records)


def _settings(daily: int = 1000, milestone: int = 500) -> Settings:
    return Settings(
        AIDEVSWARM_DAILY_TOKEN_BUDGET=daily,
        AIDEVSWARM_PER_MILESTONE_TOKEN_BUDGET=milestone,
    )


def test_can_spend_returns_true_when_under_both_caps() -> None:
    repo = FakeTokenLog()
    budget = DefaultTokenBudget(_settings(), repo)
    assert budget.can_spend(milestone_id=None, requested=100) is True


def test_daily_cap_blocks_spend() -> None:
    repo = FakeTokenLog()
    budget = DefaultTokenBudget(_settings(daily=200, milestone=10_000), repo)
    repo.record(
        project_id=None,
        milestone_id=None,
        role="ideation",
        model="claude-haiku-4-5",
        input_tokens=150,
        output_tokens=0,
        cost_usd=0.0,
    )
    assert budget.can_spend(milestone_id=None, requested=100) is False


def test_per_milestone_cap_blocks_spend() -> None:
    repo = FakeTokenLog()
    budget = DefaultTokenBudget(_settings(daily=10_000, milestone=200), repo)
    ms = uuid4()
    repo.record(
        project_id=None,
        milestone_id=ms,
        role="build",
        model="claude-opus-4-7",
        input_tokens=150,
        output_tokens=0,
        cost_usd=0.0,
    )
    assert budget.can_spend(milestone_id=ms, requested=100) is False


def test_per_milestone_cap_does_not_block_other_milestones() -> None:
    repo = FakeTokenLog()
    budget = DefaultTokenBudget(_settings(daily=10_000, milestone=200), repo)
    spent_ms, fresh_ms = uuid4(), uuid4()
    repo.record(
        project_id=None,
        milestone_id=spent_ms,
        role="build",
        model="claude-opus-4-7",
        input_tokens=180,
        output_tokens=0,
        cost_usd=0.0,
    )
    assert budget.can_spend(milestone_id=fresh_ms, requested=100) is True


def test_record_spend_appends_to_repo() -> None:
    repo = FakeTokenLog()
    budget = DefaultTokenBudget(_settings(), repo)
    budget.record_spend(
        project_id=None,
        milestone_id=None,
        role="planning",
        model="claude-opus-4-7",
        input_tokens=10,
        output_tokens=20,
        cost_usd=0.001,
    )
    assert len(repo.records) == 1
    assert repo.records[0]["role"] == "planning"


def test_negative_requested_rejected() -> None:
    repo = FakeTokenLog()
    budget = DefaultTokenBudget(_settings(), repo)
    with pytest.raises(ValueError):
        budget.can_spend(milestone_id=None, requested=-1)


# ---------------------------------------------------------------------------
# SpendRecorder + estimate_cost_usd + UnlimitedTokenBudget
# ---------------------------------------------------------------------------


def test_estimate_cost_usd_falls_back_to_price_table_for_unknown_model() -> None:
    # A made-up "opus" id LiteLLM won't price -> our fallback table fires.
    cost = estimate_cost_usd(
        "vendor/made-up-opus-zzz", prompt_tokens=1_000_000, completion_tokens=0
    )
    assert cost == pytest.approx(15.0, rel=0.01)


def test_estimate_cost_usd_zero_for_unrecognised_model() -> None:
    assert estimate_cost_usd("totally-unknown-xyz", 1000, 1000) == 0.0


def test_spend_recorder_writes_a_row_with_estimated_cost() -> None:
    repo = FakeTokenLog()
    recorder = SpendRecorder(repo)
    recorder.record(
        project_id=None,
        milestone_id=None,
        role="ideation",
        model="anthropic/claude-haiku-4-5",
        prompt_tokens=1_000_000,
        completion_tokens=0,
    )
    assert len(repo.records) == 1
    row = repo.records[0]
    assert row["role"] == "ideation"
    assert row["input_tokens"] == 1_000_000
    # Haiku input ~ $1 / Mtok via the fallback table (or LiteLLM).
    assert float(row["cost_usd"]) > 0.0


def test_spend_recorder_uses_explicit_cost_when_given() -> None:
    repo = FakeTokenLog()
    recorder = SpendRecorder(repo)
    recorder.record(
        project_id=None,
        milestone_id=None,
        role="Developer",
        model="anthropic/claude-opus-4-7",
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.4242,
    )
    assert repo.records[0]["cost_usd"] == 0.4242


def test_spend_recorder_never_raises_on_repo_failure() -> None:
    class Boom:
        def record(self, **_: object) -> None:
            raise RuntimeError("db down")

    recorder = SpendRecorder(Boom())  # type: ignore[arg-type]
    # Must swallow the error — recording is best-effort.
    recorder.record(
        project_id=None,
        milestone_id=None,
        role="x",
        model="m",
        prompt_tokens=1,
        completion_tokens=1,
        cost_usd=0.0,
    )


def test_unlimited_budget_always_allows_and_records_nothing() -> None:
    budget = UnlimitedTokenBudget()
    assert budget.can_spend(milestone_id=None, requested=10**9) is True
    assert budget.can_spend(milestone_id=uuid4(), requested=10**9) is True
    # record_spend is a no-op that must not raise.
    budget.record_spend(
        project_id=None,
        milestone_id=None,
        role="x",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
    )
