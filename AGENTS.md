# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Event-driven, multi-tenant B2B2B (white-label/partner) AI agent SaaS platform. Three planes:

```
Frontend (React) → API Gateway (Control Plane, 8000) → Kafka → Orchestrator (Compute Plane) → LLM
                          ↓                                          ↓
                    Stream Edge (Data Plane, 8001) ←── Redis Pub/Sub ←──┘

Frontend (React) → WebSocket Gateway (8002) → Live Session Manager (8003) → Deepgram/ElevenLabs
```

**Services:**
- **api-gateway** (port 8000): FastAPI REST — auth, rate limiting, job creation → Kafka `agent.jobs`
- **stream-edge** (port 8001): FastAPI SSE streaming to clients via Redis Pub/Sub (`events:{job_id}`), secured with one-time tokens (OTT)
- **orchestrator**: Kafka consumer running the agent loop (Think → Act → Observe). Supports **suspend/resume**: serializes state to `job_snapshots`, dispatches tool tasks to Kafka, exits to free CPU, then resumes from snapshot when tool results arrive
- **tool-workers**: Stateless tool executors via Kafka (`agent.tools` → results in Redis). Real web search via **DuckDuckGo API** (default) or **Brave Search API** (optional, requires key)
- **archiver**: Moves completed events from Redis (hot) to PostgreSQL (cold). Handles **all 9 event types** (message, delta, tool_result, tool_call, start, complete, error, cancelled, suspended). **Periodic stream cleanup** (hourly, 24h retention)
- **websocket-gateway** (port 8002): Bidirectional WebSocket server for live assistant features — routes audio/screen frames to live-session-manager, enforces per-tenant connection limits (50), max message size 5MB
- **live-session-manager** (port 8003): Orchestrates real-time voice + vision pipelines. STT via Deepgram (`nova-3`), TTS via ElevenLabs (`eleven_turbo_v2_5`). Frame processor downscales screen captures to 720×512. Dispatches agent jobs to Kafka. Tracks usage (audio seconds, screen frames, conversation turns) in PostgreSQL
- **frontend**: React 18 + TypeScript + Vite + Zustand + Tailwind CSS (port 3000)

**Key data flows:**
1. Chat request → API Gateway → Kafka `agent.jobs` → Orchestrator
2. Agent events → Redis Pub/Sub `events:{job_id}` → Stream Edge → SSE to client
3. Tool calls → Kafka `agent.tools` → Tool Workers → Redis `tool_result:{id}` → Orchestrator
4. Tool confirmations → API Gateway → Kafka `agent.confirm` → Orchestrator (for CONFIRM_REQUIRED tools)
5. Live session → WebSocket Gateway → Live Session Manager → Deepgram (STT) / ElevenLabs (TTS) / Vision processor

**SSE event types:** `message`, `delta`, `tool_call`, `tool_result`, `start`, `complete`, `error`, `cancelled`, `suspended`, `confirm_request`, `confirm_response`

**Reconnection strategy:** Stream Edge uses hot/cold fetch — recent history from Redis, archived messages from PostgreSQL, then live subscription.

## Tool Management System

Unified tool configuration with behavior-based execution flow. Tools are defined in `libs/common/tool_catalog.py`.

**Tool Behaviors:**

| Behavior | Description | Execution |
|----------|-------------|-----------|
| `AUTO_EXECUTE` | Executes automatically when LLM calls it | Plan-based, always on |
| `USER_ENABLED` | Requires user to toggle ON in UI | Plan-based + user toggle |
| `CONFIRM_REQUIRED` | Requires user approval per-call | Emits `confirm_request` SSE event |
| `CLIENT_SIDE` | Executes in frontend | Sent to client |

**Confirm flow for CONFIRM_REQUIRED tools:**
1. LLM decides to call tool → Orchestrator emits `confirm_request` SSE event
2. Frontend displays Confirm/Cancel buttons
3. User clicks → `POST /confirm-response` → Kafka `agent.confirm`
4. Orchestrator handles: confirmed → dispatch to workers; rejected → inject rejection result

**Tool assets:** Each tool can have assets in `services/tool-workers/src/tools/assets/{tool_name}/` (e.g., schema.json, prompts). Use `load_json_asset()` / `load_text_asset()` from `assets/loader.py`.

## Multi-Tenancy & Auth Model

Four authentication tiers (B2B2B white-label/partner model):

| Priority | Tier | Auth Method | Token Format | Access Scope |
|----------|------|-------------|--------------|--------------|
| 1 | **Platform Owner** (Super Admin) | Master Admin Key (env var) | `Bearer {MASTER_ADMIN_KEY}` | Full system — creates partners, tenants, manages everything |
| 2 | **Partner** (White-label Owner) | Partner API Key | `Bearer pk-agent-*` | Own tenants only — creates/manages tenants, generates tenant API keys |
| 3 | **Tenant Backend** (Machine) | Tenant API Key | `Bearer sk-agent-*` | Tenant resources — creates users, manages settings, creates jobs |
| 4 | **End User** (Virtual) | Short-lived JWT (1hr) | `Bearer {jwt}` | User-scoped — creates jobs, streams events |

`partner_id` is nullable on Tenant for backward compatibility — existing tenants without a partner continue working unchanged.

Rate limiting uses a **four-tier waterfall**: Partner RPM → Tenant RPM → User RPM → TPM (partner + tenant levels). Tenant limits inherit from partner, then system defaults. Limits tracked in Redis sorted sets with sliding windows.

An **Internal Transaction Token v2** (signed JWT, HS256, 10-minute TTL) travels with Kafka payloads so workers can verify job legitimacy. Includes `partner_id`, `tenant_id`, `job_id`, `credit_check_passed`, `limits`, and `trace_id`.

## Shared Libraries (`libs/`)

- `libs/common/` — Base config (`pydantic-settings`), structured logging (`structlog`), auth (JWT/HS256, partner/tenant API key generation), exceptions, **tool catalog** (`ToolBehavior`, `ToolMetadata`)
- `libs/db/` — SQLAlchemy 2.0 async models, session management. Three PostgreSQL schemas: `tenants`, `billing`, `jobs`
- `libs/llm/` — Provider-agnostic LLM abstraction. `get_provider("anthropic"|"openai")`. `ToolDefinition` with `.to_anthropic()`/`.to_openai()` format conversion
- `libs/messaging/` — Kafka async producer/consumer (with DLQ support) and Redis Pub/Sub client
- `libs/embeddings/` — OpenAI `text-embedding-3-small` (1536 dims). `embed_text()` / `embed_batch()` with chunking. `get_embedder()` singleton
- `libs/vectordb/` — Milvus abstraction with multi-tenant partitioning (one partition per `tenant_id`). `insert()`, `search()`, `delete()`. Auto-creates collection and partitions

## Development Commands

**Important:** Always activate the virtual environment before running any Python/pytest/make command:
```bash
source /root/Agent-System-Claude/.venv/bin/activate
```

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
make ws-gateway         # WebSocket Gateway (port 8002)
make live-session       # Live Session Manager (port 8003)
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
- **Migrations**: sequential numbering in `migrations/versions/`. Current: `001_tenants_users`, `002_pricing_ledger`, `003_jobs_messages`, `004_partners`, `005_billing_plans`, `006_user_tool_preferences`, `007_file_uploads`, `008_conversations`, `009_file_analysis_description`, `010_knowledge_base`, `011_live_sessions`, `012_file_extracted_text`.
- **Tests**: `pytest-asyncio` with `asyncio_mode = "auto"` — no explicit `@pytest.mark.asyncio` needed. Organized in subdirectories: `tests/unit/{api-gateway,common,orchestrator,archiver,tool-workers}/`.
- **Test imports**: Hyphenated service directories (e.g. `services/api-gateway`) require `sys.path.insert(0, "services/api-gateway")` before importing `src.*` modules in unit tests.

## Adding New Tools

1. Extend `BaseTool` in `services/tool-workers/src/tools/`, set behavior and plan feature:

```python
from libs.common.tool_catalog import ToolBehavior

class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something"
    parameters = {"type": "object", "properties": {...}, "required": [...]}
    behavior = ToolBehavior.AUTO_EXECUTE  # or USER_ENABLED, CONFIRM_REQUIRED
    required_plan_feature = "tools.my_tool"  # Plan feature for access control

    async def execute(self, arguments: dict, context: dict) -> str:
        # context provides job_id, tenant_id
        return "result"
```

2. Register in `registry.py → register_all()`

3. Add to `libs/common/tool_catalog.py → TOOL_CATALOG` with metadata (for CONFIRM_REQUIRED tools, include `confirm_button_label` and `confirm_description_template`)

4. (Optional) Add assets in `services/tool-workers/src/tools/assets/{tool_name}/`

## Key Files

| Concept | Path |
|---------|------|
| Base config | `libs/common/config.py` |
| Auth utilities (JWT, API keys) | `libs/common/auth.py` |
| DB models (all entities) | `libs/db/models.py` |
| LLM abstraction | `libs/llm/base.py` |
| Embeddings (OpenAI) | `libs/embeddings/openai_embedder.py` |
| Vector DB (Milvus) | `libs/vectordb/milvus_client.py` |
| Auth middleware (4-tier) | `services/api-gateway/src/middleware/auth.py` |
| Rate limiting middleware | `services/api-gateway/src/middleware/rate_limit.py` |
| Partner admin endpoints | `services/api-gateway/src/routers/partners.py` |
| Tenant admin endpoints | `services/api-gateway/src/routers/admin.py` |
| Chat/job router | `services/api-gateway/src/routers/chat.py` |
| Tenant API key cache | `services/api-gateway/src/services/api_key_cache.py` |
| Partner API key cache | `services/api-gateway/src/services/partner_api_key_cache.py` |
| Billing service | `services/api-gateway/src/services/billing.py` |
| Wallet service | `services/api-gateway/src/services/wallet.py` |
| Subscription service | `services/api-gateway/src/services/subscription.py` |
| Feature service | `services/api-gateway/src/services/feature.py` |
| Wallet router | `services/api-gateway/src/routers/wallet.py` |
| Plans router | `services/api-gateway/src/routers/plans.py` |
| Subscriptions router | `services/api-gateway/src/routers/subscriptions.py` |
| Top-ups router | `services/api-gateway/src/routers/topups.py` |
| Features router | `services/api-gateway/src/routers/features.py` |
| Background job scheduler | `services/api-gateway/src/jobs/scheduler.py` |
| Agent execution loop | `services/orchestrator/src/engine/agent.py` |
| Agent state machine | `services/orchestrator/src/engine/state.py` |
| Agent state serializer | `services/orchestrator/src/engine/serializer.py` |
| Distributed state lock | `services/orchestrator/src/services/state_lock.py` |
| Resume handler (suspend/resume) | `services/orchestrator/src/handlers/resume_handler.py` |
| Confirm handler (tool confirmations) | `services/orchestrator/src/handlers/confirm_handler.py` |
| Tool handler (dispatch logic) | `services/orchestrator/src/handlers/tool_handler.py` |
| Tool catalog (behavior config) | `libs/common/tool_catalog.py` |
| Tool base class | `services/tool-workers/src/tools/base.py` |
| Tool asset loader | `services/tool-workers/src/tools/assets/loader.py` |
| Web search tool (real API) | `services/tool-workers/src/tools/web_search.py` |
| Knowledge base tools | `services/tool-workers/src/tools/save_to_knowledge_base.py`, `search_knowledge_base.py`, `delete_from_knowledge_base.py` |
| File tools | `services/tool-workers/src/tools/analyze_file.py`, `get_file_description.py` |
| Tool workers config | `services/tool-workers/src/config.py` |
| WebSocket gateway main | `services/websocket-gateway/src/main.py` |
| WebSocket gateway config | `services/websocket-gateway/src/config.py` |
| Live session manager main | `services/live-session-manager/src/main.py` |
| Live session manager config | `services/live-session-manager/src/config.py` |
| Live session state | `services/live-session-manager/src/session/state.py` |
| Deepgram STT client | `services/live-session-manager/src/stt/deepgram_client.py` |
| ElevenLabs TTS client | `services/live-session-manager/src/tts/elevenlabs_client.py` |
| Screen frame processor | `services/live-session-manager/src/vision/frame_processor.py` |
| Archiver postgres writer | `services/archiver/src/services/postgres_writer.py` |
| Kafka topic setup | `infrastructure/docker/kafka/create-topics.sh` |
| DB init SQL | `infrastructure/docker/postgres/init.sql` |
| OTT unit tests | `tests/unit/common/test_stream_ott.py` |
| Suspend/resume unit tests | `tests/unit/orchestrator/test_suspend_resume.py` |
| Suspend/resume integration tests | `tests/integration/test_suspend_resume_flow.py` |
| Web search unit tests | `tests/unit/tool-workers/test_web_search.py` |
| Archiver events unit tests | `tests/unit/archiver/test_archiver_events.py` |
| Frontend chat state | `frontend/src/hooks/useChat.ts` |
| Frontend chat client | `frontend/apps/demo/src/api/RealChatClient.ts` |
| Frontend live WebSocket client | `frontend/apps/demo/src/api/LiveWebSocketClient.ts` |
| ConfirmButtons component | `frontend/packages/chatbot-ui/src/components/ConfirmButtons/ConfirmButtons.tsx` |
| MessageBubble component | `frontend/packages/chatbot-ui/src/components/MessageBubble/MessageBubble.tsx` |
| Architecture design doc | `docs/rebuild.md` |
| Project plan | `docs/plan.md` |
| Tool management plan | `docs/tools-plan.md` |
| Environment template | `.env.example` |

## Cursor Cloud specific instructions

### Environment overview

This is a Docker Compose-based multi-service platform. All services (17 containers) run via `make up` / `sudo docker compose up -d`. See `CLAUDE.md` for full architecture, dev commands, and code conventions.

### Starting services

- **All services via Docker**: `sudo docker compose up -d` (equivalent to `make up` but requires `sudo` in Cloud Agent VMs where Docker runs as root).
- **Infrastructure only** (for local Python dev): `sudo docker compose up -d postgres redis zookeeper kafka kafka-init pgadmin`
- After infrastructure is up, run `make migrate` to apply Alembic migrations.
- Individual Python services can be run locally with `make api`, `make stream`, etc. (see Makefile).

### Key gotchas

- **`docker compose` requires `sudo`** in the Cloud Agent VM because Docker runs as root.
- **`.env` must exist** before running `docker compose`. Copy from `.env.example`. The following vars must be non-empty for `docker compose` to parse: `DEEPGRAM_API_KEY`, `TENANT_API_KEY`, `USER_ID`. Set placeholder values if you don't have real keys.
- **Frontend `npm run lint`** does not exist in the workspace root `package.json`. The `make lint` target's frontend portion will fail; Python lint (`ruff check libs/ services/`) works fine.
- **`PYTHONPATH`**: all `make` targets set `PYTHONPATH=$(PWD)`. When running pytest or Python directly, ensure `PYTHONPATH` points to the repo root.
- **`$HOME/.local/bin`** must be on PATH for pip-installed CLI tools (ruff, mypy, alembic, pytest, uvicorn).
- **Unit tests** run without infrastructure: `PYTHONPATH=$(pwd) pytest tests/unit/ -v` (231 tests).
- **Integration tests** require running infrastructure (`make infra` or `make up`) plus migrations, and `RUN_INTEGRATION_TESTS=true`.

### Hello world API flow

1. Create tenant: `POST /api/admin/tenants` with `Authorization: Bearer $MASTER_ADMIN_KEY` (body needs `name`, `slug`).
2. Generate API key: `POST /api/admin/tenants/{id}/api-keys`.
3. Create user: `POST /api/v1/users` with tenant API key (body needs `external_id`, `email`, `name`).
4. Get JWT: `POST /api/v1/auth/token` with tenant API key and `user_id`.
5. Chat: `POST /api/v1/chat/completions` with JWT (body: `{"messages": [{"role":"user","content":"..."}]}`).
6. Stream response: connect SSE to the `stream_url` returned by the chat endpoint.

### Auth-broker setup for frontend demo

The **auth-broker** (port 8003) is a convenience proxy that the frontend uses to obtain JWT tokens. It requires real `TENANT_API_KEY` and `USER_ID` values in `.env` — placeholder values will cause the frontend to fail silently (no LLM responses, no conversation persistence).

To set up:
1. Create a tenant, API key, and user via the admin API (see "Hello world API flow" above).
2. Set `TENANT_API_KEY` and `USER_ID` in `.env` with the real values.
3. Restart the auth-broker: `sudo docker compose up -d auth-broker`.
4. Verify: `curl -s -X POST http://localhost:8003/request-token` should return a valid JWT.

The frontend Docker container (port 8005) uses env vars `VITE_AUTH_BROKER_URL`, `VITE_API_BASE_URL`, and `VITE_WS_URL` baked in at build time. The defaults in `.env.example` point to `localhost` which works for the Docker setup.
