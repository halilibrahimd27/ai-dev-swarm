# examples — small projects ai-dev-swarm builds

This directory carries two sample projects shaped EXACTLY the way
the orchestrator stores them on disk:

  * `spec.json`         — the typed `Project` + `MilestoneGraph`
                          rows the planning crew produces.
  * `transcript.md`     — curated key inter-agent exchanges from
                          a run (Critic ↔ Ideator, PM ↔ Architect,
                          Developer SDK calls, Reviewer decisions).
  * `README.md`         — what was built, how to reproduce the
                          run locally, where the live trace lives.

## Honesty disclaimer

These are **curated reproductions**, not live captures. The
swarm's first real autonomous run will produce real captures —
when that happens, replace these directories with the real ones
(the schema is the same).

Why ship reproductions at all? Because a stranger cloning the repo
needs to see what a finished project *looks like* before they
commit to running a 24/7 autonomous system. The reproductions are
schema-faithful so the structure is real even if the trace IDs
aren't.

## How to reproduce a live run

1. Follow the [project README](../README.md) Quickstart.
2. Once the orchestrator is running, drop one of these specs into
   a steering note via the web panel:

   ```
   please build the cli-port-finder spec from examples/
   ```

   The Ideator + Critic will accept it; the planning crew will
   regenerate the milestone graph; the build crew will run.

3. Watch the live trace at http://localhost:6006 (Phoenix). Save
   the trace ID; that's what you'd commit back to `examples/` as
   a real capture.

## Want a new example?

Pick a small, niche, deeply-decomposable project and write a
`spec.json` by hand. Two rules:

  * **No "yet another todo app"** — the Critic's novelty check
    will refuse it. Pick something genuinely underserved.
  * **At least 3 milestones** so the Phase 4 replanner + the
    consolidation cadence have something to do.

See the existing examples for shape.
