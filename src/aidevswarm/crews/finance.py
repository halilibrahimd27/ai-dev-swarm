"""The Finance / Cost voice in the boardroom.

A lightweight, *rule-based* commentator (no LLM call — zero added spend or
latency) that turns the numbers the system already tracks into a plain-
English cost opinion and publishes it as a ``role="Finance"`` boardroom
decision. It comments at the two points an operator cares about money:

  * after planning — how big the committed scope is, and
  * after each milestone — what it cost against its sanity cap and how the
    day's budget is pacing, flagging when a milestone ran hot or the daily
    budget is nearly spent.

Deterministic + free by design: the operator asked for cost *visibility and
waste-cutting*, not another billable agent. It reads :class:`TokenLogRepo`
(the same ledger the budget guard uses) so its numbers always match Spend.
"""

from __future__ import annotations

from aidevswarm.db.protocols import TokenLogRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.observability import TranscriptPublisher, publish_decision
from aidevswarm.schemas import Project
from aidevswarm.settings import Settings


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


class FinanceVoice:
    """Publishes cost opinions to the boardroom. Never raises."""

    def __init__(
        self,
        settings: Settings,
        token_repo: TokenLogRepo,
        transcript: TranscriptPublisher | None,
    ) -> None:
        self._settings = settings
        self._token_repo = token_repo
        self._transcript = transcript
        self._log = get_logger(__name__)

    def on_plan(self, project: Project, milestone_count: int) -> None:
        """Comment on the committed scope right after planning."""
        budget = self._settings.daily_token_budget
        self._say(
            project,
            f"Plan approved at {milestone_count} milestones. Daily budget is "
            f"{_fmt_tokens(budget)} tokens; this will span "
            f"{'several days' if milestone_count > 5 else 'a day or two'} of build at "
            f"the current pace. I'll flag any milestone that runs hot.",
        )

    def on_milestone_done(self, project: Project, milestone_id: object, title: str) -> None:
        """Comment on what the just-finished milestone cost vs the caps."""
        try:
            m_tokens = self._token_repo.milestone_total_tokens(milestone_id)  # type: ignore[arg-type]
            day_tokens = self._token_repo.daily_total_tokens()
            day_cost = self._token_repo.daily_cost_usd()
        except Exception as exc:  # ledger hiccup must not break the tick
            self._log.warning("finance.read_failed", error=str(exc))
            return
        cap = self._settings.per_milestone_token_budget
        budget = self._settings.daily_token_budget
        m_pct = (m_tokens / cap) if cap > 0 else 0.0
        d_pct = (day_tokens / budget) if budget > 0 else 0.0
        verdict = self._verdict(m_pct, d_pct)
        self._say(
            project,
            f"'{title}' cost {_fmt_tokens(m_tokens)} tokens "
            f"({m_pct:.0%} of its {_fmt_tokens(cap)} cap). Today: "
            f"{_fmt_tokens(day_tokens)}/{_fmt_tokens(budget)} tokens (~${day_cost:.2f}). "
            f"{verdict}",
        )

    @staticmethod
    def _verdict(milestone_pct: float, daily_pct: float) -> str:
        if milestone_pct >= 0.8:
            return "⚠ This milestone ran hot — consider tighter scope or a split next time."
        if daily_pct >= 0.8:
            return "⚠ Over 80% of today's budget spent; the build will pause soon to pace."
        if daily_pct >= 0.5:
            return "Halfway through today's budget — on track but watch the pace."
        return "Comfortably within budget."

    def _say(self, project: Project, text: str) -> None:
        publish_decision(
            self._transcript,
            project_id=project.id,
            role="Finance",
            text=text,
        )


__all__ = ["FinanceVoice"]
