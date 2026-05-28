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

# Fallback price table (USD per 1M tokens) keyed by a model-name
# substring. Only used when LiteLLM has no price entry for the model
# (e.g. a brand-new model id its price map hasn't caught up to). These
# are deliberately rough — the goal is *visibility*, not billing-grade
# accuracy. The SDK build calls report their own exact `total_cost_usd`.
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Best-effort USD cost for a completion. Never raises.

    Tries LiteLLM's price map first (authoritative for known models),
    then falls back to :data:`_PRICE_PER_MTOK`, then to ``0.0`` so a
    pricing gap never blocks recording the token counts.
    """
    try:
        from litellm import cost_per_token

        prompt_cost, completion_cost = cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return float(prompt_cost) + float(completion_cost)
    except Exception:
        m = model.lower()
        for key, (price_in, price_out) in _PRICE_PER_MTOK.items():
            if key in m:
                return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000
        return 0.0


class SpendRecorder:
    """Append one row to ``token_log`` per LLM call. Never raises.

    Recording spend must NEVER take the orchestrator down — a logging
    blip is not worth aborting a multi-day build. Every write is
    wrapped; failures are logged and swallowed. This is the data that
    makes ``DefaultTokenBudget`` (and the operator's "where did my
    money go?" question) answerable.
    """

    def __init__(self, repo: TokenLogRepo) -> None:
        self._repo = repo
        self._log = get_logger(__name__)

    def record(
        self,
        *,
        project_id: UUID | None,
        milestone_id: UUID | None,
        role: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float | None = None,
    ) -> None:
        cost = (
            cost_usd
            if cost_usd is not None
            else estimate_cost_usd(model, prompt_tokens, completion_tokens)
        )
        try:
            self._repo.record(
                project_id=project_id,
                milestone_id=milestone_id,
                role=role,
                model=model,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                cost_usd=cost,
            )
            self._log.info(
                "spend.recorded",
                role=role,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=round(cost, 4),
            )
        except Exception as exc:
            self._log.warning("spend.record_failed", role=role, error=str(exc))


class BudgetExceeded(RuntimeError):
    """Raised when a guard refuses additional spend."""


class UnlimitedTokenBudget:
    """A no-op :class:`aidevswarm.tools.protocols.TokenBudget`.

    Used as the default in :class:`~aidevswarm.orchestrator.tick.TickDeps`
    so tests (and any caller that doesn't care about throttling) get a
    guard that always allows and never records. Production wires the
    real :class:`DefaultTokenBudget`.
    """

    def can_spend(self, *, milestone_id: UUID | None, requested: int) -> bool:
        del milestone_id, requested
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
        del project_id, milestone_id, role, model, input_tokens, output_tokens, cost_usd


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
