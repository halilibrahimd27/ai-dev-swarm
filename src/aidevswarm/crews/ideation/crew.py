"""CrewAI-backed Ideation crew.

Phase 0 wires up the agents and tasks but does not enforce token budgets
or call into pgvector dedup — that orchestration belongs to the caller.

The CrewAI imports are deferred to ``__init__`` so unit tests that
substitute a fake never pay the cost of importing CrewAI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aidevswarm.crews._parsing import keep_known, loads_lenient
from aidevswarm.crews._prompts import load_prompt
from aidevswarm.crews._spend import record_crew_spend
from aidevswarm.crews.ideation.novelty import NoveltyChecker, SelfHistoryDedup
from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import CriticScores, Idea, ScoredIdea
from aidevswarm.settings import Settings
from aidevswarm.tools import SpendRecorder

_CREW_DIR = Path(__file__).resolve().parent


class CrewaiIdeationCrew:
    """Concrete :class:`aidevswarm.crews.protocols.IdeationCrew`."""

    def __init__(
        self,
        settings: Settings,
        *,
        novelty_checker: NoveltyChecker | None = None,
        self_dedup: SelfHistoryDedup | None = None,
        recorder: SpendRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._log = get_logger(__name__)
        self._novelty = novelty_checker
        self._self_dedup = self_dedup
        self._recorder = recorder
        # Ideation runs BEFORE there's a project, so there's no
        # project-scoped SteeringRepo to pull from. The {{ steering_notes }}
        # slot in each template is rendered to an empty string here.
        from aidevswarm.steering import render_prompt

        self._scout_prompt = render_prompt(load_prompt(_CREW_DIR, "trend_scout"), steering_notes=[])
        self._ideator_prompt = render_prompt(load_prompt(_CREW_DIR, "ideator"), steering_notes=[])
        self._critic_prompt = render_prompt(load_prompt(_CREW_DIR, "critic"), steering_notes=[])
        # CrewAI's Agent(llm=...) eagerly contacts the LLM provider, so
        # the actual crew is constructed lazily on the first run() call
        # to let the orchestrator boot even before an API key is set.
        self._crew: Any | None = None

    def _build_crew(self) -> Any:
        from crewai import Agent, Crew, Process, Task  # local import

        from aidevswarm.crews._llm import make_llm

        max_out = self._settings.max_output_tokens
        fast_llm = make_llm(self._settings.model_fast, max_out)
        strong_llm = make_llm(self._settings.model_strong, max_out)

        scout = Agent(
            role="Trend Scout",
            goal="Find deep, niche, multi-day problem spaces.",
            backstory=self._scout_prompt,
            llm=fast_llm,
            verbose=False,
            allow_delegation=False,
        )
        ideator = Agent(
            role="Ideator",
            goal="Turn problem spaces into concrete senior-level projects.",
            backstory=self._ideator_prompt,
            llm=strong_llm,
            verbose=False,
            allow_delegation=False,
        )
        critic = Agent(
            role="Critic",
            goal="Score and gate ideas against the depth rubric.",
            backstory=self._critic_prompt,
            llm=strong_llm,
            verbose=False,
            allow_delegation=False,
        )

        scout_task = Task(
            description="Produce three candidate problem spaces.",
            expected_output="Three short briefs.",
            agent=scout,
        )
        ideator_task = Task(
            description=(
                "Propose ONE project per candidate space. Output a JSON list "
                "where each entry has fields: title (string), summary (string), "
                "rationale (string), stack (list[string]), tags (list[string])."
            ),
            expected_output=(
                'JSON list, e.g. [{"title":"...","summary":"...",'
                '"rationale":"...","stack":["python"],"tags":["cli"]}, ...]'
            ),
            agent=ideator,
        )
        critic_task = Task(
            description=(
                "Score every idea per the rubric. Output JSON ONLY, no prose. "
                "The OUTER value is a JSON list. Each entry MUST have:\n"
                '  - "idea": the FULL Idea object (title/summary/rationale/'
                "stack/tags) — NOT a bare title string\n"
                '  - "scores": {"depth_ambition": int 0-100, "usefulness_niche": '
                'int, "novelty": int, "decomposability": int, "buildability": int}\n'
                '  - "total": int 0-100 (weighted score)\n'
                '  - "rejected_reason": string OR null'
            ),
            expected_output=(
                'A JSON list, e.g. [{"idea":{"title":"...","summary":"...",'
                '"rationale":"...","stack":["python"],"tags":["x"]},'
                '"scores":{"depth_ambition":85,"usefulness_niche":80,'
                '"novelty":75,"decomposability":85,"buildability":80},'
                '"total":81,"rejected_reason":null}]'
            ),
            agent=critic,
        )

        return Crew(
            agents=[scout, ideator, critic],
            tasks=[scout_task, ideator_task, critic_task],
            process=Process.sequential,
            verbose=False,
        )

    def run(self) -> list[ScoredIdea]:
        """Execute the crew and parse the Critic's output into ScoredIdea."""
        if self._crew is None:
            self._crew = self._build_crew()
        result = self._crew.kickoff()
        record_crew_spend(
            self._recorder,
            result,
            project_id=None,
            milestone_id=None,
            role="ideation",
            model=self._settings.model_strong,
        )
        parsed = self._parse(result)
        if self._novelty is not None:
            parsed = [self._apply_novelty(s) for s in parsed]
        if self._self_dedup is not None:
            parsed = [self._apply_self_dedup(s) for s in parsed]
        self._log.info("ideation.done", count=len(parsed))
        return parsed

    def _apply_novelty(self, scored: ScoredIdea) -> ScoredIdea:
        """Re-grade the Critic's verdict against the novelty check.

        If the prior-art search finds something too close, force the
        ``ScoredIdea`` below the 80-point gate by flipping the
        ``novelty`` sub-score and writing a ``rejected_reason``.
        """
        if self._novelty is None:
            return scored
        report = self._novelty.check(scored.idea)
        if report.is_novel:
            return scored
        match_summary = ", ".join(m.title for m in report.top_matches[:3])
        return scored.model_copy(
            update={
                "scores": scored.scores.model_copy(update={"novelty": 0}),
                "total": min(scored.total, 50),
                "rejected_reason": (
                    f"low novelty (score={report.score:.2f}); matches: {match_summary}"
                ),
            }
        )

    def _apply_self_dedup(self, scored: ScoredIdea) -> ScoredIdea:
        """Reject ideas that duplicate one of the swarm's OWN projects.

        ARCHITECTURE §5.7: never re-pitch a project we already built.
        Mirrors :meth:`_apply_novelty` — flips ``novelty`` to 0 and caps
        ``total`` below the gate with a clear reason.
        """
        if self._self_dedup is None:
            return scored
        dup = self._self_dedup.find_duplicate(scored.idea)
        if dup is None:
            return scored
        return scored.model_copy(
            update={
                "scores": scored.scores.model_copy(update={"novelty": 0}),
                "total": min(scored.total, 50),
                "rejected_reason": (
                    f"duplicate of own project {dup.title!r} (similarity={dup.similarity:.2f})"
                ),
            }
        )

    @staticmethod
    def _parse(crew_output: Any) -> list[ScoredIdea]:
        """Be liberal in what we accept from CrewAI's loose output type.

        Malformed entries (e.g. ``idea`` returned as a bare title string
        instead of the full Idea dict) are SKIPPED with a warning, not
        a crash — one bad entry must not take down the whole ideation
        pass.
        """
        raw = getattr(crew_output, "raw", crew_output)
        data = loads_lenient(raw)
        if not isinstance(data, list):
            raise ValueError("Critic did not return a JSON list")
        out: list[ScoredIdea] = []
        log = get_logger(__name__)
        for entry in data:
            try:
                out.append(
                    ScoredIdea(
                        idea=Idea.model_validate(keep_known(Idea, entry["idea"])),
                        scores=CriticScores.model_validate(
                            keep_known(CriticScores, entry["scores"])
                        ),
                        total=int(entry["total"]),
                        rejected_reason=entry.get("rejected_reason"),
                    )
                )
            except Exception as exc:
                log.warning(
                    "ideation.parse.skip_entry",
                    error=str(exc),
                    entry=str(entry)[:200],
                )
        return out
