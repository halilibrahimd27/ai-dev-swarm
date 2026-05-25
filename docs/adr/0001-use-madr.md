# 0001. Use MADR for Architecture Decision Records

* **Status**: Accepted
* **Date**: 2026-05-26
* **Deciders**: ai-dev-swarm maintainers

## Context and Problem Statement

The orchestrator was built over six phases with many non-obvious
choices (CrewAI + custom orchestrator instead of LangGraph,
Claude Agent SDK for the Developer role, psycopg3 over psycopg2,
self-hosted Arize Phoenix, milestone-graph state machine, etc).
Future maintainers need the "why" to land safely.

## Decision

Use the MADR short form (https://adr.github.io/madr) and keep
records under `docs/adr/NNNN-<slug>.md`. Each record has Context,
Decision, Consequences, Alternatives — kept to ~30 lines so people
actually read them.

## Consequences

* **Positive** — Low-friction format; reviewers see the decision
  in 30 seconds; new entries fit on one screen.
* **Negative** — No formal review workflow (we don't gate merges
  on an ADR being filed); the team has to remember to write one.
* **Neutral** — Each ADR gets a sequential number; numbers are
  never re-used even if the ADR is superseded.

## Alternatives considered

* **arc42 ADR style** — too verbose for a one-engineer project.
* **No ADRs, rely on git log** — git messages decay; consequences
  + alternatives don't survive `--squash`.
* **Comments in `docs/ARCHITECTURE.md`** — that file is gitignored
  (build kit), so it can't carry public-facing architecture.
