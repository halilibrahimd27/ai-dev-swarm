"""Unit tests for the SDK tool's option assembly + result parsing.

These tests do NOT invoke the SDK subprocess (no Anthropic key
needed) — they exercise ``build_options``, ``_result_from``, and the
deliver-once steering-note wiring.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from aidevswarm.schemas import AcceptanceCriterion, Milestone, MilestoneSpec
from aidevswarm.settings import Settings
from aidevswarm.tools.claude_agent_sdk_tool import (
    _DISALLOWED_TOOLS,
    ClaudeAgentSDKDeveloperTool,
    ClaudeAgentSDKTesterTool,
    SDKResult,
    _result_from,
)
from aidevswarm.tools.workspace import Workspace
from tests.fakes import FakeMilestoneSessionRepo, FakeSteeringRepo


def _milestone() -> Milestone:
    return Milestone(
        project_id=uuid4(),
        ordinal=0,
        title="add fizzbuzz",
        spec=MilestoneSpec(
            title="add fizzbuzz",
            description="implement fizzbuzz(n)",
            acceptance_criteria=[
                AcceptanceCriterion(description="pytest passes", verifier="pytest")
            ],
        ),
    )


def test_developer_tool_allowed_tools_and_disallowed(tmp_path: Path) -> None:
    repo = FakeMilestoneSessionRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo)
    ms = _milestone()
    ws = Workspace(tmp_path / ms.title)
    ws.init()
    opts = tool.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    assert opts.allowed_tools == ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
    assert set(opts.disallowed_tools) == set(_DISALLOWED_TOOLS)
    assert opts.permission_mode == "acceptEdits"
    assert opts.max_turns == 10
    assert opts.max_budget_usd == 1.0
    assert opts.resume is None
    assert opts.cwd == str(ws.root)


def test_tester_tool_bash_is_pytest_namespaced(tmp_path: Path) -> None:
    repo = FakeMilestoneSessionRepo()
    tool = ClaudeAgentSDKTesterTool(Settings(), repo)
    ms = _milestone()
    ws = Workspace(tmp_path / "ws")
    ws.init()
    opts = tool.build_options(ms, ws, max_turns=5, max_budget_usd=0.5, resume=None)
    assert "Bash(pytest:*)" in opts.allowed_tools
    assert "Bash" not in opts.allowed_tools


def test_resume_threads_through_options(tmp_path: Path) -> None:
    repo = FakeMilestoneSessionRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo)
    ms = _milestone()
    ws = Workspace(tmp_path / "ws")
    ws.init()
    opts = tool.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume="prev-session-id")
    assert opts.resume == "prev-session-id"


def test_steering_notes_land_in_system_prompt(tmp_path: Path) -> None:
    repo = FakeMilestoneSessionRepo()
    steering = FakeSteeringRepo()
    ms = _milestone()
    steering.add_note(ms.project_id, "prefer dataclasses")
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo, steering_repo=steering)
    ws = Workspace(tmp_path / "ws")
    ws.init()
    opts = tool.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    assert isinstance(opts.system_prompt, str)
    assert "prefer dataclasses" in opts.system_prompt
    assert "## Steering notes from the operator" in opts.system_prompt


def test_steering_notes_are_role_consumed(tmp_path: Path) -> None:
    """A Developer pull marks the note consumed; the Tester sees nothing."""
    session_repo = FakeMilestoneSessionRepo()
    steering = FakeSteeringRepo()
    ms = _milestone()
    steering.add_note(ms.project_id, "watch the bounds")
    dev = ClaudeAgentSDKDeveloperTool(Settings(), session_repo, steering_repo=steering)
    tester = ClaudeAgentSDKTesterTool(Settings(), session_repo, steering_repo=steering)
    ws = Workspace(tmp_path / "ws")
    ws.init()
    dev_opts = dev.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    test_opts = tester.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    assert "watch the bounds" in str(dev_opts.system_prompt)
    assert "watch the bounds" not in str(test_opts.system_prompt)


def test_task_prompt_includes_milestone_title_and_spec() -> None:
    repo = FakeMilestoneSessionRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo)
    ms = _milestone()
    prompt = tool.task_prompt(ms)
    assert "add fizzbuzz" in prompt
    assert "pytest passes" in prompt


def test_result_from_success_path() -> None:
    from claude_agent_sdk import ResultMessage

    final = ResultMessage(
        subtype="success",
        duration_ms=1234,
        duration_api_ms=900,
        is_error=False,
        num_turns=3,
        session_id="sess-ok",
        total_cost_usd=0.07,
        result="done",
    )
    out = _result_from(final)
    assert out.success is True
    assert out.session_id == "sess-ok"
    assert out.cost_usd == 0.07
    assert out.turns == 3
    assert out.summary == "done"
    assert out.failure_reason is None


def test_result_from_error_path() -> None:
    from claude_agent_sdk import ResultMessage

    final = ResultMessage(
        subtype="error_max_budget_usd",
        duration_ms=1234,
        duration_api_ms=900,
        is_error=True,
        num_turns=10,
        session_id="sess-err",
        stop_reason="max_budget",
        errors=["budget exceeded"],
    )
    out = _result_from(final)
    assert out.success is False
    assert out.session_id == "sess-err"
    assert out.failure_reason is not None
    assert "max_budget" in out.failure_reason
    assert "error_max_budget_usd" in out.failure_reason


def test_sdkresult_is_a_frozen_dataclass() -> None:
    """Defensive: SDKResult must be immutable so callers can't mutate it."""
    from dataclasses import FrozenInstanceError

    r = SDKResult(success=True, session_id="x", cost_usd=0.0, turns=0, summary="")
    with pytest.raises(FrozenInstanceError):
        r.success = False  # type: ignore[misc]


def test_mcp_servers_thread_through_options(tmp_path: Path) -> None:
    """The mcp_servers dict passed at __init__ time lands in ClaudeAgentOptions."""
    repo = FakeMilestoneSessionRepo()
    mcp = {
        "tree-sitter": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@nendo/tree-sitter-mcp", "--mcp"],
        }
    }
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo, mcp_servers=mcp)  # type: ignore[arg-type]
    ms = _milestone()
    ws = Workspace(tmp_path / "ws")
    ws.init()
    opts = tool.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    assert isinstance(opts.mcp_servers, dict)
    assert "tree-sitter" in opts.mcp_servers


def test_developer_and_tester_have_template_files() -> None:
    """Constructing the tool loads the template; if the file is missing
    the constructor would raise — so this also guards the prompts/
    deployment shape."""
    repo = FakeMilestoneSessionRepo()
    ClaudeAgentSDKDeveloperTool(Settings(), repo)
    ClaudeAgentSDKTesterTool(Settings(), repo)
