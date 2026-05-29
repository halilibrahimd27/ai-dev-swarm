"""End-to-end smoke test for the Phase 2 build crew.

Skipped unless ``ANTHROPIC_API_KEY`` is set in the environment so the
gauntlet stays green without burning operator budget. When run, it
drives a baked-in trivial milestone ("create a ``fizzbuzz`` module
with Hypothesis property tests") through the new SDK-powered build
crew end-to-end and asserts:

  1. The workspace contains ``fizzbuzz.py`` (Developer's output) and
     a ``tests/property/`` file authored by the Tester.
  2. ``milestone_sessions`` gains at least one Developer + one Tester
     row, so a future retry can ``resume=session_id``.
  3. A second build run on the same milestone resumes the Developer's
     session (verified by capturing the second invocation's
     ``ClaudeAgentOptions.resume``).

Operator workflow:

  export ANTHROPIC_API_KEY=sk-ant-...
  uv run pytest tests/integration/test_fizzbuzz_smoke.py -m anthropic -v

Phoenix at http://localhost:6006 will show a nested span tree:
CrewAI build task → sdk.developer / sdk.tester → MCP calls.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from aidevswarm.db.sessions import PsycopgMilestoneSessionRepo
from aidevswarm.schemas import AcceptanceCriterion, Milestone, MilestoneSpec
from aidevswarm.settings import Settings
from aidevswarm.tools.claude_agent_sdk_tool import (
    ClaudeAgentSDKDeveloperTool,
    ClaudeAgentSDKTesterTool,
)
from aidevswarm.tools.mcp_config import load_mcp_servers
from aidevswarm.tools.workspace import Workspace

# ``live_pool`` comes from tests/integration/conftest.py (isolated test DB).

pytestmark = [
    pytest.mark.anthropic,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set — skipping live SDK smoke",
    ),
]


@pytest.fixture
def fizzbuzz_milestone(live_pool: ConnectionPool) -> Iterator[Milestone]:
    """Insert a throwaway project + milestone, clean up after the test."""
    pid = uuid4()
    mid = uuid4()
    spec = MilestoneSpec(
        title="fizzbuzz",
        description=(
            "Create a `fizzbuzz` module exposing `fizzbuzz(n: int) -> str` "
            "that returns 'FizzBuzz' iff n % 15 == 0, 'Fizz' iff n % 3 == 0, "
            "'Buzz' iff n % 5 == 0, str(n) otherwise. Then write Hypothesis "
            "property tests under tests/property/test_fizzbuzz.py."
        ),
        acceptance_criteria=[
            AcceptanceCriterion(description="pytest passes", verifier="pytest"),
            AcceptanceCriterion(description="ruff/mypy clean", verifier="lint"),
        ],
        technical_note="prefer stdlib only; no extra deps.",
    )
    project_spec_payload = {
        "title": "fizzbuzz-smoke",
        "summary": "smoke",
        "rationale": "smoke",
        "stack": ["python"],
        "tags": ["smoke"],
        "score": 80,
    }
    with live_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (id, name, spec, state) VALUES (%s, %s, %s, 'queued')",
            (str(pid), f"fizzbuzz-smoke-{pid}", Json(project_spec_payload)),
        )
        cur.execute(
            """
            INSERT INTO milestones (id, project_id, ordinal, title, spec, state)
            VALUES (%s, %s, 0, %s, %s, 'pending')
            """,
            (str(mid), str(pid), spec.title, Json(spec.model_dump())),
        )
    yield Milestone(id=mid, project_id=pid, ordinal=0, title=spec.title, spec=spec)
    with live_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id = %s", (str(pid),))


def _session_count(pool: ConnectionPool, milestone_id: UUID, role: str) -> int:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM milestone_sessions WHERE milestone_id = %s AND role = %s",
            (str(milestone_id), role),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def test_developer_then_tester_then_workspace_has_module(
    tmp_path: Path,
    live_pool: ConnectionPool,
    fizzbuzz_milestone: Milestone,
) -> None:
    """Full SDK round-trip: Developer writes fizzbuzz, Tester writes property tests."""
    settings = Settings()
    session_repo = PsycopgMilestoneSessionRepo(live_pool)
    mcp = load_mcp_servers()

    ws = Workspace(tmp_path / fizzbuzz_milestone.title)
    ws.init()

    dev = ClaudeAgentSDKDeveloperTool(settings, session_repo, mcp_servers=mcp)
    dev_result = dev.run_sync(fizzbuzz_milestone, ws, max_turns=20, max_budget_usd=1.5)
    assert dev_result.success, f"Developer failed: {dev_result.failure_reason}"
    assert _session_count(live_pool, fizzbuzz_milestone.id, "Developer") >= 1

    tester = ClaudeAgentSDKTesterTool(settings, session_repo, mcp_servers=mcp)
    test_result = tester.run_sync(fizzbuzz_milestone, ws, max_turns=20, max_budget_usd=1.5)
    assert test_result.success, f"Tester failed: {test_result.failure_reason}"
    assert _session_count(live_pool, fizzbuzz_milestone.id, "Tester") >= 1

    # The Developer should have produced the production module.
    fizzbuzz_files = list(ws.root.rglob("fizzbuzz.py"))
    assert fizzbuzz_files, "Developer didn't create a fizzbuzz.py anywhere in the workspace"
    # The Tester should have produced at least one property test.
    property_tests = (
        list((ws.root / "tests").rglob("test_*.py")) if (ws.root / "tests").is_dir() else []
    )
    assert property_tests, "Tester didn't write any tests/ files"


def test_second_developer_run_resumes_first_session(
    tmp_path: Path,
    live_pool: ConnectionPool,
    fizzbuzz_milestone: Milestone,
) -> None:
    """Two consecutive Developer runs for the same milestone must resume."""
    settings = Settings()
    session_repo = PsycopgMilestoneSessionRepo(live_pool)
    mcp = load_mcp_servers()

    ws = Workspace(tmp_path / fizzbuzz_milestone.title)
    ws.init()

    dev = ClaudeAgentSDKDeveloperTool(settings, session_repo, mcp_servers=mcp)
    first = dev.run_sync(fizzbuzz_milestone, ws, max_turns=10, max_budget_usd=1.0)
    assert first.success, f"First Developer run failed: {first.failure_reason}"

    captured_resume: list[str | None] = []
    real_build_options = dev.build_options

    def spy_build_options(*args: object, **kwargs: object) -> object:
        captured_resume.append(kwargs.get("resume"))  # type: ignore[arg-type]
        return real_build_options(*args, **kwargs)  # type: ignore[arg-type]

    dev.build_options = spy_build_options  # type: ignore[assignment, method-assign]
    try:
        second = dev.run_sync(fizzbuzz_milestone, ws, max_turns=5, max_budget_usd=0.5)
    finally:
        dev.build_options = real_build_options  # type: ignore[method-assign]

    assert second.success or second.failure_reason  # smoke: it completed
    assert captured_resume, "build_options was never invoked on the second run"
    assert captured_resume[0] == first.session_id, (
        f"Second run did not resume the first session (resume={captured_resume[0]!r}"
        f" first.session_id={first.session_id!r})"
    )
