# ai-dev-swarm — Threat Model

This document is the STRIDE-shaped threat surface for ai-dev-swarm
as of Phase 6. It's the operator-facing answer to "what can go
wrong, and what stops it?"

The product runs on **one developer's PC** and **executes LLM-
generated code**. Three threat sources matter:

1. **A malicious operator on the same machine** (or a malicious
   process running as the same user).
2. **The LLM itself** — Claude is non-malicious but unreliable;
   generated code is treated as untrusted by construction.
3. **A network attacker** — bounded by the fact that nothing
   listens on a public port. The Telegram bot is the only outbound
   bidirectional channel; the FastAPI server is loopback-only.

Out of scope: nation-state adversaries with persistent access to
the operator's machine, supply-chain attacks on Anthropic /
Docker / pip (mitigated by pinned versions + `pip-audit`).

## Trust boundaries

```
┌──────────────────── operator's PC ───────────────────────────────┐
│                                                                  │
│   .env (secrets)  CLAUDE.md (build kit, gitignored)              │
│        │                                                         │
│        ▼                                                         │
│   ┌───────────────────────────────────────────────────────────┐  │
│   │  docker network (bridge, no public exposure)              │  │
│   │                                                           │  │
│   │   postgres ─── redis ─── phoenix                          │  │
│   │       ▲           ▲          ▲                            │  │
│   │       └───────────┼──────────┘                            │  │
│   │                   │                                       │  │
│   │              orchestrator  ─►  CrewAI agents              │  │
│   │              + FastAPI         ─► Claude Agent SDK ───┐   │  │
│   │              + Telegram bot       (Developer/Tester)  │   │  │
│   │                   │                                   ▼   │  │
│   │                   │                            sandbox    │  │
│   │                   │                            container  │  │
│   │                   ▼                            (untrusted │  │
│   │             127.0.0.1:8080                      gen code) │  │
│   │             web UI                                        │  │
│   └───────────────────────────────────────────────────────────┘  │
│                       │                            │             │
│                       │                            ▼             │
│                       ▼                       api.anthropic.com  │
│              api.telegram.org                 (LLM calls)        │
│              (polling, outbound)                                 │
└──────────────────────────────────────────────────────────────────┘
```

## Per-component STRIDE table

Format: `(S)poofing | (T)ampering | (R)epudiation | (I)nfo
disclosure | (D)oS | (E)levation of privilege`.

### Orchestrator process

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (E) untrusted gen code escapes | Developer SDK writes to `cwd` | `permission_mode="acceptEdits"` (NEVER `bypassPermissions`), `disallowed_tools=("WebFetch", "WebSearch")`, `max_turns` + `max_budget_usd` caps per call — see `claude_agent_sdk_tool.py:_DISALLOWED_TOOLS` |
| (D) runaway LLM cost | Loop in the build crew | Per-milestone token budget (`AIDEVSWARM_PER_MILESTONE_TOKEN_BUDGET`), daily cap (`AIDEVSWARM_DAILY_TOKEN_BUDGET`), AutoSplit predictor short-circuit before LLM replanner |
| (D) operator can't stop a runaway | — | Per-project + global Redis kill switch (`aidevswarm:kill:<id>` + `aidevswarm:kill_switch`); checked every tick |
| (I) secret leakage in log/UI/Telegram | Stack trace includes API key | `SecretRedactor` (Phase 5) wraps every outbound SSE + Telegram message; bandit B105/106 gauntlet on the source |

### CrewAI agents

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (T) prompt injection | LLM output flows back into next role's prompt | Discriminated-union schemas at every role boundary (`Idea`, `ReplannerAction`, `MilestoneSpec`) — off-schema replies are rejected, not executed |
| (E) prompt injection asks for shell | — | The Developer/Tester SDK tool's `allowed_tools` is a fixed allow-list per role; an inserted "use Bash to ..." instruction still can't reach disallowed tools |

### Claude Agent SDK subprocess

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (E) sandboxed gen code escapes the workspace | — | SDK enforces `cwd` + `permission_mode`; the workspace is a per-project git repo on disk, never `/etc` or `~` |
| (T) operator note injection abused | PreToolUse hook surfaces operator steering | The hook reads from `steering_notes`, written ONLY via the typed `Command` bus — schema-validated; nothing arbitrary lands there |
| (I) credentials in generated code | Developer writes a key into a file | Detected at the L1 `bandit` step (secret scanner in `make verify`); production push gate ensures it never reaches GitHub |

### CI sandbox (generated-code gate)

Each finished milestone is gated by running the **generated project's own
CI** (lint + types + tests). That means executing untrusted, LLM-written
code — and the isolation depends on `AIDEVSWARM_SANDBOX_MODE`
(`settings.py::sandbox_mode`, three implementations in `tools/sandbox.py`):

- **`docker`** — `docker run --rm --network=none -v <ws>:/workspace:ro`
  in an ephemeral container with **no network** and the workspace mounted
  **read-only**, **no secrets** in the container env. This is the
  treat-it-as-hostile option. Needs the host Docker socket + the
  `aidevswarm-sandbox` image.
- **`subprocess`** — **the compose DEFAULT** (the orchestrator container has
  no Docker socket). `SubprocessSandbox` creates a throwaway `uv` venv,
  `uv pip install`s the generated project **with its declared
  dependencies**, then runs `ruff` + `mypy --strict` + `pytest` **inside the
  orchestrator's own container**, which **has network** and **has the
  orchestrator's environment** (including `ANTHROPIC_API_KEY`,
  `GITHUB_TOKEN`, `POSTGRES_PASSWORD`).
- **`inmemory`** — no execution at all; CI is a free pass (last resort,
  quality then rests on the Reviewer LLM alone).

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (E) gen code executes in-process (`subprocess` mode) | `pytest` imports + runs the generated module; a `pyproject` build/install hook runs on `uv pip install` | **Accepted trade-off for a single-operator local system.** The code was authored by Claude (non-adversarial by assumption) and the only consumer is the operator. The gain over `inmemory` is that tests *actually run*, catching broken/garbage milestones that the old free-pass shipped blind. **For real isolation set `AIDEVSWARM_SANDBOX_MODE=docker`** — network-less, read-only, secret-free container. |
| (I) gen code reads orchestrator secrets (`subprocess` mode) | `os.environ` is visible to in-process test code | Same trade-off; `docker` mode removes secrets from the gate entirely. The orchestrator container is on the docker bridge, never exposed to the LAN, so exfil requires both adversarial gen code AND outbound network. |
| (T) malicious/typosquatted dependency pulled at install (`subprocess` mode) | generated `pyproject.toml` declares a hostile package; `uv pip install` fetches it with network | Bounded by the same non-adversarial-author assumption; `docker` mode isolates the install in a network-less container. Prompt-injection into the Developer is mitigated by the role's fixed `allowed_tools` allow-list (see the Claude Agent SDK rows above). |
| (D) gen test suite hangs / loops | infinite loop in generated tests | Per-run `timeout_seconds` (default 1800s) on the subprocess; the build crew records a CI failure on timeout (`SandboxRun(exit_code=124)`) and the milestone retries/blocks |

> The trust-boundary diagram's "sandbox container" reflects **`docker`**
> mode. In the **default `subprocess`** mode there is no separate sandbox
> container — the gate runs inside the orchestrator box. Operators who treat
> generated code as fully hostile should switch to `docker` mode.

### Postgres

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (E) container escape via pgvector ext | — | pgvector + pgcrypto are official Postgres extensions; we run them with the default least-privilege role |
| (I) DB credentials in compose logs | `POSTGRES_PASSWORD` env var | Operator-set; `.env` is gitignored; orchestrator never logs the value |

### Redis

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (T) attacker writes to the kill switch | — | Redis is on the docker bridge network; not exposed to LAN by default |
| (D) Redis full | — | Kill-switch keys are tiny (~50 B each); not a realistic OOM vector |

### Phoenix observability

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (I) trace tree contains secret | LLM call payload | The orchestrator never sends secret-shaped strings to Phoenix; OpenInference instrumentation captures spans, not raw API keys. Operators can disable Phoenix via `AIDEVSWARM_PHOENIX_ENABLED=false`. |
| (I) Phoenix UI exposed | — | Phoenix listens on `0.0.0.0:6006` inside the docker bridge; docker compose publishes to `0.0.0.0:6006` on host. Operators wanting strict loopback can edit `docker/compose.yml` to use `127.0.0.1:6006:6006`. **Not** loopback-locked by default because Phoenix has no authentication and would be useless behind one. |

### Telegram bot

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (S) attacker spoofs operator | DM to bot | Hard allow-list of Telegram `user_id`s (`AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS`); non-listed users SILENTLY denied at every handler — see `telegram/bot.py::_is_allowed` |
| (T) attacker tampers with intent | Free-form message → Haiku parser | Haiku output is JSON-only + schema-validated by Pydantic `TypeAdapter[Command]`; off-list intents bounce |
| (E) destructive command without confirm | `abort_project` arrives unconfirmed | `CommandRouter.dispatch` REFUSES `requires_confirmation()=True` commands regardless of surface; the bot enforces `[Yes][No]` echo |
| (I) bot reveals secret in reply | — | Every outbound `_reply` + `_reply_via_query` goes through `SecretRedactor` |

### Web UI / FastAPI

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (S) network attacker reaches API | LAN | `AIDEVSWARM_API_HOST` validator refuses LAN IPs; docker publish is `127.0.0.1:8080:8080` only — see `settings.py::_enforce_loopback` |
| (T) cross-site request forgery | Browser pages on same host | Strict CSP (`default-src 'self'`); no inline `eval`; no third-party scripts |
| (T) inline scripts | — | CSP rejects them; the UI is vanilla JS with no eval |
| (I) SSE leaks secret | — | `SecretRedactor` wraps every outbound SSE event in `_emit` |

### MCP servers (tree-sitter-mcp)

| Threat | Vector | Mitigation |
| --- | --- | --- |
| (E) MCP server runs untrusted code | — | tree-sitter-mcp is read-only (parser, no exec); `mcp_config.py` only configures `npx @nendo/tree-sitter-mcp` |
| (S) operator's `.mcp.json` swapped | — | `.mcp.example.json` is the committed template; on fresh clones the operator copies it manually |

## Operator-facing security controls (recap)

1. **Kill switch**: global (`AIDEVSWARM_API_PORT/api/commands` POST
   with `intent=kill_switch`, requires `confirmed=true`) +
   per-project (`abort_project`).
2. **Plan-approval checkpoint**: `AIDEVSWARM_REQUIRE_APPROVAL=true`
   stops every project at `awaiting_approval` for explicit operator
   sign-off before the multi-day build begins.
3. **Telegram allow-list**: empty list = locked down.
4. **Loopback-only control plane**: host-side guarantee comes from
   docker's `127.0.0.1:`-prefixed publish line; uvicorn binds
   `0.0.0.0` inside the container (necessary for docker port
   publishing to work). See `docker/compose.yml`.
5. **`SecretRedactor`**: every outbound SSE + Telegram message is
   filtered. 20+20 positive/negative tests in
   `tests/unit/test_redactor.py`.
6. **`bandit` + `pip-audit` + `semgrep`** in `make verify`: L1
   catches HIGH bandit; L2 catches known CVEs (with an explicit
   allowlist in `ci/audit_allowlist.txt`).
7. **CI sandbox isolation**: `AIDEVSWARM_SANDBOX_MODE=docker` runs
   the generated-code gate in a network-less, read-only, secret-free
   container. The default `subprocess` mode runs it in-process for
   convenience (no Docker socket needed) — see the *CI sandbox*
   STRIDE section for the trade-off.

## Reporting a vulnerability

See [SECURITY.md](SECURITY.md).
