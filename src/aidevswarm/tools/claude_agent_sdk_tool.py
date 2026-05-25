"""Claude Agent SDK tool — the real coding loop for Phase 2+.

Wraps :class:`claude_agent_sdk.ClaudeSDKClient` so the Developer and
Tester roles in the build crew become SDK invocations instead of
direct LLM prompts. Every call:

  * Sets ``cwd`` to the project's persistent git workspace.
  * Uses ``permission_mode="acceptEdits"`` (NEVER ``bypassPermissions``).
  * Caps ``max_turns`` and ``max_budget_usd`` (the SDK aborts the
    query with an ``error_max_budget_usd`` ``ResultMessage`` when the
    budget is exceeded).
  * On retry, passes ``resume=<session_id>`` pulled from
    :class:`MilestoneSessionRepo.latest_for(milestone_id, role)`.
  * Persists the closing ``ResultMessage`` to ``milestone_sessions``.
  * Emits an OTEL span ``sdk.<role>`` tagged with project/milestone/
    role/model/session/cost/turns so Phoenix shows the full trace
    tree (CrewAI task → sdk.<role> → SDK tool calls → MCP calls).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
)
from claude_agent_sdk.types import McpStdioServerConfig

from aidevswarm.db.sessions import MilestoneSessionRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.observability import get_tracer
from aidevswarm.schemas import Milestone
from aidevswarm.settings import Settings
from aidevswarm.steering import SteeringRepo, render_prompt
from aidevswarm.tools.workspace import Workspace

_SDK_PROMPT_DIR = Path(__file__).resolve().parent / "sdk_prompts"

# Tools the SDK is forbidden from invoking. Both roles are sandboxed
# off the open internet — operator-driven steering may relax this.
_DISALLOWED_TOOLS = ("WebFetch", "WebSearch")


@dataclass(frozen=True)
class SDKResult:
    """Outcome of one SDK invocation."""

    success: bool
    session_id: str
    cost_usd: float
    turns: int
    summary: str
    failure_reason: str | None = None


class ClaudeAgentSDKTool:
    """Base SDK invocation tool. Subclasses set ``role``/``allowed_tools``/``_template_name``."""

    role: ClassVar[str] = ""
    allowed_tools: ClassVar[tuple[str, ...]] = ()
    _template_name: ClassVar[str] = ""

    # Defaults; per-call override via run_sync(max_turns=..., max_budget_usd=...).
    default_max_turns: ClassVar[int] = 40
    default_max_budget_usd: ClassVar[float] = 2.0

    def __init__(
        self,
        settings: Settings,
        session_repo: MilestoneSessionRepo,
        *,
        steering_repo: SteeringRepo | None = None,
        mcp_servers: dict[str, McpStdioServerConfig] | None = None,
    ) -> None:
        self._settings = settings
        self._session_repo = session_repo
        self._steering = steering_repo
        self._mcp_servers: dict[str, McpStdioServerConfig] = mcp_servers or {}
        self._log = get_logger(__name__)
        self._template = (_SDK_PROMPT_DIR / f"{self._template_name}.txt").read_text("utf-8")

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def run_sync(
        self,
        milestone: Milestone,
        workspace: Workspace,
        *,
        max_turns: int | None = None,
        max_budget_usd: float | None = None,
    ) -> SDKResult:
        """Drive the SDK to completion and persist the session row."""
        return asyncio.run(
            self._arun(
                milestone,
                workspace,
                max_turns=max_turns or self.default_max_turns,
                max_budget_usd=max_budget_usd or self.default_max_budget_usd,
            )
        )

    # ------------------------------------------------------------------
    # Internals — also useful directly from async tests
    # ------------------------------------------------------------------

    def build_options(
        self,
        milestone: Milestone,
        workspace: Workspace,
        *,
        max_turns: int,
        max_budget_usd: float,
        resume: str | None,
    ) -> ClaudeAgentOptions:
        """Assemble :class:`ClaudeAgentOptions` for one invocation.

        Split out from ``_arun`` so unit tests can assert the option
        shape without invoking the SDK subprocess.
        """
        steering_notes = self._pull_notes(milestone.project_id)
        system_prompt = render_prompt(self._template, steering_notes=steering_notes)
        return ClaudeAgentOptions(
            cwd=str(workspace.root),
            system_prompt=system_prompt,
            allowed_tools=list(self.allowed_tools),
            disallowed_tools=list(_DISALLOWED_TOOLS),
            permission_mode="acceptEdits",
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            resume=resume,
            model=self._settings.model_strong,
            mcp_servers=dict(self._mcp_servers),
            hooks=self._build_hooks(milestone.project_id),
        )

    def _build_hooks(
        self,
        project_id: UUID,
    ) -> dict[Any, list[HookMatcher]] | None:
        """Install a PreToolUse hook that injects pending steering notes.

        Phase 5 Mandate 3: the operator can drop a note in the web UI
        while the SDK is running. The note goes to ``steering_notes``;
        on the SDK's NEXT tool call this hook pulls any unconsumed
        notes and surfaces them as a ``systemMessage`` — the agent
        reads them on the very next turn without restarting.

        Returns ``None`` if no steering repo is wired (Phase 0-4
        callers don't pay any overhead).
        """
        steering = self._steering
        if steering is None:
            return None
        role = self.role

        async def _inject(
            _input: Any,
            _tool_use_id: str | None,
            _context: Any,
        ) -> dict[str, Any]:
            notes = await asyncio.to_thread(steering.pull_unconsumed, project_id, role)
            if not notes:
                return {}
            joined = "\n".join(f"- {n}" for n in notes)
            return {
                "systemMessage": (
                    "## Mid-flight steering from the operator\n"
                    f"{joined}\n"
                    "Apply these on your next step."
                )
            }

        # PreToolUse fires before EACH tool call — the matcher with
        # no pattern means "all tools". (claude-agent-sdk 0.2.87)
        return {"PreToolUse": [HookMatcher(hooks=[_inject])]}  # type: ignore[list-item]

    def task_prompt(self, milestone: Milestone) -> str:
        """The user-facing prompt the SDK receives as the first turn."""
        return (
            f"Milestone: {milestone.title}\n"
            f"Acceptance criteria + technical note:\n"
            f"{milestone.spec.model_dump_json(indent=2)}\n\n"
            "Work in the current directory; the previous milestones' code"
            " is already on disk. Stop when the milestone's acceptance"
            " criteria are met."
        )

    async def _arun(
        self,
        milestone: Milestone,
        workspace: Workspace,
        *,
        max_turns: int,
        max_budget_usd: float,
    ) -> SDKResult:
        prev = self._session_repo.latest_for(milestone.id, self.role)
        resume = prev.session_id if prev else None
        options = self.build_options(
            milestone,
            workspace,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            resume=resume,
        )

        tracer = get_tracer()
        with tracer.start_as_current_span(f"sdk.{self.role.lower()}") as span:
            span.set_attribute("aidevswarm.project_id", str(milestone.project_id))
            span.set_attribute("aidevswarm.milestone_id", str(milestone.id))
            span.set_attribute("aidevswarm.role", self.role)
            span.set_attribute("aidevswarm.model", self._settings.model_strong)
            if resume is not None:
                span.set_attribute("aidevswarm.resume_session_id", resume)

            final: ResultMessage | None = None
            async with ClaudeSDKClient(options=options) as client:
                await client.query(self.task_prompt(milestone))
                async for msg in client.receive_messages():
                    if isinstance(msg, ResultMessage):
                        final = msg
                        break

            if final is None:
                # Stream closed before a ResultMessage — treat as failure.
                span.set_attribute("aidevswarm.error", "no_result_message")
                return SDKResult(
                    success=False,
                    session_id=resume or "",
                    cost_usd=0.0,
                    turns=0,
                    summary="SDK closed without a ResultMessage",
                    failure_reason="no_result_message",
                )

            span.set_attribute("aidevswarm.session_id", final.session_id)
            span.set_attribute("aidevswarm.cost_usd", float(final.total_cost_usd or 0.0))
            span.set_attribute("aidevswarm.num_turns", int(final.num_turns))

            # Persist the row even on error so retries can resume.
            self._session_repo.record(
                milestone_id=milestone.id,
                role=self.role,
                session_id=final.session_id,
                cost_usd=float(final.total_cost_usd or 0.0),
                turns=int(final.num_turns),
            )

            return _result_from(final)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pull_notes(self, project_id: UUID) -> Sequence[str]:
        if self._steering is None:
            return []
        return self._steering.pull_unconsumed(project_id, self.role)


class ClaudeAgentSDKDeveloperTool(ClaudeAgentSDKTool):
    """Developer role: writes the actual milestone code."""

    role = "Developer"
    allowed_tools = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")
    _template_name = "developer"


class ClaudeAgentSDKTesterTool(ClaudeAgentSDKTool):
    """Tester role: writes Hypothesis property tests."""

    role = "Tester"
    # Bash is namespace-restricted so the Tester can run pytest but
    # not arbitrary shell.
    allowed_tools = ("Read", "Write", "Edit", "Glob", "Grep", "Bash(pytest:*)")
    _template_name = "tester"


# ----------------------------------------------------------------------
# Helpers used by tests
# ----------------------------------------------------------------------


def _result_from(final: ResultMessage) -> SDKResult:
    """Build an :class:`SDKResult` from the closing ``ResultMessage``."""
    failure: str | None = None
    if final.is_error:
        bits: list[str] = []
        if final.stop_reason:
            bits.append(f"stop_reason={final.stop_reason}")
        if final.errors:
            bits.append("errors=" + "; ".join(map(str, final.errors)))
        bits.append(f"subtype={final.subtype}")
        failure = " | ".join(bits)
    summary = final.result or final.stop_reason or final.subtype
    return SDKResult(
        success=not final.is_error,
        session_id=final.session_id,
        cost_usd=float(final.total_cost_usd or 0.0),
        turns=int(final.num_turns),
        summary=summary or "",
        failure_reason=failure,
    )
