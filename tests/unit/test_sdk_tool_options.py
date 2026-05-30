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
    # The Tester may also run ruff to lint its own test files before handoff.
    assert "Bash(ruff:*)" in opts.allowed_tools
    assert "Bash" not in opts.allowed_tools


def test_task_prompt_repair_context_targets_ci_errors() -> None:
    """A repair invocation tells the agent to fix the exact CI errors."""
    repo = FakeMilestoneSessionRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo)
    ms = _milestone()
    plain = tool.task_prompt(ms)
    assert "CI gate just FAILED" not in plain
    repair = tool.task_prompt(ms, "ruff: F401 'mod.unused' imported but unused")
    assert "CI gate just FAILED" in repair
    assert "F401" in repair


def test_task_prompt_resumed_tells_agent_to_continue() -> None:
    """A re-attempt (resumed session) continues partial work, not restart."""
    repo = FakeMilestoneSessionRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo)
    ms = _milestone()
    fresh = tool.task_prompt(ms, resumed=False)
    assert "PARTIAL prior attempt" not in fresh
    resumed = tool.task_prompt(ms, resumed=True)
    assert "PARTIAL prior attempt" in resumed
    assert "CONTINUE" in resumed


def test_role_model_tiering(tmp_path: Path) -> None:
    """Developer builds on the cheap dev model first, escalates to strong on a
    retry; Tester always runs on the fast model (cost optimisation)."""
    settings = Settings(
        AIDEVSWARM_MODEL_STRONG="anthropic/claude-opus-4-7",
        AIDEVSWARM_MODEL_FAST="anthropic/claude-haiku-4-5",
        AIDEVSWARM_MODEL_DEV="anthropic/claude-sonnet-4-6",
    )
    repo = FakeMilestoneSessionRepo()
    ms = _milestone()  # retry_count == 0 (first attempt)
    ws = Workspace(tmp_path / "ws")
    ws.init()
    dev = ClaudeAgentSDKDeveloperTool(settings, repo)
    tester = ClaudeAgentSDKTesterTool(settings, repo)
    dev_opts = dev.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    tester_opts = tester.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    # SDK/CLI gets a BARE model id — the LiteLLM "anthropic/" prefix is stripped.
    assert dev_opts.model == "claude-sonnet-4-6"  # cheap first attempt
    assert tester_opts.model == "claude-haiku-4-5"

    # A retried milestone escalates the Developer to the strong model.
    retried = ms.model_copy(update={"retry_count": 1})
    escalated = dev.build_options(retried, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    assert escalated.model == "claude-opus-4-7"


def test_build_options_disables_claude_co_author(tmp_path: Path) -> None:
    """Generated commits must NOT carry a Claude co-author trailer."""
    import json

    repo = FakeMilestoneSessionRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo)
    ms = _milestone()
    ws = Workspace(tmp_path / "ws")
    ws.init()
    opts = tool.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    assert opts.settings is not None
    assert json.loads(opts.settings) == {"includeCoAuthoredBy": False}


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


def test_no_steering_repo_means_no_pretooluse_hook(tmp_path: Path) -> None:
    """Phase 5 invariant: callers without a SteeringRepo pay zero hook cost."""
    repo = FakeMilestoneSessionRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo)  # no steering_repo
    ms = _milestone()
    ws = Workspace(tmp_path / "ws")
    ws.init()
    opts = tool.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    assert opts.hooks is None or opts.hooks == {}


def test_with_steering_repo_installs_pretooluse_hook(tmp_path: Path) -> None:
    """When a SteeringRepo is wired, build_options installs the PreToolUse hook."""
    repo = FakeMilestoneSessionRepo()
    steering = FakeSteeringRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo, steering_repo=steering)
    ms = _milestone()
    ws = Workspace(tmp_path / "ws")
    ws.init()
    opts = tool.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    assert opts.hooks is not None
    pretool = opts.hooks.get("PreToolUse")
    assert pretool is not None
    assert len(pretool) == 1
    assert len(pretool[0].hooks) == 1


@pytest.mark.asyncio
async def test_pretooluse_hook_injects_pending_steering_notes(tmp_path: Path) -> None:
    """Mid-flight steering: the hook surfaces newly-queued notes."""
    repo = FakeMilestoneSessionRepo()
    steering = FakeSteeringRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), repo, steering_repo=steering)
    ms = _milestone()
    ws = Workspace(tmp_path / "ws")
    ws.init()

    opts = tool.build_options(ms, ws, max_turns=10, max_budget_usd=1.0, resume=None)
    assert opts.hooks is not None
    callback = opts.hooks["PreToolUse"][0].hooks[0]

    # No notes yet -> empty output.
    out_empty = await callback({}, None, None)
    assert out_empty == {}

    # Drop a note (the operator types it in the web UI mid-flight)
    # and assert the next PreToolUse call surfaces it as a
    # systemMessage. NOTE: build_options() pulled the steering pipe
    # once at session start; the hook handles the case AFTER that.
    steering.add_note(ms.project_id, "watch for off-by-one")

    out = await callback({}, None, None)
    assert "watch for off-by-one" in out.get("systemMessage", "")

    # A second call consumes nothing (the note was atomically marked
    # consumed on the first call).
    out_after = await callback({}, None, None)
    assert out_after == {}


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


# ---------------------------------------------------------------------------
# _arun (SDK client mocked) — session persistence + spend recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arun_persists_session_and_records_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from claude_agent_sdk import ResultMessage

    from aidevswarm.tools import claude_agent_sdk_tool as mod
    from aidevswarm.tools.budget import SpendRecorder
    from tests.fakes import InMemoryTokenLogRepo

    final = ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=False,
        num_turns=4,
        session_id="sess-1",
        total_cost_usd=0.12,
        result="done",
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 15,
        },
    )

    class _FakeClient:
        def __init__(self, options: object = None) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

        async def query(self, _prompt: str) -> None:
            return None

        async def receive_messages(self):  # type: ignore[no-untyped-def]
            yield final

    monkeypatch.setattr(mod, "ClaudeSDKClient", _FakeClient)

    token_repo = InMemoryTokenLogRepo()
    session_repo = FakeMilestoneSessionRepo()
    tool = ClaudeAgentSDKDeveloperTool(Settings(), session_repo, recorder=SpendRecorder(token_repo))
    ms = _milestone()
    ws = Workspace(tmp_path / "ws")
    ws.init()

    result = await tool._arun(ms, ws, max_turns=10, max_budget_usd=1.0)
    assert result.success is True
    assert result.session_id == "sess-1"
    # Session row persisted (so a retry can resume).
    assert session_repo.latest_for(ms.id, "Developer") is not None
    # Spend recorded: input counts cache reads, output is output_tokens,
    # cost is the SDK's exact total_cost_usd.
    assert len(token_repo.records) == 1
    rec = token_repo.records[0]
    assert rec["input_tokens"] == 115  # input(100) + cache_creation(15); cache_read excluded
    assert rec["output_tokens"] == 50
    assert rec["cost_usd"] == 0.12
    assert rec["role"] == "Developer"


@pytest.mark.asyncio
async def test_arun_streams_transcript_to_publisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Developer/Tester turns are fanned out to the live transcript."""
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    from aidevswarm.observability import TranscriptEntry
    from aidevswarm.tools import claude_agent_sdk_tool as mod

    assistant = AssistantMessage(
        content=[
            TextBlock(text="Implementing fizzbuzz now."),
            ToolUseBlock(id="t1", name="Write", input={"file_path": "fizz.py"}),
        ],
        model="claude-opus-4-7",
    )
    user = UserMessage(
        content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)],
    )
    final = ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=False,
        num_turns=2,
        session_id="sess-x",
        total_cost_usd=0.05,
        result="done",
    )

    class _FakeClient:
        def __init__(self, options: object = None) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

        async def query(self, _prompt: str) -> None:
            return None

        async def receive_messages(self):  # type: ignore[no-untyped-def]
            yield assistant
            yield user
            yield final

    monkeypatch.setattr(mod, "ClaudeSDKClient", _FakeClient)

    captured: list[TranscriptEntry] = []

    class _Sink:
        def publish(self, entry: TranscriptEntry) -> None:
            captured.append(entry)

    tool = ClaudeAgentSDKDeveloperTool(Settings(), FakeMilestoneSessionRepo(), transcript=_Sink())
    ms = _milestone()
    ws = Workspace(tmp_path / "ws-t")
    ws.init()

    await tool._arun(ms, ws, max_turns=10, max_budget_usd=1.0)

    kinds = [e.kind for e in captured]
    assert "assistant" in kinds
    assert "tool_use" in kinds
    assert "tool_result" in kinds
    # Every entry is tagged with the project + role so the per-project
    # SSE filter routes it to the right transcript pane.
    assert all(e.project_id == ms.project_id for e in captured)
    assert all(e.role == "Developer" for e in captured)
    assert all(e.topic == "transcript" for e in captured)


@pytest.mark.asyncio
async def test_arun_handles_stream_with_no_result_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aidevswarm.tools import claude_agent_sdk_tool as mod

    class _EmptyClient:
        def __init__(self, options: object = None) -> None:
            pass

        async def __aenter__(self) -> _EmptyClient:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

        async def query(self, _prompt: str) -> None:
            return None

        async def receive_messages(self):  # type: ignore[no-untyped-def]
            return
            yield  # pragma: no cover — makes this an (empty) async generator

    monkeypatch.setattr(mod, "ClaudeSDKClient", _EmptyClient)

    tool = ClaudeAgentSDKDeveloperTool(Settings(), FakeMilestoneSessionRepo())
    ms = _milestone()
    ws = Workspace(tmp_path / "ws2")
    ws.init()
    result = await tool._arun(ms, ws, max_turns=10, max_budget_usd=1.0)
    assert result.success is False
    assert result.failure_reason == "no_result_message"
