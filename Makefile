.PHONY: help dev dev-down test test-api test-api-watch test-coverage test-supervisor review deploy-check prd logs db-migrate db-studio hooks-install monitoring monitoring-down monitoring-logs monitoring-reset

## ─── General ────────────────────────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

## ─── Development ────────────────────────────────────────────────────────────

dev: ## Start full dev stack (Docker Compose with source mounts)
	docker compose -f docker/compose.yml -f docker/compose.dev.yml up

dev-down: ## Stop dev stack
	docker compose -f docker/compose.yml -f docker/compose.dev.yml down

dev-logs: ## Tail dev stack logs
	docker compose -f docker/compose.yml -f docker/compose.dev.yml logs -f

dev-reset: ## Reset dev stack (removes volumes)
	docker compose -f docker/compose.yml -f docker/compose.dev.yml down -v

## ─── Database ───────────────────────────────────────────────────────────────

db-migrate: ## Run pending database migrations
	@echo "Running migrations..."
	docker compose exec api npm run db:migrate

db-rollback: ## Rollback last migration
	docker compose exec api npm run db:rollback

db-studio: ## Open Adminer database admin UI (dev stack must be running)
	@echo "Opening Adminer at http://localhost:8081"
	@open http://localhost:8081 2>/dev/null || xdg-open http://localhost:8081

## ─── Quality ────────────────────────────────────────────────────────────────

test: ## Run all tests (API unit tests + supervisor bash tests)
	@echo "=== API unit tests ==="
	cd api && npm test
	@echo ""
	@echo "=== Supervisor: validate-agent ==="
	bash tests/supervisor/test-validate-agent.sh
	@echo ""
	@echo "=== Supervisor: discover-agents ==="
	bash tests/supervisor/test-discover-agents.sh
	@echo ""
	@echo "=== Supervisor: external-triggers ==="
	bash tests/supervisor/test-external-triggers.sh

test-api: ## Run API unit tests only
	cd api && npm test

test-api-watch: ## Run API unit tests in watch mode
	cd api && npm run test:watch

test-coverage: ## Run API tests with coverage report
	cd api && npm run test:coverage

test-supervisor: ## Run all supervisor bash tests
	bash tests/supervisor/test-validate-agent.sh
	bash tests/supervisor/test-discover-agents.sh
	bash tests/supervisor/test-external-triggers.sh

lint: ## Run linter
	npm run lint

typecheck: ## Run TypeScript type check
	npm run typecheck

codegen: ## Regenerate GraphQL types from schema
	npm run codegen

## ─── Agent Commands (Claude Code) ──────────────────────────────────────────

review: ## Run multi-agent code review (Claude Code)
	claude -p "/review" 

deploy-check: ## Run pre-deployment agent checklist (Claude Code)
	claude -p "/deploy-check"

prd-show: ## Show current PRD summary (Claude Code)
	claude -p "/prd"

## ─── Monitoring ──────────────────────────────────────────────────────────────

monitoring: ## Start monitoring stack (Prometheus + Grafana + Loki)
	docker compose -f docker/compose.monitoring.yml up -d

monitoring-down: ## Stop monitoring stack
	docker compose -f docker/compose.monitoring.yml down

monitoring-logs: ## Tail monitoring logs
	docker compose -f docker/compose.monitoring.yml logs -f

monitoring-reset: ## Reset monitoring stack (removes volumes)
	docker compose -f docker/compose.monitoring.yml down -v

## ─── Setup ───────────────────────────────────────────────────────────────────

hooks-install: ## Make all Claude Code hooks executable
	chmod +x .claude/hooks/*.sh
	@echo "✅ Hooks installed"

setup: hooks-install ## Initial project setup
	cp -n .env.example .env || true
	npm install
	@echo "✅ Project setup complete. Run 'make dev' to start."
