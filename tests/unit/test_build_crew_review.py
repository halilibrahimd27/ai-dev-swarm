"""Reviewer verdict extraction + the retry-before-PASS behaviour.

The Reviewer is the only LLM quality gate after the mechanical
Developer + Tester + CI gates. Its output can be empty / prose-wrapped /
fenced / slightly-malformed JSON. The build crew gives it ONE retry on an
unparseable verdict before defaulting to a PASS (since the mechanical
gates already succeeded), so a single transient bad emit can't silently
bypass the gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from aidevswarm.crews.build.crew import CrewaiBuildCrew
from aidevswarm.schemas import (
    AcceptanceCriterion,
    Milestone,
    MilestoneBuildResult,
    MilestoneSpec,
)
from aidevswarm.settings import Settings
from aidevswarm.tools.claude_agent_sdk_tool import SDKResult
from aidevswarm.tools.sandbox import SandboxRun


@dataclass
class _Raw:
    """Mimics a CrewAI output object: a ``.raw`` string, no ``.pydantic``."""

    raw: str


def _crew() -> CrewaiBuildCrew:
    # session_repo is only stashed by the SDK tools; never invoked here.
    return CrewaiBuildCrew(Settings(ANTHROPIC_API_KEY="sk-ant-test"), MagicMock())


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


def _sdk() -> SDKResult:
    return SDKResult(success=True, session_id="s", cost_usd=0.1, turns=5, summary="ok")


def _ci() -> SandboxRun:
    return SandboxRun(passed=True, stdout="ok", stderr="", exit_code=0)


def _review(crew: CrewaiBuildCrew) -> MilestoneBuildResult:
    return crew._review(
        _milestone(),
        SimpleNamespace(root="/tmp/ws"),  # type: ignore[arg-type]
        _sdk(),
        _sdk(),
        _ci(),
    )


# ---------------------------------------------------------------------------
# _extract — verdict or None (never the PASS fallback)
# ---------------------------------------------------------------------------


def test_extract_prefers_validated_pydantic() -> None:
    verdict = MilestoneBuildResult(success=True, summary="approved")
    out = SimpleNamespace(pydantic=verdict, raw="ignored")
    assert CrewaiBuildCrew._extract(out) is verdict


def test_extract_parses_raw_json() -> None:
    out = _Raw(raw='{"success": false, "summary": "rejected", "failure_reason": "missing tests"}')
    verdict = CrewaiBuildCrew._extract(out)
    assert verdict is not None
    assert verdict.success is False
    assert verdict.failure_reason == "missing tests"


def test_extract_returns_none_for_unparseable_prose() -> None:
    assert CrewaiBuildCrew._extract(_Raw(raw="Looks good to me — ship it!")) is None


def test_extract_returns_none_for_non_mapping() -> None:
    # A repaired-but-non-dict payload (e.g. a bare list) isn't a verdict.
    assert CrewaiBuildCrew._extract(_Raw(raw="[1, 2, 3]")) is None


# ---------------------------------------------------------------------------
# _review — retry once on unparseable, then PASS
# ---------------------------------------------------------------------------


def test_review_returns_verdict_without_retry_when_first_parses() -> None:
    crew = _crew()
    verdict = MilestoneBuildResult(success=True, summary="approved")
    calls: list[int] = []

    def stub(_m: object, _b: str, _c: str) -> MilestoneBuildResult | None:
        calls.append(1)
        return verdict

    crew._run_reviewer = stub  # type: ignore[method-assign]
    result = _review(crew)
    assert len(calls) == 1  # no retry needed
    assert result is verdict


def test_review_retries_once_then_returns_parsed_verdict() -> None:
    crew = _crew()
    verdict = MilestoneBuildResult(success=False, summary="rejected", failure_reason="fix X")
    calls: list[int] = []

    def stub(_m: object, _b: str, _c: str) -> MilestoneBuildResult | None:
        calls.append(1)
        return None if len(calls) == 1 else verdict

    crew._run_reviewer = stub  # type: ignore[method-assign]
    result = _review(crew)
    assert len(calls) == 2  # one retry
    assert result is verdict


def test_review_passes_after_two_unparseable_attempts() -> None:
    crew = _crew()
    calls: list[int] = []

    def stub(_m: object, _b: str, _c: str) -> MilestoneBuildResult | None:
        calls.append(1)
        return None

    crew._run_reviewer = stub  # type: ignore[method-assign]
    result = _review(crew)
    assert len(calls) == 2  # initial attempt + exactly one retry, no more
    assert result.success is True
    assert "unparseable after a retry" in result.summary
