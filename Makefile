.PHONY: help install dev up down logs clean test migrate lint format

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
	@echo "  make test        - Run all tests"
	@echo "  make test-unit   - Run unit tests only"
	@echo "  make test-int    - Run integration tests"
	@echo "  make test-phase1 - Run Phase 1 authentication tests"
	@echo "  make test-cov    - Run tests with coverage"
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

# ===================
# INFRASTRUCTURE
# ===================

up:
	docker compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 10
	@docker compose ps

down:
	docker compose down

logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api-gateway

logs-stream:
	docker compose logs -f stream-edge

logs-orchestrator:
	docker compose logs -f orchestrator

clean:
	docker compose down --volumes --rmi local
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
	@echo "Run each service in separate terminals:"
	@echo "  make api"
	@echo "  make stream"
	@echo "  make orchestrator"
	@echo "  make frontend"

api:
	PYTHONPATH=$(PWD) uvicorn services.api-gateway.src.main:app --reload --port 8000

stream:
	PYTHONPATH=$(PWD) uvicorn services.stream-edge.src.main:app --reload --port 8001

orchestrator:
	PYTHONPATH=$(PWD) python -m services.orchestrator.src.main

workers:
	PYTHONPATH=$(PWD) python -m services.tool-workers.src.main

archiver:
	PYTHONPATH=$(PWD) python -m services.archiver.src.main

frontend:
	cd frontend && npm run dev

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