# Next Phases: Technical Implementation Roadmap (Phases 4-6)

This document provides the concrete, file-level implementation steps for completing the agent system. Phases 1, 2, 2.5 (B2B2B Partners), 3 (Comprehensive Billing System), 3.5 (Stream-Edge OTT Security), and 4 (Orchestrator Suspend/Resume) are done — see [plan.md](plan.md) for their retrospective.

---

## Current System Snapshot

### What works today
- **API Gateway**: Four-tier auth (super admin / partner `pk-agent-*` / tenant `sk-agent-*` / JWT), partner management with full CRUD + API keys, admin CRUD with partner-scoped tenant management, chat completion with DB persistence + Kafka publish (includes `partner_id`), **comprehensive billing (wallets, plans, subscriptions, credit top-ups, features, credit rate limits)**, waterfall rate limiting (partner → tenant → user → TPM)
- **Stream Edge**: SSE streaming via Redis Pub/Sub with hot/cold reconnection, **secured with one-time tokens (OTT)**
- **Orchestrator**: Agent execution loop (Think → Act → Observe) with LLM calls, event streaming, **suspend/resume architecture** — exits after tool dispatch, saves state to PostgreSQL snapshots, resumes from snapshot when tools complete via Kafka signals. Distributed locking prevents duplicate processing.
- **Tool Workers**: Two tools (code executor, mock web search), results stored in Redis, **resume signals published to Kafka**
- **Archiver**: Redis stream reader → PostgreSQL batch writer for message/delta/tool_result events
- **Database**: All models complete — Tenant, User, ApiKey, Job, ChatMessage, UsageLedger, ModelPricing, Partner, PartnerApiKey, **PartnerWallet, PartnerDeposit, WalletTransaction (model only), PartnerPlan, TenantSubscription, CreditTopUp, SystemFeature, PartnerFeatureConfig, CreditUsageRecord**. 6 migrations (001-006).
- **Tests**: 138 unit tests passing (auth, billing, rate limiting, partner auth, partner rate limiting, internal token v2, **subscription, wallet, feature, credit consumption, OTT security, suspend/resume**)

### What is broken / missing
| Problem | Impact | Phase |
|---------|--------|-------|
| ~~Orchestrator **blocks** waiting for tool results~~ | ~~Cannot scale horizontally, CPU wasted~~ | ~~4~~ ✅ |
| ~~No **snapshot save** during execution~~ | ~~Crash = lost state, no resume possible~~ | ~~4~~ ✅ |
| ~~No **resume handler**~~ | ~~Jobs with tools can't survive orchestrator restart~~ | ~~4~~ ✅ |
| ~~No **distributed locking**~~ | ~~Multiple orchestrators could process same job~~ | ~~4~~ ✅ |
| ~~No **Kafka resume topic** (`agent.job-resume`)~~ | ~~Tool completion can't trigger orchestrator~~ | ~~4~~ ✅ |
| Web search tool is **mock-only** | Non-functional in production | 5 |
| No **calculator** tool | Missing basic tool | 5 |
| Archiver ignores `tool_call`, `complete`, `error`, `cancelled` events | Incomplete audit trail | 5 |
| ~~Tool workers don't publish **resume signals**~~ | ~~Suspend/resume flow incomplete~~ | ~~4+5~~ ✅ |
| No **integration tests** for suspend/resume | Can't verify core workflow | 6 |
| No **load/chaos testing** | Production readiness unknown | 6 |
| No **integration tests for billing flows** | Full wallet→plan→subscription→consumption untested | 6 |

---

## Phase 4: Orchestrator Suspend/Resume -- COMPLETE

**Goal**: Orchestrator exits after tool dispatch, frees CPU, resumes from snapshot when tools complete.

**Status**: COMPLETE. All features implemented with 24 unit tests, integration test suite created.

### 4.1 Add `agent.job-resume` Kafka Topic

**File**: `infrastructure/docker/kafka/create-topics.sh`

Add after the existing topics:
```bash
# Job resumption signals from tool workers
kafka-topics --create \
    --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
    --topic agent.job-resume \
    --partitions 6 \
    --replication-factor 1 \
    --config retention.ms=3600000 \
    --if-not-exists
```

**Also update**: `services/orchestrator/src/config.py` — add:
```python
resume_topic: str = "agent.job-resume"
resume_consumer_group: str = "orchestrator-resume"
enable_suspend_resume: bool = True  # Feature flag
```

### 4.2 Distributed State Lock

**Create**: `services/orchestrator/src/services/state_lock.py`

Prevents two orchestrator instances from processing the same job simultaneously. Uses Redis SETNX with TTL.

```python
class DistributedStateLock:
    """Redis-based distributed lock for job processing."""

    LOCK_PREFIX = "lock:job:"

    async def acquire(self, job_id: UUID, ttl: int = 300) -> bool:
        """Acquire exclusive lock. Returns True if acquired."""
        redis = await get_redis_client()
        lock_key = f"{self.LOCK_PREFIX}{job_id}"
        return bool(await redis.set(lock_key, "locked", ex=ttl, nx=True))

    async def release(self, job_id: UUID) -> None:
        """Release lock."""
        redis = await get_redis_client()
        await redis.delete(f"{self.LOCK_PREFIX}{job_id}")

    async def extend(self, job_id: UUID, ttl: int = 300) -> None:
        """Extend lock TTL during long execution."""
        redis = await get_redis_client()
        await redis.expire(f"{self.LOCK_PREFIX}{job_id}", ttl)
```

**Integration point**: Both `JobHandler.handle_job()` and `ResumeHandler.handle_resume()` must acquire the lock before processing and release it when done.

### 4.3 Refactor AgentExecutor — Suspend on Tool Dispatch

**File**: `services/orchestrator/src/engine/agent.py`

The core change: when the LLM returns tool calls, the orchestrator **saves state and exits** instead of blocking.

**Current flow** (blocking):
```
LLM response with tool_calls
  → dispatch tools to Kafka
  → poll Redis every 100ms (BLOCKING)
  → get results
  → continue loop
```

**New flow** (suspend/resume):
```
LLM response with tool_calls
  → mark state as WAITING_TOOL
  → save snapshot to PostgreSQL
  → dispatch tools to Kafka (include snapshot_sequence in message)
  → emit "suspended" event
  → RETURN (exit the function, free CPU)

... later, tool worker completes ...

ResumeHandler receives Kafka message
  → acquire distributed lock
  → load snapshot from PostgreSQL
  → fetch tool results from Redis
  → add results to state
  → continue execution loop from where it stopped
```

**Changes to `execute()` and `execute_streaming()`:**

Replace the tool execution block:
```python
# OLD (blocking):
tool_results = await self.tool_handler.execute_tools(state, response.tool_calls)
for tc, result in zip(response.tool_calls, tool_results, strict=True):
    state.add_tool_result(tc.id, result)

# NEW (suspend):
state.mark_waiting_tool(response.tool_calls)
await self.snapshot_service.save_snapshot(state)
await self._dispatch_tools_async(state, response.tool_calls)
await self._emit_event(state, "suspended", {
    "pending_tools": [tc.name for tc in response.tool_calls],
    "snapshot_sequence": state.iteration,
})
return state  # EXIT — orchestrator is done for now
```

**New method on AgentExecutor:**
```python
async def resume_from_snapshot(
    self, state: AgentState, tool_results: dict[str, str]
) -> AgentState:
    """Resume execution after tool completion."""
    # Inject tool results into message history
    for tc in state.pending_tool_calls:
        result = tool_results.get(tc.id, "Error: Tool result not found")
        state.add_tool_result(tc.id, result)

    state.pending_tool_calls = []
    state.mark_running()

    # Continue the main execution loop
    return await self.execute(state)  # or execute_streaming()
```

**Feature flag**: Check `config.enable_suspend_resume`. When False, fall back to the old blocking `_wait_for_result()` behavior so we can gradually roll out.

### 4.4 Update Tool Dispatch — Include Resume Metadata

**File**: `services/orchestrator/src/handlers/tool_handler.py`

The `_dispatch_to_worker()` method must include `snapshot_sequence` so the resume handler knows which snapshot to load:

```python
message = {
    "tool_call_id": tool_call.id,
    "job_id": str(state.job_id),
    "tenant_id": str(state.tenant_id),
    "tool_name": tool_call.name,
    "arguments": tool_call.arguments,
    "snapshot_sequence": state.iteration,  # NEW
}
```

Add a new method for non-blocking dispatch:
```python
async def dispatch_tools_async(
    self, state: AgentState, tool_calls: list[ToolCall]
) -> None:
    """Dispatch tools to Kafka without waiting for results."""
    for tc in tool_calls:
        await self._dispatch_to_worker_async(state, tc)
```

### 4.5 Tool Worker Resume Signal

**File**: `services/tool-workers/src/main.py`

After storing the tool result in Redis, the worker must publish a resume signal to Kafka:

```python
# Existing: store result in Redis
await redis.set(result_key, json.dumps(result_data), ex=300)

# NEW: publish resume signal to Kafka
producer = await get_producer()
await producer.send(
    topic="agent.job-resume",
    message={
        "job_id": message["job_id"],
        "tool_call_id": message["tool_call_id"],
        "snapshot_sequence": message.get("snapshot_sequence", 0),
        "status": "completed",
    },
    key=message["job_id"],  # Partition by job_id for ordering
)
```

**Also update**: `services/tool-workers/src/config.py` — add `resume_topic: str = "agent.job-resume"`.

### 4.6 Resume Handler (New Consumer)

**Create**: `services/orchestrator/src/handlers/resume_handler.py`

Listens on `agent.job-resume` topic. When a tool completes, this handler:
1. Acquires the distributed lock for the job
2. Loads the snapshot from PostgreSQL
3. Checks if ALL pending tools are complete (may need to wait for multiple)
4. Fetches tool results from Redis
5. Resumes execution via `AgentExecutor.resume_from_snapshot()`

```python
class ResumeHandler:
    """Handles job resumption after tool completion."""

    async def handle_resume(self, message: dict, headers: dict) -> None:
        job_id = UUID(message["job_id"])
        snapshot_seq = message["snapshot_sequence"]

        # Acquire lock
        lock = DistributedStateLock()
        if not await lock.acquire(job_id):
            logger.warning("Job already being processed", job_id=str(job_id))
            return

        try:
            # Load snapshot
            state = await self.snapshot_service.load_latest_snapshot(job_id)
            if not state or state.status != AgentStatus.WAITING_TOOL:
                return

            # Check if ALL pending tools are done
            tool_results = await self._fetch_all_tool_results(state.pending_tool_calls)
            if None in tool_results.values():
                # Not all tools complete yet — release lock and wait
                await lock.release(job_id)
                return

            # Resume execution
            executor = AgentExecutor(...)
            state = await executor.resume_from_snapshot(state, tool_results)

            # Save final state
            await self.snapshot_service.save_job(state)

        finally:
            await lock.release(job_id)
```

**Critical edge case**: If 3 tools are dispatched, the resume handler will fire 3 times (once per tool completion). Only the last one (when all results are available) should actually resume. The others should detect missing results and release the lock.

### 4.7 Register Resume Consumer in main.py

**File**: `services/orchestrator/src/main.py`

Add a second Kafka consumer alongside the existing jobs consumer:

```python
# Existing: job consumer
job_consumer = await create_consumer(
    topics=[config.jobs_topic],
    group_id=config.consumer_group,
    dlq_topic=config.jobs_dlq_topic,
)
job_consumer.register_handler(config.jobs_topic, job_handler.handle_job)

# NEW: resume consumer
if config.enable_suspend_resume:
    resume_handler = ResumeHandler(snapshot_service, ...)
    resume_consumer = await create_consumer(
        topics=[config.resume_topic],
        group_id=config.resume_consumer_group,
    )
    resume_consumer.register_handler(config.resume_topic, resume_handler.handle_resume)

# Run consumers concurrently
await asyncio.gather(
    job_consumer.run(),
    resume_consumer.run() if config.enable_suspend_resume else asyncio.sleep(float("inf")),
)
```

### 4.8 Periodic Snapshot Saves

**File**: `services/orchestrator/src/engine/agent.py`

Use the existing `config.snapshot_interval` (default 5) to save state every N iterations, not just at suspend/completion:

```python
# Inside the execution loop, after each iteration:
if state.iteration % self.config.snapshot_interval == 0:
    await self.snapshot_service.save_snapshot(state)
```

This provides crash recovery: if the orchestrator dies mid-execution, the resume handler can pick up from the last saved snapshot.

### Phase 4 Testing -- COMPLETE

**Unit tests** (`tests/unit/test_suspend_resume.py`) — 24 tests passing:
- `TestDistributedStateLock`: acquire, release, extend, is_locked, cleanup, ownership tracking
- `TestAgentState`: WAITING_TOOL status, mark_waiting_tool, pending_tool_calls field
- `TestStateSerializer`: full roundtrip, message serialization, tool_calls preservation, pending tools
- `TestResumeHandlerToolWaiting`: partial completion detection, all tools complete detection
- `TestAgentExecutorSuspend`: suspend on tool dispatch, snapshot save verification, feature flag fallback
- `TestOrchestratorConfig`: resume_topic, enable_suspend_resume flag, consumer group settings

**Integration tests** (`tests/integration/test_suspend_resume_flow.py`):
- Full cycle: submit job → orchestrator suspends → tool completes → resume signal → orchestrator resumes → job completes
- Distributed locking prevents duplicate processing
- Snapshot persistence and recovery
- Multiple tools waiting logic

### Phase 4 Files Summary -- COMPLETE

| File | Action | Status |
|------|--------|--------|
| `infrastructure/docker/kafka/create-topics.sh` | Modify | ✅ Added `agent.job-resume` topic |
| `services/orchestrator/src/config.py` | Modify | ✅ Added `resume_topic`, `enable_suspend_resume`, `job_lock_ttl_seconds` |
| `services/orchestrator/src/services/state_lock.py` | **Create** | ✅ `DistributedStateLock` (Redis SETNX with TTL) |
| `services/orchestrator/src/engine/agent.py` | Modify | ✅ Suspend on tool calls, `resume_from_snapshot()`, periodic snapshots |
| `services/orchestrator/src/engine/serializer.py` | **Create** | ✅ `StateSerializer` for JSON serialization of AgentState |
| `services/orchestrator/src/handlers/tool_handler.py` | Modify | ✅ `dispatch_tools_async()`, `snapshot_sequence` in messages |
| `services/orchestrator/src/handlers/resume_handler.py` | **Create** | ✅ `ResumeHandler` — load snapshot, fetch results, resume |
| `services/orchestrator/src/handlers/job_handler.py` | Modify | ✅ Lazy import for `AgentExecutor` to break circular dependency |
| `services/orchestrator/src/main.py` | Modify | ✅ Dual Kafka consumers with `asyncio.gather` |
| `services/tool-workers/src/main.py` | Modify | ✅ Publish resume signal to Kafka after tool completion |
| `services/tool-workers/src/config.py` | Modify | ✅ Added `resume_topic` |
| `tests/unit/test_suspend_resume.py` | **Create** | ✅ 24 unit tests passing |
| `tests/integration/test_suspend_resume_flow.py` | **Create** | ✅ Integration test suite created |

---

## Phase 5: Tool Workers & Archiver Completion

**Goal**: Production-ready tools, complete event archival.

Can be parallelized with Phase 4 (independent services).

### 5.1 Web Search Tool — Real API Integration

**File**: `services/tool-workers/src/tools/web_search.py`

Current state: returns mock/placeholder results.

Replace with real API integration (Brave Search or DuckDuckGo):
```python
async def execute(self, arguments: dict, context: dict) -> str:
    query = arguments["query"]
    max_results = arguments.get("max_results", 5)

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": self.config.brave_api_key},
            params={"q": query, "count": max_results},
        )
        response.raise_for_status()
        data = response.json()

    results = data.get("web", {}).get("results", [])
    formatted = f"## Search results for: {query}\n\n"
    for i, r in enumerate(results):
        formatted += f"{i+1}. **{r['title']}**\n   {r.get('description', '')}\n   URL: {r['url']}\n\n"

    return formatted or "No results found."
```

**Config**: Add `brave_api_key: str = ""` to tool workers config. When empty, fall back to DuckDuckGo instant answers (no API key required).

### 5.2 Calculator Tool

**Create**: `services/tool-workers/src/tools/calculator.py`

Safe mathematical expression evaluator using AST parsing (no `eval()`):
```python
class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Evaluate mathematical expressions safely"
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Mathematical expression (e.g., '2 + 3 * 4')"
            }
        },
        "required": ["expression"]
    }

    async def execute(self, arguments: dict, context: dict) -> str:
        import ast, operator
        # Walk the AST and only allow safe operations: +, -, *, /, **, %
        # Reject function calls, attribute access, imports
        ...
```

### 5.3 Update Tool Registry

**File**: `services/tool-workers/src/registry.py`

Add calculator to `register_all()`:
```python
from .tools.calculator import CalculatorTool
self.register(CalculatorTool())
```

### 5.4 Complete Archiver Event-Type Mapping

**File**: `services/archiver/src/services/postgres_writer.py`

Currently handles: `message`, `delta`, `tool_result`.

Add handlers for:

| Event Type | DB Action |
|------------|-----------|
| `start` | Update `Job.status = RUNNING`, set `started_at` |
| `tool_call` | Insert `ChatMessage` with `role=ASSISTANT`, `tool_calls` JSON |
| `complete` | Update `Job.status = COMPLETED`, set `completed_at`, token counts |
| `error` | Update `Job.status = FAILED`, set `error` field |
| `cancelled` | Update `Job.status = CANCELLED` |
| `suspended` | Update `Job.status = WAITING_TOOL` (Phase 4 event) |

Implementation pattern:
```python
async def _handle_complete_event(self, event: dict, session) -> None:
    """Update job status on completion."""
    job_id = UUID(event["job_id"])
    data = event.get("data", {})

    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job:
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now(UTC)
        job.total_input_tokens = data.get("total_input_tokens", 0)
        job.total_output_tokens = data.get("total_output_tokens", 0)
```

### 5.5 Archiver Stream Cleanup

**File**: `services/archiver/src/main.py`

Add periodic cleanup of old Redis streams (call `RedisStreamReader.cleanup_old_streams()` on a schedule):

```python
# Run cleanup every 5 minutes
async def periodic_cleanup(reader, interval=300):
    while True:
        await asyncio.sleep(interval)
        await reader.cleanup_old_streams()

# In main():
await asyncio.gather(
    process_streams(reader, writer),
    periodic_cleanup(reader),
)
```

### Phase 5 Testing

**Unit tests**:
- `tests/unit/test_calculator.py` — safe eval, operator support, injection rejection
- `tests/unit/test_web_search.py` — API response parsing, error handling, rate limiting
- `tests/unit/test_archiver_events.py` — all event types mapped correctly

### Phase 6 Files Summary

| File | Action | Key Change |
|------|--------|------------|
| `services/tool-workers/src/tools/web_search.py` | Modify | Replace mock with real Brave/DDG API |
| `services/tool-workers/src/tools/calculator.py` | **Create** | Safe math expression evaluator |
| `services/tool-workers/src/registry.py` | Modify | Register calculator tool |
| `services/tool-workers/src/config.py` | Modify | Add `brave_api_key` |
| `services/archiver/src/services/postgres_writer.py` | Modify | Handle all event types |
| `services/archiver/src/main.py` | Modify | Add periodic stream cleanup |
| `tests/unit/test_calculator.py` | **Create** | Calculator tool tests |
| `tests/unit/test_web_search.py` | **Create** | Web search integration tests |
| `tests/unit/test_archiver_events.py` | **Create** | Archiver event mapping tests |

---

## Phase 6: Testing & Production Readiness

**Goal**: Comprehensive test suite, observability, documentation.

### 6.1 Integration Test Suite

**File**: `tests/integration/test_job_lifecycle.py`

Full job lifecycle with infrastructure running:
1. Create tenant → API key → user → JWT
2. Submit chat completion via API
3. Verify Job record in DB (status = PENDING → RUNNING → COMPLETED)
4. Verify ChatMessage records (initial user message + assistant response)
5. Verify events received via SSE stream
6. Verify token counts updated

**File**: `tests/integration/test_suspend_resume_flow.py` (from Phase 4)

Suspend/resume with real Kafka and Redis:
1. Submit job that triggers tool calls
2. Verify orchestrator suspends (Job status = WAITING_TOOL)
3. Verify snapshot exists in DB
4. Verify tool dispatched to Kafka
5. Tool worker processes → result in Redis → resume signal to Kafka
6. Verify orchestrator resumes and job completes

**File**: `tests/integration/test_streaming.py`

SSE reconnection:
1. Connect to SSE stream for a job
2. Receive events
3. Disconnect and reconnect (hot/cold fetch)
4. Verify no missed events

### 6.2 Chaos Testing

**File**: `tests/e2e/test_chaos.py`

Requires all infrastructure running. Tests resilience:

| Scenario | Expected Behavior |
|----------|-------------------|
| Kill orchestrator during LLM call | Job stays RUNNING, no crash propagation |
| Kill orchestrator during tool wait | Snapshot saved, resume on restart |
| Kill tool worker during execution | Tool times out, orchestrator handles error |
| Redis restart during rate limiting | Graceful degradation, counts reset |
| Kafka broker restart | Consumers reconnect, no message loss |

### 6.3 Load Testing

**File**: `tests/e2e/test_load.py`

Using `httpx` or `locust`:
- 100 concurrent chat completions
- Measure: P50/P95/P99 latency, error rate
- Verify: rate limiting activates correctly under load
- Verify: no race conditions in billing reservations
- Target: < 500ms P95 for job creation (DB + Kafka publish)

### 6.4 Observability — Structured Metrics

**Create**: `libs/common/metrics.py`

Emit structured log events that can be scraped by Prometheus/Datadog/etc:

```python
class MetricsCollector:
    async def record_job_duration(self, job_id, duration_ms, status): ...
    async def record_tool_execution(self, tool_name, duration_ms, success): ...
    async def record_token_usage(self, tenant_id, model_id, input_tokens, output_tokens, cost): ...
    async def record_rate_limit_hit(self, tenant_id, user_id, limit_scope): ...
    async def record_billing_rejection(self, tenant_id, reason): ...
```

**Integration points**:
- Orchestrator: job duration, token usage, tool execution time
- API Gateway: rate limit hits, billing rejections, request latency
- Tool Workers: tool execution time, success/failure rate
- Archiver: batch size, flush latency, event processing rate

### 6.5 Health Check Enhancements

**Files**: Each service's `/health` endpoint

Add dependency health checks:
```json
{
  "status": "healthy",
  "service": "orchestrator",
  "dependencies": {
    "postgres": {"status": "healthy", "latency_ms": 2},
    "redis": {"status": "healthy", "latency_ms": 1},
    "kafka": {"status": "healthy", "lag": 0}
  }
}
```

### 6.6 Documentation

| Document | Content |
|----------|---------|
| `docs/api_reference.md` | OpenAPI spec, auth requirements, request/response examples, error codes |
| `docs/deployment.md` | Production setup, env vars, scaling guidelines, infrastructure sizing |
| `docs/admin_guide.md` | Tenant management, API key rotation, billing, monitoring |
| `docs/developer_guide.md` | Adding tools, adding LLM providers, local dev setup, testing |

### Phase 6 Files Summary

| File | Action | Key Change |
|------|--------|------------|
| `libs/common/metrics.py` | **Create** | Structured metrics collector |
| `tests/integration/test_job_lifecycle.py` | **Create** | Full job lifecycle tests |
| `tests/integration/test_streaming.py` | **Create** | SSE reconnection tests |
| `tests/e2e/test_chaos.py` | **Create** | Chaos/resilience tests |
| `tests/e2e/test_load.py` | **Create** | Load/performance tests |
| `docs/api_reference.md` | **Create** | API documentation |
| `docs/deployment.md` | **Create** | Deployment guide |
| `docs/admin_guide.md` | **Create** | Admin operations guide |
| `docs/developer_guide.md` | **Create** | Developer onboarding guide |

---

## Implementation Sequencing

```
Phases 1+2+2.5+3+3.5 (COMPLETE)
├── Auth (3-tier → 4-tier with Partners)
├── Basic Billing (tenant + partner pools)
├── Comprehensive Billing (wallets, plans, subscriptions, features)
├── Rate Limiting (4-tier waterfall + credit rate limits)
├── DB Persistence + Internal Token v2
├── Stream-Edge OTT Security
└── 114 unit tests passing
         │
         ▼
Phase 4 (Suspend/Resume)            Phase 5 (Tools + Archiver)
         │                                    │
   4.1 Kafka topic                   5.1 Web search API
   4.2 State lock                    5.2 Calculator tool
   4.3 Executor refactor             5.3 Registry update
   4.4 Tool dispatch metadata        5.4 Archiver event mapping
   4.5 Worker resume signal ←────────5.5 Archiver cleanup
   4.6 Resume handler                     │
   4.7 Dual consumer                      │
   4.8 Periodic snapshots                 │
         │                                │
         └────────────┬───────────────────┘
                      │
              Phase 6 (Testing + Prod)
              6.1 Integration tests
              6.2 Chaos tests
              6.3 Load tests
              6.4 Metrics
              6.5 Health checks
              6.6 Documentation
```

**Dependency**: Phase 4.5 (worker resume signal) connects to Phase 5 (tool workers). These can be developed in parallel — the tool worker resume signal (4.5) is a small change to `main.py` that can be done alongside Phase 5 tool improvements.

**Critical path**: Phase 4 → Phase 6. The suspend/resume refactor is the biggest remaining architectural change and must be thoroughly tested.

**Note on partner_id propagation**: The Kafka payloads already include `partner_id` (added in Phase 2.5). Phase 4 resume handlers should read and preserve `partner_id` from the snapshot/payload when resuming jobs.

---

## Completion Criteria

### Phase 3 is DONE when: ✅ ALL MET
- [x] Partner wallet with deposit/debit operations
- [x] Subscription plans with monthly credits and rate limits
- [x] Tenant subscription lifecycle (create/change/cancel)
- [x] Credit top-ups with FIFO consumption and expiration
- [x] Credit rate limiting (per-plan hourly/daily)
- [x] System features with default model routing
- [x] Partner feature configuration overrides
- [x] Background job scheduler (APScheduler)
- [x] Migration 005 with all billing tables
- [x] 106 unit tests passing

### Phase 3.5 is DONE when: ✅ OTT COMPLETE, ⚠️ LEDGER PARTIAL
- [x] OTT JWT tokens created in chat completion response
- [x] Stream-Edge validates OTT with Redis SETNX one-time check
- [x] Browser reconnection handled via Last-Event-ID exemption
- [x] Old `/events/{job_id}` endpoint deprecated
- [x] Frontend TypeScript interface updated with `stream_token`
- [x] 8 OTT unit tests passing
- [x] WalletTransaction model + Migration 006 created
- [ ] ⚠️ Wallet service does NOT create transaction records (intentionally skipped)
- [ ] ⚠️ Transaction list API endpoint NOT implemented (intentionally skipped)

### Phase 4 is DONE when: ✅ ALL MET
- [x] `agent.job-resume` Kafka topic created and configured
- [x] Distributed lock prevents duplicate job processing (Redis SETNX)
- [x] Orchestrator returns immediately after dispatching tools (no polling)
- [x] Snapshot saved to PostgreSQL before orchestrator exits
- [x] Tool workers publish resume signals to Kafka after completion
- [x] Resume handler loads snapshot, fetches results, continues execution
- [x] Dual Kafka consumers run concurrently in orchestrator
- [x] Feature flag `enable_suspend_resume` allows fallback to polling
- [x] Unit tests for lock, suspend, resume all pass (24 tests)
- [x] Integration test: full suspend/resume cycle test suite created

### Phase 5 is DONE when:
- [ ] Web search tool calls real API (Brave or DuckDuckGo)
- [ ] Calculator tool safely evaluates expressions (no code injection)
- [ ] All event types (`start`, `tool_call`, `complete`, `error`, `cancelled`, `suspended`) handled by archiver
- [ ] Redis stream cleanup runs periodically
- [ ] Unit tests for all tools and archiver event mapping pass

### Phase 6 is DONE when:
- [ ] All unit tests pass (target: 150+ tests, currently 138 passing)
- [ ] Integration tests cover: job lifecycle, suspend/resume, streaming, billing flows
- [ ] Chaos tests verify recovery from service kills
- [ ] Load test: 100 concurrent jobs with < 500ms P95 creation latency
- [ ] MetricsCollector integrated into all services
- [ ] Health checks include dependency status
- [ ] API reference documentation complete
- [ ] Deployment guide complete

### Full System is PRODUCTION-READY when:
- [ ] All Phase 1-6 criteria met (Phase 1, 2, 2.5, 3, 3.5, 4 already complete)
- [ ] `make check` passes (lint + typecheck + tests)
- [ ] No pre-existing mypy errors in `libs/` (14 current — should be resolved)
- [ ] All environment variables documented in `.env.example`
- [ ] Master admin key and JWT secrets rotated from defaults
- [ ] CORS configured (not wildcard `*`)
- [ ] Database migrations up to date (currently at 005_billing_plans)

---

## Verification Commands

```bash
# Phase 3 (COMPLETE)
pytest tests/unit/test_subscription_service.py tests/unit/test_wallet_service.py tests/unit/test_feature_service.py tests/unit/test_credit_consumption.py -v

# Phase 3.5 (OTT COMPLETE)
pytest tests/unit/test_stream_ott.py -v

# Phase 4
pytest tests/unit/test_suspend_resume.py -v
pytest tests/integration/test_suspend_resume_flow.py -v

# Phase 5
pytest tests/unit/test_calculator.py tests/unit/test_web_search.py tests/unit/test_archiver_events.py -v

# Phase 6
pytest tests/ -v --cov=libs --cov=services --cov-report=term-missing
pytest tests/e2e/ -v  # Requires all infrastructure

# Full verification
make check  # lint + typecheck + test
```
