"""CrewAI-backed Build crew (one invocation per milestone).

Phase 0 wires Developer / Tester / Reviewer agents to the milestone
description and lets them collaborate via CrewAI's sequential process.
Real code generation will live in the Developer agent's tool calls;
Phase 2 replaces the Developer with a Claude Agent SDK tool.

The actual CI gate is delegated to the supplied :class:`Sandbox`
instance — the build crew is NOT trusted to mark a milestone done on
its own.

Steering notes are pulled per role just before kickoff so each milestone
picks up whatever the operator has queued since the last build run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from aidevswarm.crews._prompts import load_prompt
from aidevswarm.logging_config import get_logger
from aidevswarm.schemas import Milestone, MilestoneBuildResult
from aidevswarm.settings import Settings
from aidevswarm.steering import SteeringRepo, render_prompt
from aidevswarm.tools import Sandbox, Workspace

_CREW_DIR = Path(__file__).resolve().parent


class CrewaiBuildCrew:
    """Concrete :class:`aidevswarm.crews.protocols.BuildCrew`."""

    def __init__(
        self,
        settings: Settings,
        *,
        steering_repo: SteeringRepo | None = None,
    ) -> None:
        self._settings = settings
        self._log = get_logger(__name__)
        self._steering = steering_repo
        self._developer_template = load_prompt(_CREW_DIR, "developer")
        self._tester_template = load_prompt(_CREW_DIR, "tester")
        self._reviewer_template = load_prompt(_CREW_DIR, "reviewer")

    def _pull(self, project_id: UUID, role: str) -> list[str]:
        if self._steering is None:
            return []
        return self._steering.pull_unconsumed(project_id, role)

    def _build_crew(self, milestone: Milestone, workspace: Workspace) -> Any:
        from crewai import Agent, Crew, Process, Task

        pid = milestone.project_id
        dev_backstory = render_prompt(
            self._developer_template, steering_notes=self._pull(pid, "Developer")
        )
        tester_backstory = render_prompt(
            self._tester_template, steering_notes=self._pull(pid, "Tester")
        )
        reviewer_backstory = render_prompt(
            self._reviewer_template, steering_notes=self._pull(pid, "Reviewer")
        )

        developer = Agent(
            role="Developer",
            goal="Implement the milestone in the persistent workspace.",
            backstory=dev_backstory,
            llm=self._settings.model_strong,
            verbose=False,
            allow_delegation=False,
        )
        tester = Agent(
            role="Tester",
            goal="Write/expand tests and run the CI gate to verify.",
            backstory=tester_backstory,
            llm=self._settings.model_strong,
            verbose=False,
            allow_delegation=False,
        )
        reviewer = Agent(
            role="Reviewer",
            goal="Approve only if acceptance criteria are genuinely met.",
            backstory=reviewer_backstory,
            llm=self._settings.model_strong,
            verbose=False,
            allow_delegation=False,
        )

        ctx = (
            f"WORKSPACE: {workspace.root}\n"
            f"MILESTONE: {milestone.title}\n"
            f"SPEC:\n{milestone.spec.model_dump_json(indent=2)}\n"
        )
        return Crew(
            agents=[developer, tester, reviewer],
            tasks=[
                Task(
                    description=ctx + "Implement the milestone.",
                    expected_output="diff",
                    agent=developer,
                ),
                Task(description=ctx + "Run the CI gate.", expected_output="ok|fail", agent=tester),
                Task(
                    description=ctx + "Approve and commit, or reject with fixes.",
                    expected_output="JSON MilestoneBuildResult",
                    agent=reviewer,
                ),
            ],
            process=Process.sequential,
            verbose=False,
        )

    def run(
        self,
        *,
        milestone: Milestone,
        workspace: Workspace,
        sandbox: Sandbox,
    ) -> MilestoneBuildResult:
        crew = self._build_crew(milestone, workspace)
        result = crew.kickoff()

        # The Reviewer is expected to emit MilestoneBuildResult JSON.
        parsed = self._parse(result)

        # Hard CI gate: regardless of what the crew claims, run the
        # sandbox and refuse to mark success on failure.
        ci = sandbox.run_ci(str(workspace.root))
        if not ci.passed:
            self._log.info("build.ci_failed", exit_code=ci.exit_code)
            return MilestoneBuildResult(
                success=False,
                commit_hash=None,
                summary="CI gate failed",
                failure_reason=ci.stderr.strip()[:500],
                tokens_used=parsed.tokens_used,
            )
        return parsed

    @staticmethod
    def _parse(crew_output: Any) -> MilestoneBuildResult:
        raw = getattr(crew_output, "raw", crew_output)
        data = json.loads(raw) if isinstance(raw, str) else raw
        return MilestoneBuildResult.model_validate(data)
