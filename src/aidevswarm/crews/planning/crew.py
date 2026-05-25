"""CrewAI-backed Planning crew.

PM decomposes the project into a milestone graph; the Architect fills
in technical notes per milestone. Output is a single
:class:`MilestoneGraph`.

Steering notes are pulled per role at the start of each ``run()`` call
so notes the operator adds mid-cycle are picked up on the next planning
pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from aidevswarm.crews._prompts import load_prompt
from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import MilestoneGraph, MilestoneSpec, ProjectSpec
from aidevswarm.settings import Settings
from aidevswarm.steering import SteeringRepo, render_prompt

_CREW_DIR = Path(__file__).resolve().parent


class CrewaiPlanningCrew:
    """Concrete :class:`aidevswarm.crews.protocols.PlanningCrew`."""

    def __init__(
        self,
        settings: Settings,
        *,
        steering_repo: SteeringRepo | None = None,
    ) -> None:
        self._settings = settings
        self._log = get_logger(__name__)
        self._steering = steering_repo
        # Store the raw templates; render per-run so steering notes added
        # between runs are picked up.
        self._pm_template = load_prompt(_CREW_DIR, "pm")
        self._arch_template = load_prompt(_CREW_DIR, "architect")

    def _pull(self, project_id: UUID, role: str) -> list[str]:
        if self._steering is None:
            return []
        return self._steering.pull_unconsumed(project_id, role)

    def _build_crew(self, project_id: UUID, spec: ProjectSpec) -> Any:
        from crewai import Agent, Crew, Process, Task

        pm_backstory = render_prompt(
            self._pm_template, steering_notes=self._pull(project_id, "PM")
        )
        arch_backstory = render_prompt(
            self._arch_template, steering_notes=self._pull(project_id, "Architect")
        )

        pm = Agent(
            role="PM",
            goal="Decompose the project into 4-10 testable milestones.",
            backstory=pm_backstory,
            llm=self._settings.model_strong,
            verbose=False,
            allow_delegation=False,
        )
        architect = Agent(
            role="Architect",
            goal="Set the technical foundation and per-milestone notes.",
            backstory=arch_backstory,
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

    def run(self, project_id: UUID, spec: ProjectSpec) -> MilestoneGraph:
        crew = self._build_crew(project_id, spec)
        result = crew.kickoff()
        graph = self._parse(result)
        self._log.info("planning.done", milestones=len(graph.milestones))
        return graph

    @staticmethod
    def _parse(crew_output: Any) -> MilestoneGraph:
        raw = getattr(crew_output, "raw", crew_output)
        data = json.loads(raw) if isinstance(raw, str) else raw
        return MilestoneGraph(
            milestones=[MilestoneSpec.model_validate(m) for m in data.get("milestones", [])]
        )
