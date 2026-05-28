"""CrewAI-backed Replanner crew (Architect + PM).

The crew is invoked from ``Tick._replan`` whenever a milestone has
just completed and we're deciding what to do before the next one.
It returns ONE :class:`ReplannerAction`. CrewAI is imported lazily
so tests can substitute a fake without paying the import cost.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter

from aidevswarm.crews._spend import record_crew_spend
from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import (
    Milestone,
    MilestoneSession,
    Noop,
    Project,
    ReplannerAction,
)
from aidevswarm.settings import Settings
from aidevswarm.steering import SteeringRepo, render_prompt
from aidevswarm.tools import SpendRecorder

_CREW_DIR = Path(__file__).resolve().parent
_ADAPTER: TypeAdapter[ReplannerAction] = TypeAdapter(ReplannerAction)


def _load(name: str) -> str:
    return (_CREW_DIR / "prompts" / f"{name}.txt").read_text("utf-8")


class CrewaiReplanningCrew:
    """Concrete :class:`aidevswarm.crews.replanning.protocols.ReplanningCrew`."""

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
        self._architect_template = _load("architect")
        self._pm_template = _load("pm")

    def _pull(self, project_id: UUID, role: str) -> list[str]:
        if self._steering is None:
            return []
        return self._steering.pull_unconsumed(project_id, role)

    def run(
        self,
        *,
        project: Project,
        next_milestone: Milestone,
        recent_sessions: Sequence[MilestoneSession],
    ) -> ReplannerAction:
        from crewai import Agent, Crew, Process, Task

        from aidevswarm.crews._llm import make_llm

        architect_backstory = render_prompt(
            self._architect_template,
            steering_notes=self._pull(project.id, "Architect"),
        )
        pm_backstory = render_prompt(
            self._pm_template,
            steering_notes=self._pull(project.id, "PM"),
        )
        strong_llm = make_llm(self._settings.model_strong, self._settings.max_output_tokens)

        architect = Agent(
            role="Replanner Architect",
            goal="Pick the right ReplannerAction for the upcoming milestone.",
            backstory=architect_backstory,
            llm=strong_llm,
            verbose=False,
            allow_delegation=False,
        )
        pm = Agent(
            role="Replanner PM",
            goal="Help the Architect decide; own scope-shape decisions.",
            backstory=pm_backstory,
            llm=strong_llm,
            verbose=False,
            allow_delegation=False,
        )

        ctx = (
            f"PROJECT: {project.name} (state={project.state.value})\n"
            f"NEXT MILESTONE: {next_milestone.title} (retry_count="
            f"{next_milestone.retry_count})\n"
            f"NEXT SPEC:\n{next_milestone.spec.model_dump_json(indent=2)}\n"
            f"RECENT SESSIONS:\n{_summarise_sessions(recent_sessions)}\n"
        )

        crew = Crew(
            agents=[architect, pm],
            tasks=[
                Task(
                    description=ctx + "PM: argue for or against Split/Amend/Escalate.",
                    expected_output="PM's verdict in plain English.",
                    agent=pm,
                ),
                Task(
                    description=ctx + "Architect: emit ONE ReplannerAction JSON.",
                    expected_output="JSON discriminated by 'action'.",
                    agent=architect,
                ),
            ],
            process=Process.sequential,
            verbose=False,
        )
        try:
            result = crew.kickoff()
        except Exception as exc:  # pragma: no cover — live LLM path
            self._log.warning("replanner.crew_failed", error=str(exc))
            return Noop()

        record_crew_spend(
            self._recorder,
            result,
            project_id=project.id,
            milestone_id=next_milestone.id,
            role="replanner",
            model=self._settings.model_strong,
        )
        return self._parse(result)

    @staticmethod
    def _parse(crew_output: Any) -> ReplannerAction:
        raw = getattr(crew_output, "raw", crew_output)
        data = json.loads(raw) if isinstance(raw, str) else raw
        try:
            return _ADAPTER.validate_python(data)
        except Exception:
            # LLM produced something the schema doesn't accept — be
            # defensive and Noop rather than crash the tick.
            return Noop()


def _summarise_sessions(sessions: Sequence[MilestoneSession]) -> str:
    if not sessions:
        return "  (none yet)"
    lines = []
    for s in sessions[-6:]:
        lines.append(
            f"  - {s.role}: turns={s.turns} cost=${s.cost_usd:.4f} " f"session={s.session_id[:12]}"
        )
    return "\n".join(lines)
