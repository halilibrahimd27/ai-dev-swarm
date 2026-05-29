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
            dev, ci = self._repair_ci(milestone, workspace, sandbox, dev, ci)
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

    def _repair_ci(
        self,
        milestone: Milestone,
        workspace: Workspace,
        sandbox: Sandbox,
        dev: SDKResult,
        ci: SandboxResult,
    ) -> tuple[SDKResult, SandboxResult]:
        """Re-invoke the Developer with the exact CI failure and re-run CI.

        Bounded by ``settings.ci_repair_attempts``. The Developer resumes
        its own session, so a trivial fix (e.g. an unused import the Tester
        left behind) self-heals in-attempt instead of burning a whole
        milestone retry on an unchanged prompt. Returns the latest
        ``(dev, ci)`` — the caller decides PASS/FAIL from ``ci.passed``.
        """
        attempts = max(0, self._settings.ci_repair_attempts)
        for i in range(1, attempts + 1):
            self._log.info("build.ci_repair", milestone=milestone.title, attempt=i, of=attempts)
            self._emit(
                milestone,
                "ci_failed",
                f"CI gate failed (exit {ci.exit_code}); repair attempt {i}/{attempts}",
            )
            dev = self._dev_tool.run_sync(
                milestone, workspace, repair_context=_ci_repair_context(ci)
            )
            if not dev.success:
                # The repair invocation itself errored (budget/turns) — stop
                # repairing; the caller reports the CI failure.
                break
            ci = sandbox.run_ci(str(workspace.root))
            if ci.passed:
                self._emit(milestone, "ci_repaired", f"CI gate passed after repair attempt {i}")
                break
        return dev, ci

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
        # Pull steering notes ONCE (pull_unconsumed is consuming) and reuse
        # the rendered backstory + context across attempts.
        backstory = render_prompt(
            self._reviewer_template,
            steering_notes=(
                self._steering.pull_unconsumed(milestone.project_id, "Reviewer")
                if self._steering is not None
                else []
            ),
        )
        ctx = (
            f"WORKSPACE: {workspace.root}\n"
            f"MILESTONE: {milestone.title}\n"
            f"SPEC:\n{milestone.spec.model_dump_json(indent=2)}\n"
            f"DEVELOPER: session={dev.session_id} cost=${dev.cost_usd:.4f} turns={dev.turns}\n"
            f"TESTER:    session={tester.session_id} cost=${tester.cost_usd:.4f} turns={tester.turns}\n"
            f"CI: exit={ci.exit_code} stdout_tail={ci.stdout[-200:]!r}\n"
        )

        verdict = self._run_reviewer(milestone, backstory, ctx)
        if verdict is None:
            # The Reviewer's verdict was unparseable. Before defaulting to a
            # PASS, give it ONE more shot — a single transient bad emit
            # (truncation, prose wrap) shouldn't silently bypass the only
            # LLM quality gate the milestone has.
            self._log.info("build.reviewer_unparseable_retry", milestone=milestone.title)
            verdict = self._run_reviewer(milestone, backstory, ctx)
        if verdict is not None:
            return verdict

        # Still unparseable after a retry. The Reviewer is the ONLY semantic
        # gate that the acceptance criteria were genuinely met (mechanical
        # gates only prove the code lints/types/tests-green, not that it does
        # the right thing). A model that reliably emits malformed JSON must
        # NOT silently ship the milestone — fail instead, so it retries or
        # blocks honestly and a human looks.
        self._log.warning("build.reviewer_unparseable_fail", milestone=milestone.title)
        return MilestoneBuildResult(
            success=False,
            summary="reviewer verdict unparseable after a retry",
            failure_reason=(
                "Reviewer emitted no parseable verdict after a retry; refusing to "
                "auto-approve without a semantic sign-off."
            ),
            tokens_used=dev.turns + tester.turns,
        )

    def _run_reviewer(
        self,
        milestone: Milestone,
        backstory: str,
        ctx: str,
    ) -> MilestoneBuildResult | None:
        """One Reviewer kickoff. Returns the verdict, or None if unparseable."""
        from crewai import Agent, Crew, Process, Task

        from aidevswarm.crews._llm import make_llm

        reviewer = Agent(
            role="Reviewer",
            goal="Approve only if acceptance criteria are genuinely met.",
            backstory=backstory,
            llm=make_llm(self._settings.model_strong, self._settings.max_output_tokens),
            verbose=False,
            allow_delegation=False,
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
        return self._extract(result)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract(crew_output: Any) -> MilestoneBuildResult | None:
        """Pull a verdict out of the crew output, or None if unparseable.

        The Reviewer is an LLM: its output may be empty, prose-wrapped,
        fenced, or slightly-malformed JSON. Prefer CrewAI's validated
        ``output_pydantic`` when present, else repair-and-validate the raw
        text with ``loads_lenient``. Returns None (rather than a PASS
        fallback) so the caller can decide whether to retry.
        """
        verdict = getattr(crew_output, "pydantic", None)
        if isinstance(verdict, MilestoneBuildResult):
            return verdict
        raw = getattr(crew_output, "raw", crew_output)
        data = loads_lenient(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict):
            try:
                return MilestoneBuildResult.model_validate(keep_known(MilestoneBuildResult, data))
            except Exception:
                return None
        return None


# ----------------------------------------------------------------------
# Tiny helpers — testable in isolation
# ----------------------------------------------------------------------


def _ci_repair_context(ci: SandboxResult, *, limit: int = 3000) -> str:
    """The CI failure text handed to the Developer's repair invocation.

    Keeps the TAIL of stdout/stderr (where ruff/mypy/pytest print the
    actual errors) bounded so a huge gate log can't blow the prompt.
    """
    parts = [p for p in (ci.stdout, ci.stderr) if p and p.strip()]
    blob = "\n".join(parts).strip() or f"exit_code={ci.exit_code}"
    if len(blob) > limit:
        blob = "…(truncated)…\n" + blob[-limit:]
    return blob


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
