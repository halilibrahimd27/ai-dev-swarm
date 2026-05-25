# 0002. CrewAI for role coordination + custom orchestrator for state

* **Status**: Accepted
* **Date**: 2026-05-26
* **Deciders**: ai-dev-swarm maintainers

## Context and Problem Statement

A multi-agent dev swarm needs two distinct things: (1) per-task
LLM role coordination (planner ↔ architect ↔ developer ↔ tester ↔
reviewer dialogue) and (2) a durable, multi-day state machine
(`queued → planning → awaiting_approval → building → replanning →
integration → done` + the per-milestone graph). LangGraph couples
both into one runtime; CrewAI does only the first cleanly.

## Decision

Use **CrewAI** for the in-conversation role choreography and write
our **own orchestrator** for the durable state machine and the
per-project scheduler. The orchestrator owns Postgres, the kill
switch, the workspace lifecycle, and the replanner state; CrewAI
runs inside a single milestone build to negotiate Dev ↔ Tester ↔
Reviewer.

## Consequences

* **Positive** — Clear separation of concerns. The state machine is
  pure Python that survives container restarts (Postgres-backed);
  CrewAI handles the messy in-conversation LLM coordination. Tests
  can drive the orchestrator entirely with in-memory fakes (no
  CrewAI subprocess), which is what gives Phase 4's property +
  integration tests their speed.
* **Negative** — Two abstractions to learn. We re-implement some
  scheduling primitives LangGraph would give us (worker pool, fair
  share). The asyncio ↔ CrewAI threading boundary needs careful
  handling (see `EventBridge` Phase 5).
* **Neutral** — CrewAI's EventBus is the single source of truth
  for "what each role just said"; the orchestrator listens via
  `observability.event_bridge.EventBridge` (Phase 5).

## Alternatives considered

* **LangGraph** — one runtime owns everything, but the state machine
  + LLM coordination become entangled; testing the state machine
  in isolation gets harder.
* **Plain Claude Code in a loop** — no inter-role dialogue;
  single-agent fail mode.
* **AutoGen** — heavier than CrewAI for our role count; less
  ergonomic for the Critic-style negotiation we need.
