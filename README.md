# ai-dev-swarm

[![verify](https://github.com/halilibrahimd27/ai-dev-swarm/actions/workflows/verify.yml/badge.svg?branch=main)](https://github.com/halilibrahimd27/ai-dev-swarm/actions/workflows/verify.yml)

Local autonomous multi-agent development system that designs, builds,
tests, and ships niche software projects to GitHub on its own.

> **Status:** Phase 5 (control plane). The full onboarding README,
> ADRs, threat model, and security policy land in Phase 6. This
> file is a placeholder.

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

### Replanner + consolidation (Phase 4)

Every time a milestone finishes — pass or fail — the orchestrator
routes the project through a new ``REPLANNING`` state before the next
milestone starts. Two things happen there, in order:

1. ``AutoSplitPredictor`` reads the most recent Developer/Tester
   sessions for the upcoming milestone. If predicted turns or cost
   blow ``AIDEVSWARM_AUTO_SPLIT_MAX_TURNS`` /
   ``AIDEVSWARM_AUTO_SPLIT_MAX_COST_USD``, the milestone is
   mechanically bisected into two children (no LLM call).
2. Otherwise the CrewAI Replanner crew (Architect + PM) runs and
   returns ONE typed ``ReplannerAction`` —
   ``Noop | Amend | Split | Escalate``. ``Escalate`` lands the
   project in ``BLOCKED`` and pings Telegram.

Every fifth successful milestone an explicit ``[CONSOLIDATION]``
milestone is injected — a no-new-features tidy + ``make verify`` pass
the Reviewer is instructed to reject if it tries to add public API.

The scheduler is now an asyncio ``ProjectPool`` with
``AIDEVSWARM_BUILD_CONCURRENCY`` workers (default 1). Each project
also has its own kill switch
(``aidevswarm:kill:<project_id>`` in Redis) alongside the global
``aidevswarm:kill_switch``.

### Control plane (Phase 5)

ai-dev-swarm now ships a loopback-only FastAPI server (port 8080
by default, validated to ``127.0.0.1``/``localhost`` at startup)
plus a static three-pane web UI and a polling-mode Telegram bot.
All three feed into the same typed ``Command`` discriminated union:

  * **Web panel** (``ui/index.html``) — vanilla HTML/JS/CSS, no
    build step, strict CSP. Three panes: project state, live
    inter-agent transcript, controls. The transcript pane always
    shows a "steer" text box that fires a fire-and-forget
    ``inject_note`` command — the next agent step picks it up via
    the Phase 1 ``{{ steering_notes }}`` slot OR (for live SDK
    sessions) via a ``PreToolUse`` hook that runs before each tool
    call.
  * **SSE endpoints** — ``/sse/projects``, ``/sse/transcript/{id}``
    (per-project), ``/sse/metrics``. Backed by a CrewAI EventBus
    listener that fans events into per-topic asyncio queues. Every
    outbound message passes through ``SecretRedactor`` so secrets
    in agent transcripts never reach the wire.
  * **Telegram bot** — polling mode (no webhook, no port), strict
    allow-list (``AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS``). Free-form
    text is routed through a Claude Haiku intent parser into a
    typed ``Command``. Destructive intents (abort, kill, rescope,
    transform, drop_and_start_new, switch_to_idea, reject_idea)
    are echoed back with a ``[Yes][No]`` inline keyboard before
    they reach the dispatcher.

Both surfaces funnel through ``CommandRouter.dispatch`` — the
single source of truth for what each command means. Adding a new
operator action means adding ONE variant to ``schemas/command.py``
and ONE handler to ``CommandRouter``; both surfaces gain it for
free.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## License

Apache-2.0. See `LICENSE` (added in Phase 6).
