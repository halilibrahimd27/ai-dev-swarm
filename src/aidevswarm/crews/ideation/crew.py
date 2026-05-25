"""CrewAI-backed Ideation crew.

Phase 0 wires up the agents and tasks but does not enforce token budgets
or call into pgvector dedup — that orchestration belongs to the caller.

The CrewAI imports are deferred to ``__init__`` so unit tests that
substitute a fake never pay the cost of importing CrewAI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aidevswarm.crews._prompts import load_prompt
from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import CriticScores, Idea, ScoredIdea
from aidevswarm.settings import Settings

_CREW_DIR = Path(__file__).resolve().parent


class CrewaiIdeationCrew:
    """Concrete :class:`aidevswarm.crews.protocols.IdeationCrew`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log = get_logger(__name__)
        self._scout_prompt = load_prompt(_CREW_DIR, "trend_scout")
        self._ideator_prompt = load_prompt(_CREW_DIR, "ideator")
        self._critic_prompt = load_prompt(_CREW_DIR, "critic")
        self._crew = self._build_crew()

    def _build_crew(self) -> Any:  # noqa: ANN401
        from crewai import Agent, Crew, Process, Task  # local import

        scout = Agent(
            role="Trend Scout",
            goal="Find deep, niche, multi-day problem spaces.",
            backstory=self._scout_prompt,
            llm=self._settings.model_fast,
            verbose=False,
            allow_delegation=False,
        )
        ideator = Agent(
            role="Ideator",
            goal="Turn problem spaces into concrete senior-level projects.",
            backstory=self._ideator_prompt,
            llm=self._settings.model_strong,
            verbose=False,
            allow_delegation=False,
        )
        critic = Agent(
            role="Critic",
            goal="Score and gate ideas against the depth rubric.",
            backstory=self._critic_prompt,
            llm=self._settings.model_strong,
            verbose=False,
            allow_delegation=False,
        )

        scout_task = Task(
            description="Produce three candidate problem spaces.",
            expected_output="Three short briefs.",
            agent=scout,
        )
        ideator_task = Task(
            description="Propose one project per candidate space.",
            expected_output="JSON list of Idea objects.",
            agent=ideator,
        )
        critic_task = Task(
            description="Score every idea per the rubric.",
            expected_output="JSON list of ScoredIdea objects.",
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
        result = self._crew.kickoff()
        parsed = self._parse(result)
        self._log.info("ideation.done", count=len(parsed))
        return parsed

    @staticmethod
    def _parse(crew_output: Any) -> list[ScoredIdea]:  # noqa: ANN401
        """Be liberal in what we accept from CrewAI's loose output type."""
        import json

        raw = getattr(crew_output, "raw", crew_output)
        if isinstance(raw, str):
            data = json.loads(raw)
        else:
            data = raw
        if not isinstance(data, list):
            raise ValueError("Critic did not return a JSON list")
        return [
            ScoredIdea(
                idea=Idea.model_validate(entry["idea"]),
                scores=CriticScores.model_validate(entry["scores"]),
                total=int(entry["total"]),
                rejected_reason=entry.get("rejected_reason"),
            )
            for entry in data
        ]
