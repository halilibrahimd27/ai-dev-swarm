"""CrewAI-backed Planning crew.

PM decomposes the project into a milestone graph; the Architect fills
in technical notes per milestone. Output is a single
:class:`MilestoneGraph`.

Steering notes are pulled per role at the start of each ``run()`` call
so notes the operator adds mid-cycle are picked up on the next planning
pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from aidevswarm.crews._parsing import clean_milestone_dict, loads_lenient
from aidevswarm.crews._prompts import load_prompt
from aidevswarm.crews._spend import record_crew_spend
from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import AcceptanceCriterion, MilestoneGraph, MilestoneSpec, ProjectSpec
from aidevswarm.settings import Settings
from aidevswarm.steering import SteeringRepo, render_prompt
from aidevswarm.tools import SpendRecorder

_CREW_DIR = Path(__file__).resolve().parent


class CrewaiPlanningCrew:
    """Concrete :class:`aidevswarm.crews.protocols.PlanningCrew`."""

    def __init__(
        self,
        settings: Settings,
        *,
        steering_repo: SteeringRepo | None = None,
        recorder: SpendRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._log = get_logger(__name__)
        self._steering = steering_repo
        self._recorder = recorder
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

        from aidevswarm.crews._llm import make_llm

        pm_backstory = render_prompt(self._pm_template, steering_notes=self._pull(project_id, "PM"))
        arch_backstory = render_prompt(
            self._arch_template, steering_notes=self._pull(project_id, "Architect")
        )
        strong_llm = make_llm(self._settings.model_strong, self._settings.max_output_tokens)

        pm = Agent(
            role="PM",
            goal="Decompose the project into 4-10 testable milestones.",
            backstory=pm_backstory,
            llm=strong_llm,
            verbose=False,
            allow_delegation=False,
        )
        architect = Agent(
            role="Architect",
            goal="Set the technical foundation and per-milestone notes.",
            backstory=arch_backstory,
            llm=strong_llm,
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
        record_crew_spend(
            self._recorder,
            result,
            project_id=project_id,
            milestone_id=None,
            role="planning",
            model=self._settings.model_strong,
        )
        specs = self._parse_specs(result, self._log)
        if not specs:
            # MilestoneGraph requires >= 1 milestone; an empty list +
            # advancing the project would also be misleading. Raise a
            # named ValueError instead — the project pool's safety-net
            # catches this and moves the project to BLOCKED so the
            # operator can rescope or abort via the web panel.
            raise ValueError("planning crew produced zero parseable milestones")
        graph = MilestoneGraph(milestones=specs)
        self._log.info("planning.done", milestones=len(graph.milestones))
        return graph

    @staticmethod
    def _parse_specs(crew_output: Any, log: Any | None = None) -> list[MilestoneSpec]:
        """Tolerant parse: malformed entries are skipped with a warning.

        CrewAI's Architect occasionally returns truncated or trailing-
        garbage JSON — one bad milestone must NOT crash the orchestrator.
        Returns whatever IS parseable; the caller decides whether an
        empty list is fatal.
        """
        raw = getattr(crew_output, "raw", crew_output)
        try:
            data = loads_lenient(raw)
        except Exception as exc:
            if log is not None:
                log.warning(
                    "planning.parse.json_error",
                    error=str(exc),
                    raw_head=str(raw)[:200] if raw else "",
                )
            return []
        entries = data.get("milestones", []) if isinstance(data, dict) else []
        out: list[MilestoneSpec] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # Drop LLM-added extras (e.g. "id": "m1") that MilestoneSpec's
            # extra='forbid' would otherwise reject, at the milestone and
            # nested acceptance-criteria levels.
            cleaned = clean_milestone_dict(entry, MilestoneSpec, AcceptanceCriterion)
            try:
                out.append(MilestoneSpec.model_validate(cleaned))
            except Exception as exc:
                if log is not None:
                    log.warning("planning.parse.skip_entry", error=str(exc), entry=str(entry)[:200])
        return out
