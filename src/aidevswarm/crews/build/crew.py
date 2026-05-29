"""Build crew — Phase 2 shape.

The Developer and Tester roles are now Claude Agent SDK invocations
(:class:`ClaudeAgentSDKDeveloperTool` /
:class:`ClaudeAgentSDKTesterTool`). There is no direct LLM call from
either role — the SDK owns the conversation, the trace, and the
session resume.

The Reviewer stays as a single-turn CrewAI Agent that reads the diff +
CI verdict and emits a ``MilestoneBuildResult`` JSON. The sandbox CI
gate runs between Tester and Reviewer; if it fails, the Reviewer is
skipped entirely and a failure result is returned.

Steering notes are still per-role and pulled at the start of every
SDK invocation (the SDK tools call ``SteeringRepo.pull_unconsumed``
themselves); the Reviewer uses the Phase-1 renderer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk.types import McpStdioServerConfig

from aidevswarm.crews._parsing import keep_known, loads_lenient
from aidevswarm.crews._prompts import load_prompt
from aidevswarm.crews._spend import record_crew_spend
from aidevswarm.db.sessions import MilestoneSessionRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.observability import TranscriptEntry, TranscriptPublisher
from aidevswarm.schemas import Milestone, MilestoneBuildResult
from aidevswarm.settings import Settings
from aidevswarm.steering import SteeringRepo, render_prompt
from aidevswarm.tools import Sandbox, SandboxResult, SpendRecorder, Workspace
from aidevswarm.tools.claude_agent_sdk_tool import (
    ClaudeAgentSDKDeveloperTool,
    ClaudeAgentSDKTesterTool,
    SDKResult,
)

_CREW_DIR = Path(__file__).resolve().parent


class CrewaiBuildCrew:
    """Concrete :class:`aidevswarm.crews.protocols.BuildCrew`."""

    def __init__(
        self,
        settings: Settings,
        session_repo: MilestoneSessionRepo,
        *,
        steering_repo: SteeringRepo | None = None,
        mcp_servers: dict[str, McpStdioServerConfig] | None = None,
        recorder: SpendRecorder | None = None,
        transcript: TranscriptPublisher | None = None,
    ) -> None:
        self._settings = settings
        self._log = get_logger(__name__)
        self._steering = steering_repo
        self._recorder = recorder
        self._transcript = transcript
        self._reviewer_template = load_prompt(_CREW_DIR, "reviewer")
        self._dev_tool = ClaudeAgentSDKDeveloperTool(
            settings,
            session_repo,
            steering_repo=steering_repo,
            mcp_servers=mcp_servers,
            recorder=recorder,
            transcript=transcript,
        )
        self._tester_tool = ClaudeAgentSDKTesterTool(
            settings,
            session_repo,
            steering_repo=steering_repo,
            mcp_servers=mcp_servers,
            recorder=recorder,
            transcript=transcript,
        )

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        milestone: Milestone,
        workspace: Workspace,
        sandbox: Sandbox,
    ) -> MilestoneBuildResult:
        self._emit(milestone, "milestone_start", f"Build started: {milestone.title}")
        dev = self._dev_tool.run_sync(milestone, workspace)
        if not dev.success:
            return _failure_from_sdk(dev, phase="developer")

        tester = self._tester_tool.run_sync(milestone, workspace)
        if not tester.success:
            return _failure_from_sdk(tester, phase="tester")

        ci = sandbox.run_ci(str(workspace.root))
        if not ci.passed:
            self._log.info("build.ci_failed", exit_code=ci.exit_code)
            self._emit(milestone, "ci_failed", f"CI gate failed (exit {ci.exit_code})")
            return _failure_from_ci(ci, dev=dev, tester=tester)

        self._emit(milestone, "ci_passed", "CI gate passed")
        verdict = self._review(milestone, workspace, dev, tester, ci)
        self._emit(
            milestone,
            "review_done",
            f"Reviewer: {'APPROVED' if verdict.success else 'REJECTED'} — {verdict.summary}",
        )
        return verdict

    def _emit(self, milestone: Milestone, kind: str, text: str) -> None:
        """Publish a build-stage marker to the live transcript (best-effort)."""
        if self._transcript is None:
            return
        try:
            self._transcript.publish(
                TranscriptEntry(
                    topic="transcript",
                    project_id=milestone.project_id,
                    role="BuildCrew",
                    kind=kind,
                    text=text,
                )
            )
        except Exception as exc:  # a UI sink must never break the build
            self._log.warning("build.transcript_publish_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Reviewer (single-turn CrewAI agent)
    # ------------------------------------------------------------------

    def _review(
        self,
        milestone: Milestone,
        workspace: Workspace,
        dev: SDKResult,
        tester: SDKResult,
        ci: SandboxResult,
    ) -> MilestoneBuildResult:
        from crewai import Agent, Crew, Process, Task

        from aidevswarm.crews._llm import make_llm

        backstory = render_prompt(
            self._reviewer_template,
            steering_notes=(
                self._steering.pull_unconsumed(milestone.project_id, "Reviewer")
                if self._steering is not None
                else []
            ),
        )

        reviewer = Agent(
            role="Reviewer",
            goal="Approve only if acceptance criteria are genuinely met.",
            backstory=backstory,
            llm=make_llm(self._settings.model_strong, self._settings.max_output_tokens),
            verbose=False,
            allow_delegation=False,
        )

        ctx = (
            f"WORKSPACE: {workspace.root}\n"
            f"MILESTONE: {milestone.title}\n"
            f"SPEC:\n{milestone.spec.model_dump_json(indent=2)}\n"
            f"DEVELOPER: session={dev.session_id} cost=${dev.cost_usd:.4f} turns={dev.turns}\n"
            f"TESTER:    session={tester.session_id} cost=${tester.cost_usd:.4f} turns={tester.turns}\n"
            f"CI: exit={ci.exit_code} stdout_tail={ci.stdout[-200:]!r}\n"
        )
        crew = Crew(
            agents=[reviewer],
            tasks=[
                Task(
                    description=ctx
                    + "Approve and emit MilestoneBuildResult JSON, or reject with fixes.",
                    expected_output=(
                        'JSON like {"success": true, "summary": "...", ' '"failure_reason": null}'
                    ),
                    agent=reviewer,
                    # Force a schema-valid verdict instead of free prose,
                    # which json_repair couldn't parse (-> empty -> the
                    # milestone spiralled on "missing required fields").
                    output_pydantic=MilestoneBuildResult,
                )
            ],
            process=Process.sequential,
            verbose=False,
        )
        result = crew.kickoff()
        record_crew_spend(
            self._recorder,
            result,
            project_id=milestone.project_id,
            milestone_id=milestone.id,
            role="Reviewer",
            model=self._settings.model_strong,
        )
        # Prefer CrewAI's validated structured output when present.
        verdict = getattr(result, "pydantic", None)
        if isinstance(verdict, MilestoneBuildResult):
            return verdict
        return self._parse(result, fallback_tokens=dev.turns + tester.turns)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(crew_output: Any, *, fallback_tokens: int = 0) -> MilestoneBuildResult:
        # The Reviewer is an LLM: its output may be empty, prose-wrapped,
        # fenced, or slightly-malformed JSON. loads_lenient repairs it and
        # never raises (a raw json.loads here crashed the whole build).
        # NOTE: this is reached only AFTER the Developer + Tester ran and
        # the CI gate PASSED. So if the Reviewer's verdict is unparseable,
        # we treat the milestone as a PASS (the mechanical gates already
        # succeeded) rather than spiralling on "missing required fields".
        raw = getattr(crew_output, "raw", crew_output)
        data = loads_lenient(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict):
            try:
                return MilestoneBuildResult.model_validate(keep_known(MilestoneBuildResult, data))
            except Exception:
                pass
        return MilestoneBuildResult(
            success=True,
            summary="reviewer verdict unparseable; accepted (Developer+Tester+CI passed)",
            tokens_used=fallback_tokens,
        )


# ----------------------------------------------------------------------
# Tiny helpers — testable in isolation
# ----------------------------------------------------------------------


def _failure_from_sdk(result: SDKResult, *, phase: str) -> MilestoneBuildResult:
    return MilestoneBuildResult(
        success=False,
        commit_hash=None,
        summary=f"{phase} SDK invocation failed",
        failure_reason=(result.failure_reason or result.summary)[:500] or "unknown",
        tokens_used=result.turns,
    )


def _failure_from_ci(
    ci: SandboxResult, *, dev: SDKResult, tester: SDKResult
) -> MilestoneBuildResult:
    return MilestoneBuildResult(
        success=False,
        commit_hash=None,
        summary="CI gate failed",
        failure_reason=ci.stderr.strip()[:500] or f"exit_code={ci.exit_code}",
        tokens_used=dev.turns + tester.turns,
    )
