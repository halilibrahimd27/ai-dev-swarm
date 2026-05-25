"""Token / cost budget guards.

Two distinct guards:

- **Per-milestone sanity cap** — if one milestone burns more than
  ``per_milestone_token_budget`` tokens, the build is stuck in a loop;
  the orchestrator marks it failed and triggers a retry.
- **Daily throttle** — paces the system. When the day's accumulated
  spend crosses ``daily_token_budget``, the orchestrator pauses but
  finishes the in-flight milestone first.

Both guards delegate the actual ledger to a
:class:`aidevswarm.db.protocols.TokenLogRepo`.
"""

from __future__ import annotations

from uuid import UUID

from aidevswarm.db.protocols import TokenLogRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.settings import Settings


class BudgetExceeded(RuntimeError):
    """Raised when a guard refuses additional spend."""


class DefaultTokenBudget:
    """Concrete :class:`aidevswarm.tools.protocols.TokenBudget`.

    The "requested" arg to :meth:`can_spend` is the *expected* number of
    tokens a call will use; callers can over-estimate. The guard reads
    the running totals from the :class:`TokenLogRepo` each time.
    """

    def __init__(self, settings: Settings, repo: TokenLogRepo) -> None:
        self._settings = settings
        self._repo = repo
        self._log = get_logger(__name__)

    def can_spend(self, *, milestone_id: UUID | None, requested: int) -> bool:
        if requested < 0:
            raise ValueError("requested must be non-negative")

        daily = self._repo.daily_total_tokens()
        if daily + requested > self._settings.daily_token_budget:
            self._log.info(
                "budget.daily_blocked",
                daily=daily,
                requested=requested,
                cap=self._settings.daily_token_budget,
            )
            return False

        if milestone_id is not None:
            ms = self._repo.milestone_total_tokens(milestone_id)
            if ms + requested > self._settings.per_milestone_token_budget:
                self._log.info(
                    "budget.milestone_blocked",
                    milestone_id=str(milestone_id),
                    spent=ms,
                    requested=requested,
                    cap=self._settings.per_milestone_token_budget,
                )
                return False

        return True

    def record_spend(
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
        self._repo.record(
            project_id=project_id,
            milestone_id=milestone_id,
            role=role,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
