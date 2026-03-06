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
- **orchestrator**: Kafka consumer running the agent loop (Think → Act → Observe). Supports **suspend/resume**: serializes state to `job_snapshots`, dispatches tool tasks to Kafka, exits to free CPU, then resumes from snapshot when tool results arrive. Emits tool results incrementally as each tool completes (tracked via `emitted_tool_result_ids` in state metadata)
- **tool-workers**: Stateless tool executors via Kafka (`agent.tools` → results in Redis). Real web search via **DuckDuckGo API** (default) or **Brave Search API** (optional, requires key)
- **archiver**: Moves completed events from Redis (hot) to PostgreSQL (cold). Handles **all 9 event types** (message, delta, tool_result, tool_call, start, complete, error, cancelled, suspended). **Periodic stream cleanup** (hourly, 24h retention). Sets `parent_message_id` on archived messages to maintain conversation tree structure
- **websocket-gateway** (port 8002): Bidirectional WebSocket server for live assistant features — routes audio/screen frames to live-session-manager, enforces per-tenant connection limits (50), max message size 5MB
- **live-session-manager** (port 8003): Orchestrates real-time voice + vision pipelines. STT via Deepgram (`nova-3`), TTS via ElevenLabs (`eleven_turbo_v2_5`). Frame processor downscales screen captures to 720×512. Dispatches agent jobs to Kafka. Tracks usage (audio seconds, screen frames, conversation turns) in PostgreSQL
- **auth-broker**: Authentication broker service
- **frontend**: React 18 + TypeScript monorepo (Vite + Tailwind CSS, port 3000). Structure: `frontend/apps/demo/` (demo app), `frontend/packages/chatbot-ui/` (published library `flowdit-chatbot-library`)

**Key data flows:**
1. Chat request → API Gateway → Kafka `agent.jobs` → Orchestrator
2. Agent events → Redis Pub/Sub `events:{job_id}` → Stream Edge → SSE to client
3. Tool calls → Kafka `agent.tools` → Tool Workers → Redis `tool_result:{id}` → Orchestrator
4. Tool confirmations → API Gateway → Kafka `agent.confirm` → Orchestrator (for CONFIRM_REQUIRED tools)
5. Live session → WebSocket Gateway → Live Session Manager → Deepgram (STT) / ElevenLabs (TTS) / Vision processor
6. Edit message → API Gateway → Creates new branch + Job → Kafka `agent.jobs` → Orchestrator (new branch response)

**SSE event types:** `message`, `delta`, `reasoning_delta`, `tool_call`, `tool_result`, `start`, `complete`, `error`, `cancelled`, `suspended`, `confirm_request`, `confirm_response`, `client_tool_call`

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

## Conversation Branching System

Conversations form a **tree of messages** (not a flat list). Editing a user message creates a new branch — a fork where multiple children share the same parent.

**Data model:**
- `ChatMessage.parent_message_id` — nullable FK to self, links child to parent
- `Conversation.active_branch` — JSONB mapping `{branch_point_msg_id: active_child_msg_id}`

**Example tree:**
```
msg-1 (user) → msg-2 (assistant) → msg-3 (user, original) → msg-4 (assistant)
                                  → msg-5 (user, edited)   → msg-6 (assistant)
```
Messages msg-1 and msg-2 are shared. At msg-2, there are two children (msg-3, msg-5). `active_branch` stores which child is active.

**Backend flow (edit):** `POST /conversations/{id}/edit-message` → validates user message → creates new Job + ChatMessage with same `parent_message_id` as original → builds conversation context up to branch point → updates `active_branch` → publishes to Kafka → returns SSE stream URL.

**Backend flow (switch):** `POST /conversations/{id}/switch-branch` → updates `active_branch` at specified branch point → returns updated conversation.

**Parent resolution (new messages):** When continuing a conversation, the chat endpoint uses `get_conversation_messages_tree()` to find the last message on the **active branch** as the parent for the new user message. This ensures new messages attach to the correct branch (not just the most recently created message globally).

**Tree walking:** `get_conversation_messages_tree()` in `conversation.py` builds adjacency list, walks from roots following `active_branch` selections. Falls back to flat chronological query for old conversations without `parent_message_id`. Annotates branch points with `branch_count`, `active_branch_index`, `branch_ids`.

**Frontend:** `BranchNavigator` component shows `< 1/3 >` at branch points. `MessageActions` component provides copy/reply/edit hover buttons. Edit triggers inline textarea, submits via `client.editMessage()`.

## Shared Libraries (`libs/`)

- `libs/common/` — Base config (`pydantic-settings`), structured logging (`structlog`), auth (JWT/HS256, partner/tenant API key generation), exceptions, **tool catalog** (`ToolBehavior`, `ToolMetadata`)
- `libs/db/` — SQLAlchemy 2.0 async models, session management. Three PostgreSQL schemas: `tenants`, `billing`, `jobs`
- `libs/llm/` — Provider-agnostic LLM abstraction. `get_provider("anthropic"|"openai")`. `ToolDefinition` with `.to_anthropic()`/`.to_openai()` format conversion
- `libs/messaging/` — Kafka async producer/consumer (with DLQ support) and Redis Pub/Sub client
- `libs/embeddings/` — OpenAI `text-embedding-3-small` (1536 dims). `embed_text()` / `embed_batch()` with chunking. `get_embedder()` singleton
- `libs/vectordb/` — Milvus abstraction with multi-tenant partitioning (one partition per `tenant_id`). `insert()`, `search()`, `delete()`. Auto-creates collection and partitions

## Development Commands

**Important:** Activate the virtual environment before running any Python/pytest/make command:
```bash
source .venv/bin/activate
```

```bash
# Infrastructure
make infra              # Start postgres, redis, kafka only (for local dev)
make up / make down     # Start / stop all Docker services
make clean              # Remove containers, volumes, images
make restart            # Restart all services
make restart-and-migrate # Restart + run migrations

# Run services (each in separate terminal, requires infra)
make api                # API Gateway (uvicorn --reload, port 8000)
make stream             # Stream Edge (uvicorn --reload, port 8001)
make orchestrator       # Orchestrator (auto-reloads with watchfiles)
make workers            # Tool Workers (auto-reloads with watchfiles)
make archiver           # Archiver (auto-reloads with watchfiles)
make ws                 # WebSocket Gateway (port 8002)
make live-session       # Live Session Manager (port 8003)
make auth-broker        # Auth Broker
make frontend           # React demo app dev server (Vite, port 3000)
make frontend-chatbot-ui # Chatbot-UI library watch mode build

# Database (Alembic)
make migrate            # Run migrations
make migrate-new        # Create new migration (interactive)
make migrate-reset      # Downgrade base + upgrade head
make migrate-down       # Downgrade one migration
make shell-db           # psql into PostgreSQL

# Testing
make test               # All tests: pytest tests/ -v
make test-unit          # Unit only (runs each service subdir separately)
make test-int           # Integration (requires infra): pytest tests/integration/ -v
make test-cov           # With coverage report
make test-isolated      # Isolated test env (separate DB on port 5433/8100): start → test → stop
make test-isolated-keep # Same but keeps test services running after
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
make kafka-consume      # Consume from a Kafka topic (interactive)
make postman            # Generate Postman collection
make openapi            # Generate OpenAPI schema
make ps                 # List local running services
```

All backend `make` targets set `PYTHONPATH=$(PWD)` for module resolution from repo root. Supports both Linux/macOS and Windows (PowerShell).

## Code Conventions

- **Python 3.12+** with type hints. MyPy: `disallow_untyped_defs = true`.
- **Ruff** for linting/formatting: line-length 88, isort integrated. Rules: E, W, F, I, B, C4, UP, ARG, SIM.
- **Async everywhere**: all I/O uses async/await (asyncpg, aiokafka, aioredis).
- **Structured logging**: `from libs.common import get_logger` — JSON in production, text in dev.
- **Multi-tenancy**: all operations require `tenant_id`, enforced via middleware.
- **Config pattern**: service configs extend base `pydantic-settings` from `libs/common/config.py`. Base: `get_settings()`. Service-specific: `get_config()`.
- **DB models**: SQLAlchemy 2.0 `Mapped[]` columns with `TimestampMixin`, organized into PostgreSQL schemas (`tenants`, `billing`, `jobs`).
- **Migrations**: sequential numbering in `migrations/versions/`. Current: `001_tenants_users`, `002_pricing_ledger`, `003_jobs_messages`, `004_partners`, `005_billing_plans`, `006_user_tool_preferences`, `007_file_uploads`, `008_conversations`, `009_file_analysis_description`, `010_knowledge_base`, `011_live_sessions`, `012_file_extracted_text`, `013_message_branching`.
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
| Conversations router (CRUD + branching) | `services/api-gateway/src/routers/conversations.py` |
| Conversation service (tree walking) | `services/api-gateway/src/services/conversation.py` |
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
| File upload/download router | `services/api-gateway/src/routers/files.py` |
| Live session router | `services/api-gateway/src/routers/live.py` |
| Tools router (client results) | `services/api-gateway/src/routers/tools.py` |
| Jobs router | `services/api-gateway/src/routers/jobs.py` |
| Users router | `services/api-gateway/src/routers/users.py` |
| File storage service | `services/api-gateway/src/services/file_storage.py` |
| Background job scheduler | `services/api-gateway/src/jobs/scheduler.py` |
| Agent execution loop | `services/orchestrator/src/engine/agent.py` |
| Agent state machine | `services/orchestrator/src/engine/state.py` |
| Agent state serializer | `services/orchestrator/src/engine/serializer.py` |
| Phase executor (tool resumption) | `services/orchestrator/src/engine/phase_executor.py` |
| Distributed state lock | `services/orchestrator/src/services/state_lock.py` |
| Job handler (new job entry point) | `services/orchestrator/src/handlers/job_handler.py` |
| Resume handler (suspend/resume) | `services/orchestrator/src/handlers/resume_handler.py` |
| Confirm handler (tool confirmations) | `services/orchestrator/src/handlers/confirm_handler.py` |
| Tool handler (dispatch logic) | `services/orchestrator/src/handlers/tool_handler.py` |
| User response handler | `services/orchestrator/src/handlers/user_response_handler.py` |
| Multi-phase execution | `services/orchestrator/src/engine/phases.py` |
| Tool catalog (behavior config) | `libs/common/tool_catalog.py` |
| Tool base class | `services/tool-workers/src/tools/base.py` |
| Tool asset loader | `services/tool-workers/src/tools/assets/loader.py` |
| Web search tool (real API) | `services/tool-workers/src/tools/web_search.py` |
| Knowledge base tools | `services/tool-workers/src/tools/save_to_knowledge_base.py`, `search_knowledge_base.py`, `delete_from_knowledge_base.py` |
| File tools | `services/tool-workers/src/tools/analyze_file.py`, `get_file_description.py` |
| Tool workers config | `services/tool-workers/src/config.py` |
| WebSocket gateway main | `services/websocket-gateway/src/main.py` |
| Live session manager main | `services/live-session-manager/src/main.py` |
| Live session state | `services/live-session-manager/src/session/state.py` |
| Deepgram STT client | `services/live-session-manager/src/stt/deepgram_client.py` |
| ElevenLabs TTS client | `services/live-session-manager/src/tts/elevenlabs_client.py` |
| Archiver postgres writer | `services/archiver/src/services/postgres_writer.py` |
| Kafka topic setup | `infrastructure/docker/kafka/create-topics.sh` |
| DB init SQL | `infrastructure/docker/postgres/init.sql` |
| Chatbot-UI library entry | `frontend/packages/chatbot-ui/src/index.ts` |
| Chatbot high-level component | `frontend/packages/chatbot-ui/src/components/Chatbot/Chatbot.tsx` |
| Chat client (SSE streaming) | `frontend/packages/chatbot-ui/src/api/RealChatClient.ts` |
| Chat client types/interfaces | `frontend/packages/chatbot-ui/src/api/types.ts` |
| useChat hook (message state) | `frontend/packages/chatbot-ui/src/hooks/useChat.ts` |
| MessageBubble (render + branching) | `frontend/packages/chatbot-ui/src/components/MessageBubble/MessageBubble.tsx` |
| MessageActions (copy/reply/edit) | `frontend/packages/chatbot-ui/src/components/MessageActions/MessageActions.tsx` |
| BranchNavigator (branch switching) | `frontend/packages/chatbot-ui/src/components/BranchNavigator/BranchNavigator.tsx` |
| ConfirmButtons component | `frontend/packages/chatbot-ui/src/components/ConfirmButtons/ConfirmButtons.tsx` |
| Architecture design doc | `docs/rebuild.md` |
| Multi-phase agent design | `docs/multi-phase-agent.md` |
| Project plan | `docs/plan.md` |
| Tool management plan | `docs/tools-plan.md` |
| Environment template | `.env.example` |
