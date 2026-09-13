# Convenience targets. Everything runs inside Docker; no host toolchain needed.
# On Windows without `make`, run the equivalent `docker compose` commands shown
# next to each target.

COMPOSE ?= docker compose

.PHONY: help up up-d down restart logs ps build test test-backend test-db lint format migrate db-reset db-shell backend-shell

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the full stack (docker compose up --build)
	$(COMPOSE) up --build

up-d: ## Build and start in the background
	$(COMPOSE) up --build -d

down: ## Stop the stack, keep the database volume
	$(COMPOSE) down

restart: down up ## Restart the stack

logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

build: ## Build images without starting
	$(COMPOSE) build

test: test-backend test-db ## Run all test suites

test-backend: ## Backend unit tests (docker compose run --rm --no-deps backend python -m pytest)
	$(COMPOSE) run --rm --no-deps backend python -m pytest -q -p no:cacheprovider

test-db: ## Migration job tests
	$(COMPOSE) run --rm --no-deps db-migrate python -m pytest -q -p no:cacheprovider

lint: ## Ruff lint for all Python code
	$(COMPOSE) run --rm --no-deps backend ruff check app tests
	$(COMPOSE) run --rm --no-deps db-migrate ruff check migrate.py tests

format: ## Ruff format (rewrites files through a bind mount)
	docker run --rm -v "$(PWD)":/src -w /src python:3.12-slim sh -c "pip install -q ruff==0.7.4 && ruff format backend database && ruff check --fix backend database"

migrate: ## Apply pending migrations against the running database
	$(COMPOSE) run --rm db-migrate

db-reset: ## DESTROY the database volume and rebuild everything from scratch
	$(COMPOSE) down -v
	$(COMPOSE) up --build

db-shell: ## Open psql inside the postgres container
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-argumind} -d $${POSTGRES_DB:-argumind}

backend-shell: ## Shell inside the backend container
	$(COMPOSE) exec backend /bin/sh
