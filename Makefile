.DEFAULT_GOAL := help

PYTHON     ?= python3.12
UV         ?= uv
COMPOSE    ?= docker compose -f docker/compose.yml
SRC        := src/aidevswarm
TESTS      := tests

.PHONY: help install lint format typecheck test smoke up down logs ps clean

help: ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "}; /^[a-zA-Z_-]+:.*?## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

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

up: ## Bring up postgres + redis + orchestrator.
	$(COMPOSE) up -d --build

down: ## Stop and remove the compose stack.
	$(COMPOSE) down

logs: ## Tail orchestrator logs.
	$(COMPOSE) logs -f orchestrator

ps: ## Show compose service status (healthchecks).
	$(COMPOSE) ps

clean: ## Remove caches.
	rm -rf .pytest_cache .ruff_cache .mypy_cache
