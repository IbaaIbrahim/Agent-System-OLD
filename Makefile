# Include .env file if it exists (loads environment variables like API_HOST)
-include .env
export

.PHONY: help install dev up down logs clean test migrate lint format api stream orchestrator workers archiver frontend postman openapi

# Default target
help:
	@echo "Agent System - Development Commands"
	@echo ""
	@echo "Infrastructure:"
	@echo "  make up          - Start all Docker services"
	@echo "  make down        - Stop all Docker services"
	@echo "  make logs        - Tail logs from all services"
	@echo "  make clean       - Remove containers, volumes, and images"
	@echo "  make restart     - Restart all services"
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
	@echo "  make frontend    - Run Frontend locally"
	@echo "  make postman      - Generate Postman Collection"
	@echo "  make openapi      - Generate OpenAPI Schema"

# ===================
# INFRASTRUCTURE
# ===================

up:
	docker compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 10
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

# Infrastructure only (for local development)
infra:
	docker compose up -d postgres redis zookeeper kafka kafka-init
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
	@echo "Run each service in separate terminals:"
	@echo "  make api"
	@echo "  make stream"
	@echo "  make orchestrator"
	@echo "  make frontend"

api:
	PYTHONPATH=$(PWD) uvicorn services.api-gateway.src.main:app --reload --port 8000 --host $(or $(API_HOST),localhost)

stream:
	PYTHONPATH=$(PWD) uvicorn services.stream-edge.src.main:app --reload --port 8001

orchestrator:
	PYTHONPATH=$(PWD) python -m services.orchestrator.src.main

workers:
	PYTHONPATH=$(PWD) python -m services.tool-workers.src.main

archiver:
	PYTHONPATH=$(PWD) python -m services.archiver.src.main

frontend:
	cd frontend/apps/demo && npm run dev

postman:
	PYTHONPATH=$(PWD) python3 services/api-gateway/scripts/generate_postman.py > postman_collection.json

openapi:
	cd services/api-gateway && PYTHONPATH=../../ python3 -c "from src.main import app; import json; print(json.dumps(app.openapi()))" > ../../openapi.json

# ===================
# DATABASE
# ===================

migrate:
	PYTHONPATH=. alembic -c migrations/alembic.ini upgrade head

migrate-new:
	@read -p "Migration name: " name; \
	PYTHONPATH=. alembic -c migrations/alembic.ini revision --autogenerate -m "$$name"

migrate-down:
	PYTHONPATH=. alembic -c migrations/alembic.ini downgrade -1

migrate-reset:
	PYTHONPATH=. alembic -c migrations/alembic.ini downgrade base
	PYTHONPATH=. alembic -c migrations/alembic.ini upgrade head

# ===================
# TESTING
# ===================

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

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
	PYTHONPATH=. alembic -c migrations/alembic.ini downgrade base
	PYTHONPATH=. alembic -c migrations/alembic.ini upgrade head
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
	@sleep 15
	@docker compose -f docker-compose.test.yml ps

# Stop test services and clean up volumes
test-services-down:
	@echo "Stopping test services..."
	docker compose -f docker-compose.test.yml down -v --remove-orphans --rmi local
	@echo "Test environment stopped and volumes removed."

# Run migrations on test database
test-isolated-migrate:
	@echo "Running migrations on test database..."
	DATABASE_URL=postgresql+asyncpg://agent:agent_secret@localhost:5433/agent_db_test \
		PYTHONPATH=. alembic -c migrations/alembic.ini upgrade head

# Reset test database only
test-isolated-reset:
	@echo "Resetting test database..."
	DATABASE_URL=postgresql+asyncpg://agent:agent_secret@localhost:5433/agent_db_test \
		PYTHONPATH=. alembic -c migrations/alembic.ini downgrade base
	DATABASE_URL=postgresql+asyncpg://agent:agent_secret@localhost:5433/agent_db_test \
		PYTHONPATH=. alembic -c migrations/alembic.ini upgrade head
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

shell-db:
	docker compose exec postgres psql -U agent -d agent_db

shell-redis:
	docker compose exec redis redis-cli

kafka-topics:
	docker compose exec kafka kafka-topics --list --bootstrap-server localhost:9092

kafka-consume:
	@read -p "Topic name: " topic; \
	docker compose exec kafka kafka-console-consumer \
		--bootstrap-server localhost:9092 \
		--topic $$topic \
		--from-beginning

# Build all Docker images
build:
	docker compose build

# Push images to registry
push:
	docker compose push