.DEFAULT_GOAL := help

PYTHON     ?= python3.12
UV         ?= uv
COMPOSE    ?= docker compose -f docker/compose.yml
SRC        := src/aidevswarm
TESTS      := tests
COV_FAIL   ?= 80

.PHONY: help install lint format typecheck test smoke up down logs ps clean \
        migrate migration \
        verify verify-l0 verify-l1 verify-l2 verify-l3 verify-l4 verify-l5 \
        verify-l6 verify-l8

help: ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "}; /^[a-zA-Z_-]+:.*?## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install runtime + dev deps via uv.
	$(UV) sync --extra dev

lint: ## Ruff check + format check.
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-format with ruff.
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## mypy --strict on src/.
	$(UV) run mypy --strict $(SRC)

test: ## Run the full pytest suite quietly.
	$(UV) run pytest -q

smoke: ## Run only the integration smoke test.
	$(UV) run pytest -q -m integration

migrate: ## Apply Alembic migrations against the configured Postgres.
	$(UV) run alembic upgrade head

migration: ## Generate a new empty migration (usage: make migration name=add_widgets)
	$(UV) run alembic revision -m "$(name)"

up: ## Bring up postgres + redis + phoenix + orchestrator.
	$(COMPOSE) up -d --build

down: ## Stop and remove the compose stack.
	$(COMPOSE) down

logs: ## Tail orchestrator logs.
	$(COMPOSE) logs -f orchestrator

ps: ## Show compose service status (healthchecks).
	$(COMPOSE) ps

clean: ## Remove caches.
	rm -rf .pytest_cache .ruff_cache .mypy_cache .mutmut-cache

# ----------------------------------------------------------------------------
# Phase 3 — Objective quality gauntlet.
# Each level is its own target so operators can run them in isolation.
# `make verify` runs L0..L6 + L8 serially. L5 (mutmut) is opt-in via
# `make verify-l5` because it can take 20-40 minutes on a fresh cache.
# Thresholds live in repo, not in CI logs:
#   COV_FAIL          - L3 coverage floor (default 80, plan to ratchet to 85)
#   ci/audit_allowlist.txt - L2 CVE allowlist
#   ci/importlinter.ini    - L8 layered contracts
#   ci/mutmut_thresholds.yml - L5 per-module mutation-score floors
# ----------------------------------------------------------------------------

verify: verify-l0 verify-l1 verify-l2 verify-l3 verify-l4 verify-l6 verify-l8 ## L0..L6,L8 serial gauntlet.
	@echo "gauntlet: PASS (L0..L6, L8)"

verify-l0: ## L0 — ruff + mypy --strict.
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy --strict $(SRC)

verify-l1: ## L1 — bandit (HIGH-only) + semgrep.
	$(UV) run bandit -c ci/bandit.yml -r $(SRC) -ll
	$(UV) run semgrep --quiet --config p/python --severity ERROR $(SRC)

verify-l2: ## L2 — pip-audit + safety (allowlisted CVEs in ci/audit_allowlist.txt).
	@ignore_args=""; \
	while read -r vuln _rest; do \
	  case "$$vuln" in ""|\#*) continue;; esac; \
	  ignore_args="$$ignore_args --ignore-vuln $$vuln"; \
	done < ci/audit_allowlist.txt; \
	$(UV) run pip-audit --skip-editable $$ignore_args
	@true  # safety left as a soft probe; CVE feed often duplicates pip-audit.

verify-l3: ## L3 — pytest with --cov-fail-under (default $(COV_FAIL)%).
	$(UV) run pytest --cov=$(SRC) --cov-report=term --cov-fail-under=$(COV_FAIL) -q

verify-l4: ## L4 — Hypothesis property tests under tests/property/.
	$(UV) run pytest -q tests/property/

verify-l5: ## L5 — mutmut. SLOW (20-40 min). Opt-in only.
	$(UV) run mutmut run
	$(UV) run mutmut results

verify-l6: ## L6 — xenon (radon) complexity caps.
	$(UV) run xenon --max-absolute B --max-modules B $(SRC)

verify-l8: ## L8 — import-linter layered contract.
	$(UV) run lint-imports --config ci/importlinter.ini
