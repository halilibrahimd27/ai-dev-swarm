# ai-dev-swarm

[![verify](https://github.com/halilibrahimd27/ai-dev-swarm/actions/workflows/verify.yml/badge.svg?branch=main)](https://github.com/halilibrahimd27/ai-dev-swarm/actions/workflows/verify.yml)

Local autonomous multi-agent development system that designs, builds,
tests, and ships niche software projects to GitHub on its own.

> **Status:** Phase 3 (objective quality). The full onboarding README,
> ADRs, threat model, and security policy land in Phase 6. This file
> is a placeholder.

## Quick start (developer-facing)

```bash
# 1. Install uv + Python 3.12 if you don't already have them.
brew install uv && uv python install 3.12

# 2. Install dependencies.
uv sync --extra dev

# 3. Run the verification gauntlet.
make lint typecheck test

# 4. Bring up the stack (Postgres + Redis + Phoenix + orchestrator).
cp .env.example .env       # then fill in real values
make up
make logs                  # tail the orchestrator

# 5. Apply database migrations (steering_notes etc).
make migrate

# 6. Open Phoenix to watch CrewAI traces.
open http://localhost:6006
```

### Host port collisions

`make up` defaults to publishing Postgres on `5432` and Redis on
`6379`. If another container or local service already owns one of
those, override before running:

```bash
POSTGRES_HOST_PORT=5433 REDIS_HOST_PORT=6380 make up
```

### Verification gauntlet (Phase 3)

```bash
make verify          # L0..L6 + L8: lint, types, security, deps, tests
                     # + coverage (>=85%), property tests, complexity caps,
                     # architectural layers
make verify-l5       # opt-in: mutmut mutation testing (slow, 20-40 min)
```

Each level has its own target (`make verify-l0`, `verify-l1`, ...) so
you can run them in isolation. Thresholds are codified in repo:
`ci/audit_allowlist.txt` (CVEs), `ci/importlinter.ini` (architectural
layers), `ci/mutmut_thresholds.yml` (per-module mutation-score floors).

### Manual SDK smoke (Phase 2)

The fizzbuzz integration test exercises the Claude Agent SDK
end-to-end through the new build crew. It is skipped automatically
unless an API key is set so the default gauntlet stays free.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run pytest tests/integration/test_fizzbuzz_smoke.py -m anthropic -v
```

Watch http://localhost:6006 (Phoenix) — you should see a nested span
tree per run: ``sdk.developer`` / ``sdk.tester`` → SDK tool calls
(``Read``, ``Edit``, ``Bash``) → tree-sitter MCP calls.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## License

Apache-2.0. See `LICENSE` (added in Phase 6).
