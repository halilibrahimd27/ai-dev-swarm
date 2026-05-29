"""The Diagnostician — self-healing analysis of a milestone failure.

When a milestone fails its quality gate (CI red, or the Reviewer rejects),
the next retry used to start blind. The Diagnostician closes that gap: a
cheap-model agent reads the CONCRETE failure + the milestone spec, works
out the most likely root cause, and emits ONE actionable remediation. That
remediation is:

  * written as a steering note, so the NEXT Developer attempt is informed
    (it lands in the Developer's prompt via the steering slot), and
  * published to the boardroom as a Diagnostician decision, so the operator
    (and a blocked-project post-mortem) sees the reasoning.

It complements the in-attempt CI-repair loop (which fixes mechanical lint/
type/test errors) and the ProjectPool's transient backoff (which retries
API/transport blips). This handles the "the build is genuinely going wrong,
why?" case. Budget-aware: the caller skips it when the daily budget is
exhausted. Never raises — a diagnosis hiccup must not break the tick.
"""

from __future__ import annotations

from aidevswarm.crews._spend import record_crew_spend
from aidevswarm.logging_config import get_logger
from aidevswarm.observability import TranscriptPublisher, publish_decision
from aidevswarm.schemas import Milestone, Project
from aidevswarm.settings import Settings
from aidevswarm.steering import SteeringRepo
from aidevswarm.tools import SpendRecorder

_MAX_REMEDIATION_CHARS = 600


class Diagnostician:
    """Analyse a milestone failure and produce a remediation. Never raises."""

    def __init__(
        self,
        settings: Settings,
        *,
        steering_repo: SteeringRepo | None = None,
        transcript: TranscriptPublisher | None = None,
        recorder: SpendRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._steering = steering_repo
        self._transcript = transcript
        self._recorder = recorder
        self._log = get_logger(__name__)

    def diagnose(self, project: Project, milestone: Milestone, failure_reason: str) -> str | None:
        """Diagnose a failure; side-effect a steering note + boardroom decision.

        Returns the remediation text, or None if diagnosis was unavailable.
        """
        ctx = (
            f"A milestone build just FAILED its quality gate.\n"
            f"MILESTONE: {milestone.title}\n"
            f"SPEC:\n{milestone.spec.model_dump_json(indent=2)[:1500]}\n"
            f"FAILURE (CI / reviewer output, tail):\n{failure_reason[:1500]}\n"
        )
        try:
            remediation = self._run(project, milestone, ctx)
        except Exception as exc:  # diagnosis must never break the tick
            self._log.warning("diagnostician.failed", error=str(exc))
            return None
        remediation = (remediation or "").strip()[:_MAX_REMEDIATION_CHARS]
        if not remediation:
            return None
        # Steer the next attempt + record the reasoning in the boardroom.
        if self._steering is not None:
            try:
                self._steering.add_note(
                    project.id,
                    f"[Diagnostician] Last attempt at '{milestone.title}' failed. {remediation}",
                    author="diagnostician",
                )
            except Exception as exc:  # pragma: no cover — defensive
                self._log.warning("diagnostician.note_failed", error=str(exc))
        publish_decision(
            self._transcript,
            project_id=project.id,
            role="Diagnostician",
            text=f"Root-cause for '{milestone.title}': {remediation}",
        )
        self._log.info("diagnostician.done", project=project.name, milestone=milestone.title)
        return remediation

    def _run(self, project: Project, milestone: Milestone, ctx: str) -> str:
        """One cheap-model kickoff returning a plain-text remediation."""
        from crewai import Agent, Crew, Process, Task

        from aidevswarm.crews._llm import make_llm

        agent = Agent(
            role="Diagnostician",
            goal="Find the root cause of a failed build and give one concrete fix.",
            backstory=(
                "You are a senior engineer doing triage. You read a failing "
                "build's output and the milestone spec, identify the single most "
                "likely root cause, and hand the Developer ONE specific, "
                "actionable instruction to fix it on the next attempt."
            ),
            llm=make_llm(self._settings.model_fast, self._settings.max_output_tokens),
            verbose=False,
            allow_delegation=False,
        )
        crew = Crew(
            agents=[agent],
            tasks=[
                Task(
                    description=(
                        ctx + "Identify the root cause in one sentence, then give ONE "
                        "concrete instruction the Developer should follow next "
                        "(name the file/symbol/command where possible). Plain text, "
                        "no preamble, max ~80 words."
                    ),
                    expected_output="A short root-cause + one concrete fix instruction.",
                    agent=agent,
                )
            ],
            process=Process.sequential,
            verbose=False,
        )
        result = crew.kickoff()
        record_crew_spend(
            self._recorder,
            result,
            project_id=project.id,
            milestone_id=milestone.id,
            role="Diagnostician",
            model=self._settings.model_fast,
        )
        raw = getattr(result, "raw", result)
        return str(raw) if raw is not None else ""


__all__ = ["Diagnostician"]
