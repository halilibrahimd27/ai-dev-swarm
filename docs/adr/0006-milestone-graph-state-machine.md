# 0006. Milestone-graph state machine as the multi-day work primitive

* **Status**: Accepted
* **Date**: 2026-05-26
* **Deciders**: ai-dev-swarm maintainers

## Context and Problem Statement

Unsupervised agent work is weakest at long horizons: errors
compound over days, the work drifts away from anything valuable,
and a bad premise burns days of budget. Treating "a project" as
ONE long agent run is a known failure mode (AutoGPT-era).

## Decision

A project is a durable state machine in Postgres, decomposed by
the planning crew into an ORDERED GRAPH OF MILESTONES, each with
its own acceptance criteria and CI gate. The orchestrator advances
ONE state per tick; the per-milestone build crew runs to completion
and commits the milestone before the next one starts. Phase 4
added the **replanner** state between every milestone — an
Architect + PM call that decides Noop / Amend / Split / Escalate
on what comes next, with a cheap `AutoSplitPredictor` short-circuit
for over-budget milestones. Phase 4 also added a
**consolidation milestone** injected every Nth success (no new
features — tidy + verify).

## Consequences

* **Positive** — Failures are localised: a broken milestone fails
  ONE CI gate and triggers ONE replanner pass, not a whole-project
  abandon. Token budget pacing is honest (one milestone at a time,
  resumable next day). The state machine survives container
  restarts (Postgres). Decomposition + CI gates are what make
  multi-day work tractable.
* **Negative** — Planning crew quality is now load-bearing — a bad
  milestone graph produces N bad milestones. The replanner spends
  LLM tokens between every milestone (mitigated by the AutoSplit
  cheap path). Consolidation milestones add overhead.
* **Neutral** — Every Phase 0..5 schema field maps cleanly onto
  this model; the test suite encodes the transitions
  (`tests/property/test_state_machine_no_stuck_cycles.py`).

## Alternatives considered

* **One long agent run per project** — well-documented failure
  mode; doesn't fit Anthropic's pricing or our daily budget cap.
* **No decomposition, just retries** — wastes tokens on the parts
  that already worked.
* **Static milestone template (no replanner)** — can't adapt when
  the original plan turns out wrong on day 3.
