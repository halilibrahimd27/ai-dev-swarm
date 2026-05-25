# cli-port-finder — example project

A small CLI that finds free TCP ports on a host with rich
filtering (range, family, IPv6, reserved-port skip) and a
Hypothesis-tested core.

## What ai-dev-swarm built

```
src/port_finder/
  probe.py          # pure-Python bind-and-release primitive
  cli.py            # Click app wrapping the core
tests/
  property/         # Hypothesis tests on probe()
  integration/      # CliRunner tests on the Click app
pyproject.toml
README.md
```

4 milestones, each its own git commit:

1. **core port-probe primitive** — pure socket bind-and-release.
2. **CLI surface + click integration** — `--range`, `--family`,
   `--first`, `--list`.
3. **IPv6 + dual-stack handling** — `IPV6_V6ONLY=1` after a
   mid-flight operator note.
4. **[CONSOLIDATION]** — tidy + `make verify` pass, no new
   public API.

## Reproduce it

This directory contains a curated reproduction, not a live
capture (see [`../README.md`](../README.md) for why).

To re-run it on your own host:

```bash
# Bring up ai-dev-swarm (see project README Quickstart).
docker compose up -d

# Drop a steering note into the running orchestrator pointing
# at this spec.
curl -sf -X POST http://127.0.0.1:8080/api/commands \
  -H 'content-type: application/json' \
  -d @- <<'EOF'
{
  "intent": "inject_note",
  "project_id": "00000000-0000-0000-0000-000000000000",
  "body": "Please build the cli-port-finder spec from examples/."
}
EOF
```

(Replace the `project_id` with one of your active projects — the
ideation crew is what actually accepts the suggestion.)

## Live trace

When you reproduce this run on your own host, the live trace
appears at http://localhost:6006 (Phoenix). Save the trace ID
if you want to commit your real run back to `examples/` — the
schema is the same as `spec.json` + `transcript.md`.

## Spec

See [`spec.json`](spec.json) — the typed `Project` +
`MilestoneGraph` rows the planning crew produces.

## Transcript

See [`transcript.md`](transcript.md) — curated key inter-agent
exchanges.

---

Need help getting your own ai-dev-swarm running? See
[Getting your keys](../../README.md#getting-your-keys) in the
top-level README.
