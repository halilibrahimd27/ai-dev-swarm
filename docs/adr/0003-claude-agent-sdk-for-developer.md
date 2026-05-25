# 0003. Use the Claude Agent SDK for the Developer + Tester roles

* **Status**: Accepted
* **Date**: 2026-05-26
* **Deciders**: ai-dev-swarm maintainers

## Context and Problem Statement

The Developer and Tester roles must do real coding (Read, Write,
Edit, Glob, Grep, Bash, MCP servers like tree-sitter) inside a
persistent workspace, and the session must survive day-to-day
budget caps so multi-day projects don't lose state. Doing this
with the plain Anthropic API meant reinventing tool routing,
session resume, budget enforcement, and permission gates.

## Decision

Wrap **claude-agent-sdk** (Python, `claude_agent_sdk.ClaudeSDKClient`)
as a CrewAI tool. Every Developer/Tester invocation:

* sets `cwd` to the project's persistent git workspace,
* runs with `permission_mode="acceptEdits"` (NEVER
  `bypassPermissions`),
* caps `max_turns` + `max_budget_usd` per call,
* on retry passes `resume=<session_id>` from
  `milestone_sessions` (Phase 2 table) so the SDK continues the
  prior conversation instead of restarting,
* installs a `PreToolUseHookInput` hook (Phase 5) that pulls
  pending operator steering notes before each tool call.

## Consequences

* **Positive** — Real coding loop without writing one. Built-in
  tool routing, session resume, budget enforcement. Anthropic ships
  fixes upstream. Tree-sitter MCP wires cleanly via
  `ClaudeAgentOptions.mcp_servers`. The `PreToolUse` hook is the
  Phase 5 steering mechanism.
* **Negative** — Bound to Anthropic's API + Anthropic-shaped tools
  (no easy OpenAI fallback for the Developer role). The SDK 0.2.87
  has no `stream_input` — we use `hooks` for mid-flight injection
  instead, which is strictly more conservative (no conversation
  fork).
* **Neutral** — `claude-agent-sdk` versions move fast. Pin in
  `pyproject.toml`; revisit each minor.

## Alternatives considered

* **Raw Anthropic Messages API** — re-implement session resume,
  tool routing, budget — months of work for solved problems.
* **Cursor / Aider as subprocess** — UX shells, not embeddable as
  CrewAI tools.
* **Code-interpreter style sandbox** — no persistent workspace,
  no resume semantics.
