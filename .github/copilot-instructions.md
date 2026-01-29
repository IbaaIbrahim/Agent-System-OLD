# Agent System - AI Coding Instructions

## Architecture Overview

This is an **event-driven, multi-tenant AI agent system** with a control/data plane separation:

```
Frontend (React) → API Gateway (Control) → Kafka → Orchestrator → LLM
                            ↓                         ↓
                      Stream Edge (Data) ←── Redis Pub/Sub ←──┘
```

### Service Responsibilities
- **api-gateway** (port 8000): REST API, auth, rate limiting, job creation → publishes to `agent.jobs` Kafka topic
- **stream-edge** (port 8001): SSE streaming to clients via Redis Pub/Sub subscriptions
- **orchestrator**: Kafka consumer that runs the agent loop (LLM calls + tool execution)
- **tool-workers**: Executes tools via Kafka (`agent.tools` → `agent.tool-results`)
- **archiver**: Persists events from Redis to PostgreSQL

### Key Data Flows
1. Chat request → API Gateway → Kafka `agent.jobs` → Orchestrator
2. Agent events → Redis Pub/Sub `events:{job_id}` → Stream Edge → SSE to client
3. Tool calls → Kafka `agent.tools` → Tool Workers → `agent.tool-results` → Orchestrator

## Development Commands

```bash
make infra          # Start only infrastructure (postgres, redis, kafka)
make dev            # Start infra + instructions for running services locally
make api            # Run API Gateway locally (uvicorn --reload)
make stream         # Run Stream Edge locally
make orchestrator   # Run Orchestrator locally
make frontend       # Run React frontend (Vite)
```

Database and debugging:
```bash
make migrate        # Run Alembic migrations
make shell-db       # psql into PostgreSQL
make shell-redis    # redis-cli
make kafka-topics   # List Kafka topics
```

## Code Patterns

### Configuration
All services use `pydantic-settings` loaded from environment. Service configs extend base settings:
```python
from libs.common.config import get_settings  # Base settings
from .config import get_config               # Service-specific config
```

### Database Models
Models use SQLAlchemy 2.0 mapped columns with PostgreSQL schemas (`tenants`, `billing`, `jobs`):
```python
class MyModel(Base, TimestampMixin):
    __table_args__ = {"schema": "jobs"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
```

### Messaging Patterns
- **Kafka**: Use `libs.messaging.kafka` for async produce/consume with DLQ support
- **Redis Pub/Sub**: Use `libs.messaging.redis.RedisPubSub` for real-time events
- Channel naming: `events:{job_id}` for job-specific streams

### LLM Integration
Provider-agnostic via `libs.llm`:
```python
from libs.llm import LLMProvider, get_provider
provider = get_provider("anthropic")  # or "openai"
response = await provider.complete(messages, tools=tools)
```
Tool definitions convert between formats via `ToolDefinition.to_anthropic()` / `.to_openai()`

### Adding New Tools
Extend `BaseTool` in `services/tool-workers/src/tools/`:
```python
class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something"
    parameters = {"type": "object", "properties": {...}, "required": [...]}

    async def execute(self, arguments: dict, context: dict) -> str:
        # context has job_id, tenant_id
        return "result"
```
Register in `registry.py` → `register_all()`

## Project Conventions

- **Python 3.12+** with type hints required (`mypy --strict` settings)
- **Ruff** for linting/formatting (line-length 88, isort integrated)
- **Async everywhere**: All I/O operations use async/await
- **Structured logging**: `from libs.common import get_logger` → JSON format in production
- **Multi-tenancy**: All operations require `tenant_id`, passed via middleware/headers
- Migrations: Sequential numbering `001_`, `002_` in `migrations/versions/`

## Testing

```bash
make test           # All tests
make test-unit      # Unit tests only
make test-int       # Integration tests (requires infra running)
make test-cov       # With coverage report
```
Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.

## Key Files Reference

| Concept | Location |
|---------|----------|
| Shared config | [libs/common/config.py](libs/common/config.py) |
| Database models | [libs/db/models.py](libs/db/models.py) |
| LLM abstraction | [libs/llm/base.py](libs/llm/base.py) |
| Agent execution loop | [services/orchestrator/src/engine/agent.py](services/orchestrator/src/engine/agent.py) |
| Agent state machine | [services/orchestrator/src/engine/state.py](services/orchestrator/src/engine/state.py) |
| Tool base class | [services/tool-workers/src/tools/base.py](services/tool-workers/src/tools/base.py) |
| Kafka topics | [infrastructure/docker/kafka/create-topics.sh](infrastructure/docker/kafka/create-topics.sh) |
| Frontend state | [frontend/src/hooks/useChat.ts](frontend/src/hooks/useChat.ts) (Zustand) |
