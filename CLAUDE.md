# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Event-driven, multi-tenant B2B2B (white-label/partner) AI agent SaaS platform. Three planes:

```
Frontend (React) → API Gateway (Control Plane, 8000) → Kafka → Orchestrator (Compute Plane) → LLM
                          ↓                                          ↓
                    Stream Edge (Data Plane, 8001) ←── Redis Pub/Sub ←──┘
```

**Services:**
- **api-gateway** (port 8000): FastAPI REST — auth, rate limiting, job creation → Kafka `agent.jobs`
- **stream-edge** (port 8001): FastAPI SSE streaming to clients via Redis Pub/Sub (`events:{job_id}`)
- **orchestrator**: Kafka consumer running the agent loop (Think → Act → Observe). Supports **suspend/resume**: serializes state to `job_snapshots`, dispatches tool tasks to Kafka, exits to free CPU, then resumes from snapshot when tool results arrive
- **tool-workers**: Stateless tool executors via Kafka (`agent.tools` → results in Redis)
- **archiver**: Moves completed events from Redis (hot) to PostgreSQL (cold)
- **frontend**: React 18 + TypeScript + Vite + Zustand + Tailwind CSS (port 3000)

**Key data flows:**
1. Chat request → API Gateway → Kafka `agent.jobs` → Orchestrator
2. Agent events → Redis Pub/Sub `events:{job_id}` → Stream Edge → SSE to client
3. Tool calls → Kafka `agent.tools` → Tool Workers → Redis `tool_result:{id}` → Orchestrator

**Reconnection strategy:** Stream Edge uses hot/cold fetch — recent history from Redis, archived messages from PostgreSQL, then live subscription.

## Multi-Tenancy & Auth Model

Three authentication tiers:
- **Platform Owner** (Super Admin): Master Admin Key (env var) — creates tenants and partner admins
- **Tenant Backend** (Machine): API Key (`sk_live_...`) stored hashed in `api_keys` table — creates jobs and users
- **End User** (Virtual): Short-lived JWT minted via tenant's API Key — connects to SSE streams

Rate limiting uses **waterfall inheritance**: check tenant limits first, then user-specific overrides (falling back to tenant defaults if none set). Limits tracked in Redis atomic counters.

An **Internal Transaction Token** (signed JWT) travels with Kafka payloads so workers can verify job legitimacy without access to the original HTTP request.

## Shared Libraries (`libs/`)

- `libs/common/` — Base config (`pydantic-settings`), structured logging (`structlog`), auth (JWT/HS256), exceptions
- `libs/db/` — SQLAlchemy 2.0 async models, session management. Three PostgreSQL schemas: `tenants`, `billing`, `jobs`
- `libs/llm/` — Provider-agnostic LLM abstraction. `get_provider("anthropic"|"openai")`. `ToolDefinition` with `.to_anthropic()`/`.to_openai()` format conversion
- `libs/messaging/` — Kafka async producer/consumer (with DLQ support) and Redis Pub/Sub client

## Development Commands

```bash
# Infrastructure
make infra              # Start postgres, redis, kafka only (for local dev)
make up / make down     # Start / stop all Docker services
make clean              # Remove containers, volumes, images

# Run services (each in separate terminal, requires infra)
make api                # API Gateway (uvicorn --reload, port 8000)
make stream             # Stream Edge (uvicorn --reload, port 8001)
make orchestrator       # Orchestrator
make workers            # Tool Workers
make archiver           # Archiver
make frontend           # React dev server (Vite, port 3000)

# Database (Alembic)
make migrate            # Run migrations
make migrate-new        # Create new migration (interactive)
make migrate-reset      # Downgrade base + upgrade head
make shell-db           # psql into PostgreSQL

# Testing
make test               # All tests: pytest tests/ -v
make test-unit          # Unit only: pytest tests/unit/ -v
make test-int           # Integration (requires infra): pytest tests/integration/ -v
make test-cov           # With coverage report
pytest tests/unit/test_foo.py -v    # Single file
pytest tests/ -k "test_name"        # Single test by name

# Code quality
make lint               # Ruff (Python) + ESLint (frontend)
make format             # Ruff format + Prettier
make typecheck          # MyPy
make check              # lint + typecheck + test

# Debug utilities
make shell-redis        # redis-cli
make kafka-topics       # List Kafka topics
```

All backend `make` targets set `PYTHONPATH=$(PWD)` for module resolution from repo root.

## Code Conventions

- **Python 3.12+** with type hints. MyPy: `disallow_untyped_defs = true`.
- **Ruff** for linting/formatting: line-length 88, isort integrated. Rules: E, W, F, I, B, C4, UP, ARG, SIM.
- **Async everywhere**: all I/O uses async/await (asyncpg, aiokafka, aioredis).
- **Structured logging**: `from libs.common import get_logger` — JSON in production, text in dev.
- **Multi-tenancy**: all operations require `tenant_id`, enforced via middleware.
- **Config pattern**: service configs extend base `pydantic-settings` from `libs/common/config.py`. Base: `get_settings()`. Service-specific: `get_config()`.
- **DB models**: SQLAlchemy 2.0 `Mapped[]` columns with `TimestampMixin`, organized into PostgreSQL schemas (`tenants`, `billing`, `jobs`).
- **Migrations**: sequential numbering `001_`, `002_` in `migrations/versions/`.
- **Tests**: `pytest-asyncio` with `asyncio_mode = "auto"` — no explicit `@pytest.mark.asyncio` needed.

## Adding New Tools

Extend `BaseTool` in `services/tool-workers/src/tools/`, then register in `registry.py → register_all()`:

```python
class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something"
    parameters = {"type": "object", "properties": {...}, "required": [...]}

    async def execute(self, arguments: dict, context: dict) -> str:
        # context provides job_id, tenant_id
        return "result"
```

## Key Files

| Concept | Path |
|---------|------|
| Base config | `libs/common/config.py` |
| DB models | `libs/db/models.py` |
| LLM abstraction | `libs/llm/base.py` |
| Agent execution loop | `services/orchestrator/src/engine/agent.py` |
| Agent state machine | `services/orchestrator/src/engine/state.py` |
| Tool base class | `services/tool-workers/src/tools/base.py` |
| Kafka topic setup | `infrastructure/docker/kafka/create-topics.sh` |
| DB init SQL | `infrastructure/docker/postgres/init.sql` |
| Frontend chat state | `frontend/src/hooks/useChat.ts` |
| Architecture design doc | `docs/rebuild.md` |
| Environment template | `.env.example` |
