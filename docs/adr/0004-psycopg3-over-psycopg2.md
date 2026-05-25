# 0004. psycopg3 (+ psycopg_pool.ConnectionPool) over psycopg2

* **Status**: Accepted
* **Date**: 2026-05-26
* **Deciders**: ai-dev-swarm maintainers

## Context and Problem Statement

The orchestrator is a 24/7 multi-threaded process: a CrewAI worker
thread runs the build, an asyncio loop runs the FastAPI server,
Phase 4 introduced a `ProjectPool` of N worker coroutines. A
module-level shared `psycopg2.connection` would serialise all DB
work on one connection's GIL-bound lock; under load it deadlocks.

## Decision

Use **psycopg 3** (`psycopg[binary,pool]`) with a
**`psycopg_pool.ConnectionPool`**. The pool is created once at
startup (Phase 1) and injected into every repository; no module-
level connection state exists. Repositories check connections out
via `with pool.connection() as conn:` and immediately return them.

## Consequences

* **Positive** — Thread-safe by construction (pool hands out
  per-thread connections). Async-friendly: psycopg 3 supports async
  if we need it later. pgvector + pgcrypto + JSONB just work. The
  upstream API is cleaner (`%s` everywhere — no `?` vs `:name`
  inconsistencies).
* **Negative** — `psycopg3` and `psycopg2` are NOT API-compatible;
  any old recipes from the internet must be translated. The
  binary wheel is larger than psycopg2-binary.
* **Neutral** — `CLAUDE.md` explicitly forbids `psycopg2` and
  module-level shared connections; this ADR is the contract that
  rule encodes.

## Alternatives considered

* **psycopg2 + threading.Lock** — works at low load, melts under
  Phase 4's `ProjectPool`. Re-tested in Phase 1 with the deliberate
  intent of avoiding this trap.
* **SQLAlchemy ORM** — too much abstraction for the small surface
  we have; we already write SQL deliberately.
* **asyncpg** — async-only API, awkward for the parts of the
  orchestrator that are sync today (CrewAI tools, repos).
