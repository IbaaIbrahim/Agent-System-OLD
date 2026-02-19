# Include .env file if it exists (loads environment variables like API_HOST)
-include .env
export

# Detect OS
ifeq ($(OS),Windows_NT)
	IS_WINDOWS = 1
	SHELL = cmd.exe
	SLEEP = timeout /t /nobreak
	RM = del /Q
	PYTHON = python
	# Windows Python path setting
	SET_PYTHONPATH = set PYTHONPATH=$(shell cd)
	# Check if watchfiles exists (Windows)
	CHECK_WATCHFILES = where watchfiles >nul 2>&1
else
	IS_WINDOWS = 0
	SHELL = bash
	SLEEP = sleep
	RM = rm -f
	PYTHON = python3
	# Unix Python path setting
	SET_PYTHONPATH = PYTHONPATH=$(PWD)
	# Check if watchfiles exists (Unix)
	CHECK_WATCHFILES = command -v watchfiles > /dev/null
endif

.PHONY: help install dev up down logs clean test migrate lint format api stream orchestrator auth-broker workers archiver frontend postman openapi ws live-session ps

# Default target
help:
	@echo "Agent System - Development Commands"
	@echo ""
	@echo "Note: This Makefile supports both Linux/macOS and Windows (PowerShell/Make)"
	@echo "      Windows users can also use 'dev.bat' for native Windows commands"
	@echo ""
	@echo "Infrastructure:"
	@echo "  make up          - Start all Docker services"
	@echo "  make down        - Stop all Docker services"
	@echo "  make logs        - Tail logs from all services"
	@echo "  make clean       - Remove containers, volumes, and images"
	@echo "  make restart     - Restart all services"
	@echo "  make restart-and-migrate - Restart all services and run migrations"
	@echo ""
	@echo "Development:"
	@echo "  make install     - Install Python and Node dependencies"
	@echo "  make dev         - Start services in development mode"
	@echo "  make migrate     - Run database migrations"
	@echo "  make migrate-new - Create a new migration"
	@echo ""
	@echo "Testing:"
	@echo "  make test              - Run all tests"
	@echo "  make test-unit         - Run unit tests only"
	@echo "  make test-int          - Run integration tests"
	@echo "  make test-int-clean    - Reset DB → run tests"
	@echo "  make test-int-clean-after - Run tests → reset DB"
	@echo "  make test-reset-db     - Reset database only"
	@echo ""
	@echo "  Isolated Test Environment (separate DB on port 8100):"
	@echo "  make test-isolated     - Start test services → run tests → stop"
	@echo "  make test-isolated-keep - Start test services → run tests → keep running"
	@echo "  make test-services-up  - Start isolated test services"
	@echo "  make test-services-down - Stop isolated test services"
	@echo ""
	@echo "  make test-phase1       - Run Phase 1 auth tests"
	@echo "  make test-cov          - Run tests with coverage"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint        - Run linters"
	@echo "  make format      - Format code"
	@echo "  make typecheck   - Run type checking"
	@echo ""
	@echo "Individual Services:"
	@echo "  make api         - Run API Gateway locally"
	@echo "  make stream      - Run Stream Edge locally"
	@echo "  make orchestrator- Run Orchestrator locally"
	@echo "  make auth-broker - Run Auth Broker locally"
	@echo "  make workers     - Run Tool Workers locally"
	@echo "  make archiver    - Run Archiver locally"
	@echo "  make ws          - Run WebSocket Gateway locally"
	@echo "  make live-session - Run Live Session Manager locally"
	@echo "  make frontend    - Run Frontend locally"
	@echo "  make postman      - Generate Postman Collection"
	@echo "  make openapi      - Generate OpenAPI Schema"
	@echo ""
	@echo "Utilities:"
	@echo "  make ps           - List local (non-Docker) running services"

# ===================
# INFRASTRUCTURE
# ===================

up:
	docker compose up -d
	@echo "Waiting for services to be healthy..."
	@$(SLEEP) 10
	@docker compose ps

down:
	docker compose down --rmi local

logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api-gateway

logs-stream:
	docker compose logs -f stream-edge

logs-orchestrator:
	docker compose logs -f orchestrator

clean-volumes:
	docker compose down --volumes --rmi local

clean: clean-volumes test-services-down
	docker system prune -f

restart: down up

restart-and-migrate: down up migrate

# Infrastructure only (for local development)
infra:
	docker compose up -d postgres redis zookeeper kafka kafka-init pgadmin
	@echo "Infrastructure services started"

# ===================
# DEVELOPMENT
# ===================

install:
	@echo "Installing Python dependencies..."
	pip install -r requirements.txt
	pip install -r requirements-dev.txt
	@echo "Installing Node dependencies..."
	cd frontend && npm install

dev: infra
	@echo "Starting services in development mode..."
	@echo "Run 'make migrate' first"
	@echo "Infrastructure (Postgres, Redis, Kafka, pgAdmin) is running."
	@echo "  pgAdmin: http://localhost:5050 (admin@admin.com / admin)"
	@echo "Run each service in separate terminals:"
	@echo "  make api"
	@echo "  make stream"
	@echo "  make ws"
	@echo "  make live-session"
	@echo "  make orchestrator"
	@echo "  make auth-broker"
	@echo "  make workers"
	@echo "  make archiver"
	@echo "  make frontend"

api:
ifeq ($(IS_WINDOWS),1)
	@set PYTHONPATH=$(shell cd) && uvicorn services.api-gateway.src.main:app --reload --port 8000 --host $(or $(API_HOST),localhost)
else
	PYTHONPATH=$(PWD) uvicorn services.api-gateway.src.main:app --reload --port 8000 --host $(or $(API_HOST),localhost)
endif

stream:
ifeq ($(IS_WINDOWS),1)
	@set PYTHONPATH=$(shell cd) && uvicorn services.stream-edge.src.main:app --reload --port 8001 --host $(or $(API_HOST),localhost)
else
	PYTHONPATH=$(PWD) uvicorn services.stream-edge.src.main:app --reload --port 8001 --host $(or $(API_HOST),localhost)
endif

orchestrator:
ifeq ($(IS_WINDOWS),1)
	@where watchfiles >nul 2>&1 && (set PYTHONPATH=$(shell cd) && watchfiles --ignore-paths .cursor "python -m services.orchestrator.src.main") || (set PYTHONPATH=$(shell cd) && python -m services.orchestrator.src.main)
else
	@$(CHECK_WATCHFILES) && \
		PYTHONPATH=$(PWD) watchfiles --ignore-paths .cursor "$(PYTHON) -m services.orchestrator.src.main" || \
		PYTHONPATH=$(PWD) $(PYTHON) -m services.orchestrator.src.main
endif

workers:
ifeq ($(IS_WINDOWS),1)
	@where watchfiles >nul 2>&1 && (set PYTHONPATH=$(shell cd) && watchfiles "python -m services.tool-workers.src.main") || (set PYTHONPATH=$(shell cd) && python -m services.tool-workers.src.main)
else
	@$(CHECK_WATCHFILES) && \
		PYTHONPATH=$(PWD) watchfiles "$(PYTHON) -m services.tool-workers.src.main" || \
		PYTHONPATH=$(PWD) $(PYTHON) -m services.tool-workers.src.main
endif

archiver:
ifeq ($(IS_WINDOWS),1)
	@where watchfiles >nul 2>&1 && (set PYTHONPATH=$(shell cd) && watchfiles "python -m services.archiver.src.main") || (set PYTHONPATH=$(shell cd) && python -m services.archiver.src.main)
else
	@$(CHECK_WATCHFILES) && \
		PYTHONPATH=$(PWD) watchfiles "$(PYTHON) -m services.archiver.src.main" || \
		PYTHONPATH=$(PWD) $(PYTHON) -m services.archiver.src.main
endif

ws:
ifeq ($(IS_WINDOWS),1)
	@set PYTHONPATH=$(shell cd) && uvicorn services.websocket-gateway.src.main:app --reload --port 8002 --host $(or $(API_HOST),localhost)
else
	PYTHONPATH=$(PWD) uvicorn services.websocket-gateway.src.main:app --reload --port 8002 --host $(or $(API_HOST),localhost)
endif

live-session:
ifeq ($(IS_WINDOWS),1)
	@where watchfiles >nul 2>&1 && (set PYTHONPATH=$(shell cd) && watchfiles "python -m services.live-session-manager.src.main") || (set PYTHONPATH=$(shell cd) && python -m services.live-session-manager.src.main)
else
	@$(CHECK_WATCHFILES) && \
		PYTHONPATH=$(PWD) watchfiles "$(PYTHON) -m services.live-session-manager.src.main" || \
		PYTHONPATH=$(PWD) $(PYTHON) -m services.live-session-manager.src.main
endif

auth-broker:
ifeq ($(IS_WINDOWS),1)
	cd services\auth-broker && python main.py
else
	cd services/auth-broker && python main.py
endif

frontend:
	cd frontend/apps/demo && npm run dev

postman:
ifeq ($(IS_WINDOWS),1)
	@set PYTHONPATH=$(shell cd) && python services/api-gateway/scripts/generate_postman.py > postman_collection.json
else
	PYTHONPATH=$(PWD) python3 services/api-gateway/scripts/generate_postman.py > postman_collection.json
endif

openapi:
ifeq ($(IS_WINDOWS),1)
	cd services/api-gateway && set PYTHONPATH=$(shell cd)\.. && python -c "from src.main import app; import json; print(json.dumps(app.openapi()))" > ..\openapi.json
else
	cd services/api-gateway && PYTHONPATH=../../ python3 -c "from src.main import app; import json; print(json.dumps(app.openapi()))" > ../../openapi.json
endif

# ===================
# DATABASE
# ===================

migrate:
	$(PYTHON) -m alembic -c migrations/alembic.ini upgrade head

migrate-new:
ifeq ($(IS_WINDOWS),1)
	@set /p name="Migration name: " && $(PYTHON) -m alembic -c migrations/alembic.ini revision --autogenerate -m "!name!"
else
	@read -p "Migration name: " name; \
	$(PYTHON) -m alembic -c migrations/alembic.ini revision --autogenerate -m "$$name"
endif

migrate-down:
	$(PYTHON) -m alembic -c migrations/alembic.ini downgrade -1

migrate-reset:
	$(PYTHON) -m alembic -c migrations/alembic.ini downgrade base
	$(PYTHON) -m alembic -c migrations/alembic.ini upgrade head

# ===================
# TESTING
# ===================


test:
	pytest tests/ -v
	
test-all:
	pytest tests/unit/api-gateway -v
	pytest tests/unit/archiver -v
	pytest tests/unit/orchestrator -v
	pytest tests/unit/tool-workers -v
	pytest tests/unit/common -v
	pytest tests/integration/ -v

test-unit:
	pytest tests/unit/api-gateway -v
	pytest tests/unit/archiver -v
	pytest tests/unit/orchestrator -v
	pytest tests/unit/tool-workers -v
	pytest tests/unit/common -v

test-int:
	pytest tests/integration/ -v

test-phase1:
	pytest tests/integration/test_phase1_auth_flow.py -v

test-cov:
	pytest tests/ -v --cov=libs --cov=services --cov-report=html --cov-report=term

test-watch:
	ptw tests/ -- -v

# Reset database and run integration tests (clean slate)
test-int-clean: test-reset-db
	pytest tests/integration/ -v

# Run integration tests, then reset database after
test-int-clean-after:
	pytest tests/integration/ -v
	$(MAKE) test-reset-db

# Reset database only (drops all tables and runs migrations)
test-reset-db:
	@echo "Resetting database..."
ifeq ($(IS_WINDOWS),1)
	@set PYTHONPATH=. && alembic -c migrations/alembic.ini downgrade base && alembic -c migrations/alembic.ini upgrade head
else
	@PYTHONPATH=. alembic -c migrations/alembic.ini downgrade base && PYTHONPATH=. alembic -c migrations/alembic.ini upgrade head
endif
	@echo "Clearing Redis cache..."
	redis-cli -u $(REDIS_URL) FLUSHDB || true
	@echo "Database reset complete."

# ===================
# ISOLATED TEST ENVIRONMENT
# ===================
# Uses separate ports (8100) and database (agent_db_test)

# Start test services, run tests, stop services
test-isolated: test-services-up test-isolated-migrate
	@echo "Running integration tests against isolated environment..."
	RUN_INTEGRATION_TESTS=true TEST_API_BASE_URL=http://localhost:8100 pytest tests/ -v || ($(MAKE) test-services-down && exit 1)
	$(MAKE) test-services-down

# Start test services, run tests, keep services running
test-isolated-keep: test-services-up test-isolated-migrate
	@echo "Running integration tests against isolated environment..."
	TEST_API_BASE_URL=http://localhost:8100 pytest tests/ -v
	@echo "Test services still running on port 8100. Use 'make test-services-down' to stop."

# Start test infrastructure and API
test-services-up:
	@echo "Starting isolated test environment..."
	docker compose -f docker-compose.test.yml up -d
	@echo "Waiting for services to be healthy..."
	@$(SLEEP) 15
	@docker compose -f docker-compose.test.yml ps

# Stop test services and clean up volumes
test-services-down:
	@echo "Stopping test services..."
	docker compose -f docker-compose.test.yml down -v --remove-orphans --rmi local
	@echo "Test environment stopped and volumes removed."

# Run migrations on test database
test-isolated-migrate:
	@echo "Running migrations on test database..."
ifeq ($(IS_WINDOWS),1)
	@set DATABASE_URL=postgresql+asyncpg://agent:agent_secret@localhost:5433/agent_db_test && set PYTHONPATH=. && alembic -c migrations/alembic.ini upgrade head
else
	@DATABASE_URL=postgresql+asyncpg://agent:agent_secret@localhost:5433/agent_db_test \
		PYTHONPATH=. alembic -c migrations/alembic.ini upgrade head
endif

# Reset test database only
test-isolated-reset:
	@echo "Resetting test database..."
ifeq ($(IS_WINDOWS),1)
	@set DATABASE_URL=postgresql+asyncpg://agent:agent_secret@localhost:5433/agent_db_test && set PYTHONPATH=. && alembic -c migrations/alembic.ini downgrade base && alembic -c migrations/alembic.ini upgrade head
else
	@DATABASE_URL=postgresql+asyncpg://agent:agent_secret@localhost:5433/agent_db_test \
		PYTHONPATH=. alembic -c migrations/alembic.ini downgrade base && \
	DATABASE_URL=postgresql+asyncpg://agent:agent_secret@localhost:5433/agent_db_test \
		PYTHONPATH=. alembic -c migrations/alembic.ini upgrade head
endif
	@echo "Clearing test Redis cache..."
	redis-cli -p 6380 FLUSHDB || true
	@echo "Test database reset complete."

# ===================
# CODE QUALITY
# ===================

lint:
	ruff check libs/ services/
	cd frontend && npm run lint

format:
	ruff format libs/ services/
	cd frontend && npm run format

typecheck:
	mypy libs/ services/ --ignore-missing-imports

check: lint typecheck test

# ===================
# UTILITIES
# ===================

ps:
	@ps aux | grep "python -m services\." | grep -v grep || echo "No local services running."

shell-db:
	docker compose exec postgres psql -U agent -d agent_db

shell-redis:
	docker compose exec redis redis-cli

kafka-topics:
	docker compose exec kafka kafka-topics --list --bootstrap-server localhost:9092

kafka-consume:
ifeq ($(IS_WINDOWS),1)
	@set /p topic="Topic name: " && docker compose exec kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic !topic! --from-beginning
else
	@read -p "Topic name: " topic; \
	docker compose exec kafka kafka-console-consumer \
		--bootstrap-server localhost:9092 \
		--topic $$topic \
		--from-beginning
endif

# Build all Docker images
build:
	docker compose build

# Push images to registry
push:
	docker compose push