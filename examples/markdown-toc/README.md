# markdown-toc — example project

A Python Markdown table-of-contents generator that matches
GitHub's slugify rules exactly (incl. duplicate-heading
suffixes) and round-trips with an existing TOC block (idempotent).

## What ai-dev-swarm built

```
src/markdown_toc/
  parser.py         # ATX + Setext heading scanner
  slug.py           # GitHub-rules slugify with duplicate suffixes
  render.py         # bullet-list renderer + idempotent inserter
  cli.py            # Click app
tests/
  property/         # Hypothesis tests on parser + slug
  integration/      # idempotency round-trip
pyproject.toml
README.md
```

**5 milestones** (not 4 — the planning crew's initial milestone
2 was auto-split mid-run by the Phase 4 `AutoSplitPredictor`):

1. heading parser (ATX + Setext)
2. **[AUTO-SPLIT 1/2]** GitHub slugify — emoji + punctuation
3. **[AUTO-SPLIT 2/2]** GitHub slugify — duplicate-heading suffix
4. TOC renderer + idempotent insert
5. **[CONSOLIDATION]** tidy + verify pass

Note the workflow in the transcript: the operator drops a mid-
flight steering note ("don't forget GitHub uses `-1`/`-2`, not
`(1)`/`(2)`") via the web panel's "steer" text box; the Phase 5
PreToolUse hook surfaces it to the Developer SDK before the next
tool call. The Reviewer's commit hash `d8b1f44` reflects that
note being applied.

## Reproduce it

See [`spec.json`](spec.json) for the typed spec. To re-run on
your host:

```bash
docker compose up -d
# In the web UI's "steer" text box on any active project:
#    please build the markdown-toc spec from examples/
```

## Live trace

When you reproduce this run, the live trace appears at
http://localhost:6006 (Phoenix) and the live transcript appears
at http://127.0.0.1:8080 (web UI). The interesting span in the
trace tree is the `replanner.split` one — the auto-split decision
that bisected the original milestone 2 into 2a + 2b.

## Spec

See [`spec.json`](spec.json).

## Transcript

See [`transcript.md`](transcript.md) — curated key inter-agent
exchanges, including the auto-split and the mid-flight steering
note.

---

Need help getting your own ai-dev-swarm running? See
[Getting your keys](../../README.md#getting-your-keys) in the
top-level README.
