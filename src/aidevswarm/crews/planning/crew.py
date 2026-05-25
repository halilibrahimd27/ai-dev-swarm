"""CrewAI-backed Planning crew.

PM decomposes the project into a milestone graph; the Architect fills
in technical notes per milestone. Output is a single
:class:`MilestoneGraph`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aidevswarm.crews._prompts import load_prompt
from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import MilestoneGraph, MilestoneSpec, ProjectSpec
from aidevswarm.settings import Settings

_CREW_DIR = Path(__file__).resolve().parent


class CrewaiPlanningCrew:
    """Concrete :class:`aidevswarm.crews.protocols.PlanningCrew`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log = get_logger(__name__)
        self._pm_prompt = load_prompt(_CREW_DIR, "pm")
        self._arch_prompt = load_prompt(_CREW_DIR, "architect")
        self._crew_factory = self._build_crew

    def _build_crew(self, spec: ProjectSpec) -> Any:  # noqa: ANN401
        from crewai import Agent, Crew, Process, Task

        pm = Agent(
            role="PM",
            goal="Decompose the project into 4-10 testable milestones.",
            backstory=self._pm_prompt,
            llm=self._settings.model_strong,
            verbose=False,
            allow_delegation=False,
        )
        architect = Agent(
            role="Architect",
            goal="Set the technical foundation and per-milestone notes.",
            backstory=self._arch_prompt,
            llm=self._settings.model_strong,
            verbose=False,
            allow_delegation=False,
        )

        pm_task = Task(
            description=(
                "Decompose the following project spec into a MilestoneGraph "
                f"(JSON). SPEC:\n{spec.model_dump_json(indent=2)}"
            ),
            expected_output="JSON MilestoneGraph.",
            agent=pm,
        )
        arch_task = Task(
            description=(
                "Fill in technical_note for each milestone produced by the PM. "
                "Return the same MilestoneGraph JSON with technical_note set."
            ),
            expected_output="JSON MilestoneGraph.",
            agent=architect,
        )

        return Crew(
            agents=[pm, architect],
            tasks=[pm_task, arch_task],
            process=Process.sequential,
            verbose=False,
        )

    def run(self, spec: ProjectSpec) -> MilestoneGraph:
        crew = self._crew_factory(spec)
        result = crew.kickoff()
        graph = self._parse(result)
        self._log.info("planning.done", milestones=len(graph.milestones))
        return graph

    @staticmethod
    def _parse(crew_output: Any) -> MilestoneGraph:  # noqa: ANN401
        raw = getattr(crew_output, "raw", crew_output)
        data = json.loads(raw) if isinstance(raw, str) else raw
        return MilestoneGraph(
            milestones=[MilestoneSpec.model_validate(m) for m in data.get("milestones", [])]
        )
