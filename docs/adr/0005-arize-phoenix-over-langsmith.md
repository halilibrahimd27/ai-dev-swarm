# 0005. Self-hosted Arize Phoenix over LangSmith for observability

* **Status**: Accepted
* **Date**: 2026-05-26
* **Deciders**: ai-dev-swarm maintainers

## Context and Problem Statement

We need a trace tree per agent step: CrewAI task → role agent →
SDK invocation → tool call → MCP call. The operator must be able
to see WHY a milestone failed and WHAT the LLMs were saying when
it failed. The system also runs locally on the operator's PC — a
cloud telemetry provider would mean every agent message leaves the
host.

## Decision

Run **Arize Phoenix** self-hosted via the `arizephoenix/phoenix`
Docker image, instrumented via **OpenInference** for CrewAI. Phoenix
gets traces over OTLP (`http://phoenix:6006/v1/traces`), stores
them in a local SQLite volume, and serves the UI at
`http://localhost:6006`. No data leaves the host.

## Consequences

* **Positive** — Free, self-hosted, OTLP-compatible (we can swap in
  Jaeger/Tempo later if we ever want). OpenInference's CrewAI
  instrumentation gives us the agent trace tree for free. Operator
  sees every LLM call, every tool call, every MCP call in the
  browser. The Phase 5 web UI's SSE transcript is a complementary,
  not redundant, surface — Phoenix has full history, SSE has live
  tail.
* **Negative** — One more container in the compose stack (~150 MB
  image). Phoenix's healthcheck is a bit fragile (uses a Python
  one-liner because the image ships without curl/wget).
* **Neutral** — Phoenix can be turned off via
  `AIDEVSWARM_PHOENIX_ENABLED=false`; the orchestrator runs fine
  without it (just no traces).

## Alternatives considered

* **LangSmith** — paid, requires sending data to LangChain Inc.,
  ties us to LangChain runtime conventions.
* **OpenTelemetry direct + Jaeger** — works, but we'd lose the
  CrewAI-aware UI Phoenix gives us.
* **Print to stdout** — useless for the trace-tree question.
