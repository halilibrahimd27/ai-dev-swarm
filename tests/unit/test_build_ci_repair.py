"""The in-attempt CI repair loop.

When the CI gate fails, the build crew re-invokes the Developer with the
exact lint/type/test errors and re-runs CI, up to ``ci_repair_attempts``
times, BEFORE the milestone counts a failed attempt. This is what stops a
trivial lint slip (e.g. an unused import the Tester left behind) from
burning a whole retry on an unchanged prompt.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from aidevswarm.crews.build.crew import CrewaiBuildCrew, _ci_repair_context
from aidevswarm.schemas import (
    AcceptanceCriterion,
    Milestone,
    MilestoneBuildResult,
    MilestoneSpec,
)
from aidevswarm.settings import Settings
from aidevswarm.tools.claude_agent_sdk_tool import SDKResult
from aidevswarm.tools.sandbox import SandboxRun


def _milestone() -> Milestone:
    return Milestone(
        project_id=uuid4(),
        ordinal=0,
        title="m",
        spec=MilestoneSpec(
            title="m",
            description="d",
            acceptance_criteria=[AcceptanceCriterion(description="x", verifier="pytest")],
        ),
    )


def _ok_sdk() -> SDKResult:
    return SDKResult(success=True, session_id="s", cost_usd=0.1, turns=5, summary="ok")


def _crew(repair_attempts: int = 2) -> CrewaiBuildCrew:
    crew = CrewaiBuildCrew(
        Settings(ANTHROPIC_API_KEY="sk-ant-test", AIDEVSWARM_CI_REPAIR_ATTEMPTS=repair_attempts),
        MagicMock(),
    )
    # Dev + Tester always "succeed"; the CI gate drives the loop.
    crew._dev_tool = MagicMock()
    crew._dev_tool.run_sync.return_value = _ok_sdk()
    crew._tester_tool = MagicMock()
    crew._tester_tool.run_sync.return_value = _ok_sdk()
    # Skip the real CrewAI Reviewer — return an approval.
    crew._review = MagicMock(return_value=MilestoneBuildResult(success=True, summary="ok"))  # type: ignore[method-assign]
    return crew


class _Sandbox:
    """run_ci fails the first ``fail_times`` calls, then passes."""

    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self.calls = 0

    def run_ci(self, _workspace_dir: str) -> SandboxRun:
        self.calls += 1
        if self.calls <= self._fail_times:
            return SandboxRun(
                passed=False,
                stdout="F401 'mod.unused' imported but unused",
                stderr="",
                exit_code=1,
            )
        return SandboxRun(passed=True, stdout="ok", stderr="", exit_code=0)


def _run(crew: CrewaiBuildCrew, sandbox: _Sandbox) -> MilestoneBuildResult:
    return crew.run(
        milestone=_milestone(),
        workspace=SimpleNamespace(root="/tmp/ws"),  # type: ignore[arg-type]
        sandbox=sandbox,  # type: ignore[arg-type]
    )


def test_ci_failure_self_heals_within_repair_budget() -> None:
    crew = _crew(repair_attempts=2)
    sandbox = _Sandbox(fail_times=1)  # fails once, passes on the repair
    result = _run(crew, sandbox)
    assert result.success is True
    # initial CI + one repair CI == 2 runs
    assert sandbox.calls == 2
    # the repair invocation carried the CI failure as repair_context
    repair_call = crew._dev_tool.run_sync.call_args_list[-1]
    assert "F401" in repair_call.kwargs["repair_context"]


def test_ci_failure_past_budget_reports_failure() -> None:
    crew = _crew(repair_attempts=2)
    sandbox = _Sandbox(fail_times=99)  # never recovers
    result = _run(crew, sandbox)
    assert result.success is False
    assert result.summary == "CI gate failed"
    # initial CI + exactly 2 repair re-runs
    assert sandbox.calls == 3


def test_repair_disabled_when_attempts_zero() -> None:
    crew = _crew(repair_attempts=0)
    sandbox = _Sandbox(fail_times=99)
    result = _run(crew, sandbox)
    assert result.success is False
    assert sandbox.calls == 1  # no repair runs at all
    crew._dev_tool.run_sync.assert_called_once()  # never re-invoked


def test_tester_runs_with_its_own_turn_cap() -> None:
    crew = _crew()
    _run(crew, _Sandbox(fail_times=0))  # passes immediately
    # The Tester is invoked with the (lower) tester_max_turns cap, not the
    # Developer's default — the recurring Tester cost saver.
    assert crew._tester_tool.run_sync.call_args.kwargs["max_turns"] == 40


def test_repair_context_keeps_error_tail() -> None:
    ci = SandboxRun(passed=False, stdout="x" * 5000, stderr="ERR", exit_code=1)
    ctx = _ci_repair_context(ci, limit=100)
    assert ctx.endswith("ERR") or "ERR" in ctx
    assert len(ctx) <= 100 + len("…(truncated)…\n")
