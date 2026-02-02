# Agentic System - Complete Implementation Plan

## Executive Summary

The Agentic System is currently **~60% complete**. Phases 1 and 2 are fully implemented, tested, and verified. The remaining work centers on the orchestrator suspend/resume refactor (Phase 3), tool worker enhancements (Phase 4), and production testing (Phase 5).

See [docs/next-phases.md](next-phases.md) for the detailed technical roadmap for Phases 3-5.

### Current State (as of Phase 2 completion)
- ✅ **Stream Edge (95%)**: Fully functional SSE with hot/cold reconnection
- ✅ **API Gateway (95%)**: Three-tier auth, admin endpoints, billing, DB job persistence, waterfall rate limiting — all complete
- ⚠️ **Orchestrator (30%)**: Basic loop works but blocks on tool calls — no suspend/resume, no distributed locking
- ⚠️ **Tool Workers (25%)**: Two tools (code executor + mock web search), no resume signal, no Kafka result publication
- ⚠️ **Archiver (40%)**: Reads Redis streams and batches to PostgreSQL, but incomplete event-type mapping
- ✅ **Database Models (100%)**: All SQLAlchemy models complete
- ✅ **Shared Libraries (95%)**: Auth, billing, internal tokens, config, logging, LLM abstraction, Kafka, Redis

### Resolved Features (Phase 1 + 2)
1. ~~Three-tier authentication~~ → **DONE** (Platform Owner / Tenant API Key / End User JWT)
2. ~~Admin endpoints~~ → **DONE** (CRUD tenants, API keys, users, token exchange)
3. ~~Job creation DB transaction~~ → **DONE** (Job + ChatMessages persisted before Kafka publish)
4. ~~Billing pre-checks~~ → **DONE** (feature-flagged, microdollar credit system with atomic Redis reservations)
5. ~~Internal transaction tokens~~ → **DONE** (signed JWT with `internal_jwt_secret`, 10-min TTL)
6. ~~Waterfall rate limiting~~ → **DONE** (tenant RPM → user RPM with custom/inherited limits → TPM)
7. ~~API key caching layer~~ → **DONE** (Redis-backed LRU with 5-min TTL)

### Remaining Critical Features
1. **True suspend/resume** — orchestrator must exit after tool dispatch, resume from snapshot on completion
2. **Kafka-based tool result consumption** — replace Redis polling with resume signals
3. **Distributed state locking** — prevent duplicate processing across orchestrator instances
4. **Production tool implementations** — web search needs real API, add calculator tool
5. **Complete archiver event-type mapping** — `tool_call`, `complete`, `error`, `cancelled` events
6. **Incremental message persistence** — persist assistant messages during execution, not just at completion
7. **Comprehensive test suite** — integration tests for suspend/resume, chaos testing, load testing

---

## Phase 1: Authentication & Admin Foundation -- COMPLETE

**Goal**: Establish three-tier auth system and enable platform administration

**Status**: COMPLETE. All endpoints implemented, 8 unit tests passing, 12 integration tests written.

### 1.1 Master Admin Key System

**Files to modify:**
- [libs/common/config.py](libs/common/config.py)
- [services/api-gateway/src/middleware/auth.py](services/api-gateway/src/middleware/auth.py)

**Tasks:**
- Add `master_admin_key: str` to Settings class (env var: `MASTER_ADMIN_KEY`)
- Add `internal_jwt_secret: str` to Settings (separate from user JWT secret)
- Modify `AuthMiddleware` to recognize master admin key
- Set `request.state.is_platform_owner = True` for admin requests

### 1.2 Admin Endpoints (Tenant Management)

**File to create:**
- [services/api-gateway/src/routers/admin.py](services/api-gateway/src/routers/admin.py)

**Endpoints:**
```python
POST   /admin/tenants                    # Create tenant (master admin only)
GET    /admin/tenants                    # List all tenants
GET    /admin/tenants/{tenant_id}        # Get tenant details
PUT    /admin/tenants/{tenant_id}        # Update tenant (status, plan, limits)
POST   /admin/tenants/{tenant_id}/api-keys  # Generate API key
DELETE /admin/api-keys/{key_id}          # Revoke API key
```

**Implementation pattern:**
```python
async with get_session_context() as session:
    # 1. Create tenant
    tenant = Tenant(name=..., slug=..., status=TenantStatus.ACTIVE)
    session.add(tenant)
    await session.flush()  # Get tenant.id

    # 2. Generate API key (sk_live_...)
    raw_key, key_hash = generate_api_key()  # Returns raw key + SHA-256 hash
    api_key = ApiKey(tenant_id=tenant.id, key_hash=key_hash, key_prefix="sk_live")
    session.add(api_key)

    await session.commit()
    return {"tenant_id": tenant.id, "api_key": raw_key}  # Show raw key ONCE
```

### 1.3 User Management Endpoints

**File to create:**
- [services/api-gateway/src/routers/users.py](services/api-gateway/src/routers/users.py)

**Endpoints:**
```python
POST   /v1/users           # Create virtual user (tenant API key required)
GET    /v1/users           # List users in tenant
GET    /v1/users/{user_id} # Get user details
PUT    /v1/users/{user_id} # Update user limits (custom_daily_limit, custom_monthly_limit)
DELETE /v1/users/{user_id} # Deactivate user
```

**Features:**
- Upsert logic for virtual users (create if not exists)
- Support custom rate limit overrides (NULL = inherit from tenant)
- Validate tenant_id matches authenticated tenant

### 1.4 Token Exchange Endpoint

**File to create:**
- [services/api-gateway/src/routers/auth.py](services/api-gateway/src/routers/auth.py)

**Endpoint:**
```python
POST /v1/auth/token
# Request:  {"user_id": "uuid-or-external-id"}
# Header:   Authorization: Bearer sk_live_...
# Response: {"access_token": "jwt...", "expires_in": 3600, "token_type": "Bearer"}
```

**Logic:**
1. Validate tenant API key from header
2. Verify `user_id` belongs to authenticated tenant
3. Mint short-lived JWT (1 hour expiry)
4. JWT payload: `{sub: user_id, tenant_id, role, scopes: ["job:create", "stream:read"], exp}`

**Purpose**: Allows frontend apps to authenticate end users without exposing master API key

### 1.5 API Key Caching Layer

**File to create:**
- [services/api-gateway/src/services/api_key_cache.py](services/api-gateway/src/services/api_key_cache.py)

**Implementation:**
```python
class ApiKeyCache:
    """LRU cache for API key lookups (TTL: 5 minutes)"""

    async def get(self, key_hash: str) -> tuple[ApiKey, Tenant] | None:
        redis = await get_redis_client()
        cache_key = f"api_key_cache:{key_hash}"

        # Try cache first
        cached = await redis.get(cache_key)
        if cached:
            return deserialize(cached)

        # Cache miss - query DB
        async with get_session_context() as session:
            result = await session.execute(
                select(ApiKey, Tenant)
                .join(Tenant)
                .where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
            )
            row = result.first()

        if row:
            await redis.set(cache_key, serialize(row), ex=300)  # 5 min TTL
        return row
```

**Modify:** [services/api-gateway/src/middleware/auth.py](services/api-gateway/src/middleware/auth.py) to use cache

### Testing Phase 1
- Unit: Admin router (tenant creation, API key generation, hashing)
- Integration: Master admin → create tenant → tenant uses API key → create user → exchange token
- Security: Attempt to access admin endpoints without master key (should 401)
- Cache: Verify cache hit/miss behavior, TTL expiration

---

## Phase 2: Billing & Enhanced Rate Limiting -- COMPLETE

**Goal**: Implement credit-based billing and waterfall rate limiting

**Status**: COMPLETE. All features implemented with 29 new unit tests (37 total), integration tests written.

**Key decisions made during implementation:**
- Credits stored as **integer microdollars** (1,000,000 = $1.00) — avoids floating-point issues, Redis DECRBY works natively with integers
- Billing is **feature-flagged** via `ENABLE_BILLING_CHECKS` (default: false)
- Internal transaction tokens use `internal_jwt_secret` (separate from user `jwt_secret`)
- DB write happens **before** Kafka publish — job exists in DB even if messaging fails

### 2.1 Internal Transaction Token System

**File to modify:**
- [libs/common/auth.py](libs/common/auth.py)

**New functions:**
```python
def create_internal_transaction_token(
    job_id: UUID,
    tenant_id: UUID,
    credit_check_passed: bool,
    max_tokens: int
) -> str:
    """Create internal JWT for Kafka payload authentication (10 min TTL)"""
    payload = {
        "ver": 1,
        "trace_id": str(uuid4()),  # For distributed tracing
        "job_id": str(job_id),
        "tenant_id": str(tenant_id),
        "credit_check_passed": credit_check_passed,
        "limits": {"max_tokens": max_tokens},
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())
    }
    return jwt.encode(payload, get_settings().internal_jwt_secret, algorithm="HS256")

def verify_internal_transaction_token(token: str) -> dict:
    """Verify and decode internal transaction token"""
    try:
        return jwt.decode(token, get_settings().internal_jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Internal transaction token expired")
    except jwt.InvalidTokenError:
        raise AuthenticationError("Invalid internal transaction token")
```

**Purpose**: Workers can verify job legitimacy without access to HTTP context

### 2.2 Billing Pre-Check Service

**File to create:**
- [services/api-gateway/src/services/billing.py](services/api-gateway/src/services/billing.py)

**Implementation:**
```python
class BillingService:
    """Manages credit checks and reservations"""

    async def check_credit_balance(self, tenant_id: UUID, estimated_tokens: int) -> bool:
        """Check if tenant has sufficient credits for estimated usage"""
        redis = await get_redis_client()
        balance_key = f"tenant:{tenant_id}:balance"

        # Try Redis cache first
        balance = await redis.get(balance_key)
        if balance is None:
            # Fetch from DB and cache
            balance = await self._fetch_balance_from_db(tenant_id)
            await redis.set(balance_key, str(balance), ex=60)

        # Estimate cost based on pricing
        pricing = await self._get_model_pricing(provider, model_id)
        estimated_cost = (estimated_tokens / 1000) * pricing.input_price_per_1k

        return float(balance) >= estimated_cost

    async def reserve_credits(self, tenant_id: UUID, estimated_cost: float) -> str:
        """Reserve credits for a job (atomic Redis DECRBY)"""
        redis = await get_redis_client()
        balance_key = f"tenant:{tenant_id}:balance"

        # Atomic decrement
        new_balance = await redis.decrby(balance_key, int(estimated_cost * 1000000))

        # Store reservation for audit
        reservation_id = str(uuid4())
        await redis.hset(
            f"reservation:{reservation_id}",
            mapping={"tenant_id": str(tenant_id), "amount": estimated_cost, "timestamp": time.time()}
        )
        await redis.expire(f"reservation:{reservation_id}", 3600)

        return reservation_id
```

### 2.3 Waterfall Rate Limiting

**File to modify:**
- [services/api-gateway/src/middleware/rate_limit.py](services/api-gateway/src/middleware/rate_limit.py)

**Enhanced logic:**
```python
async def _check_rate_limit_waterfall(
    self,
    tenant_id: UUID,
    user_id: UUID | None,
) -> None:
    """Waterfall: Tenant limit → User limit (inherit if not set)"""

    # Step 1: Check tenant-level RPM/TPM
    tenant_limits = await self._get_tenant_limits(tenant_id)
    tenant_usage = await self._get_tenant_usage_rpm(tenant_id)

    if tenant_usage >= tenant_limits.rpm:
        raise RateLimitError(
            "Tenant rate limit exceeded",
            retry_after=self._calculate_retry_after(tenant_id)
        )

    # Step 2: Check user-specific limits (if user_id provided)
    if user_id:
        # Check for custom user limit override
        user_limit = await self._get_user_custom_limit(user_id)

        if user_limit is None:
            # Inherit from tenant default
            user_limit = tenant_limits.default_user_rpm

        user_usage = await self._get_user_usage_rpm(user_id)
        if user_usage >= user_limit:
            raise RateLimitError("User rate limit exceeded")
```

**Redis keys pattern:**
```
tenant:{tenant_id}:rpm:{window}       # Sorted set for tenant RPM
user:{user_id}:rpm:{window}           # Sorted set for user RPM
tenant:{tenant_id}:balance            # Float (credits)
```

### 2.4 Job Creation with DB Transaction

**File to modify:**
- [services/api-gateway/src/routers/chat.py](services/api-gateway/src/routers/chat.py)

**Critical changes to `POST /v1/chat/completions`:**

```python
@router.post("/chat/completions")
async def create_chat_completion(body: ChatCompletionRequest, request: Request):
    tenant_id = request.state.tenant_id
    user_id = request.state.user_id
    job_id = uuid4()

    # Estimate tokens for billing
    estimated_tokens = estimate_tokens_from_messages(body.messages)

    # 1. Billing pre-check
    billing_service = BillingService()
    if not await billing_service.check_credit_balance(tenant_id, estimated_tokens):
        raise BillingError("Insufficient credits", status_code=402)

    # 2. Reserve credits
    reservation_id = await billing_service.reserve_credits(tenant_id, estimated_cost)

    # 3. DATABASE TRANSACTION - Create Job + ChatMessages
    async with get_session_context() as session:
        # Create Job record (status: PENDING)
        job = Job(
            id=job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            status=JobStatus.PENDING,
            provider=body.provider or "anthropic",
            model_id=body.model,
            system_prompt=body.system,
            tools_config=body.tools,
            metadata_=body.metadata or {},
        )
        session.add(job)

        # Create initial user messages
        for idx, msg in enumerate(body.messages):
            chat_msg = ChatMessage(
                job_id=job_id,
                sequence_num=idx,
                role=MessageRole(msg.role),
                content=msg.content,
            )
            session.add(chat_msg)

        await session.commit()

    # 4. Generate internal transaction token
    internal_token = create_internal_transaction_token(
        job_id=job_id,
        tenant_id=tenant_id,
        credit_check_passed=True,
        max_tokens=body.max_tokens or 4096,
    )

    # 5. Publish to Kafka with internal token in headers
    producer = await get_producer()
    await producer.send(
        topic=config.jobs_topic,
        message={
            "job_id": str(job_id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id) if user_id else None,
            "model": body.model,
            "messages": [msg.dict() for msg in body.messages],
            "tools": body.tools,
        },
        headers={
            "job_id": str(job_id),
            "tenant_id": str(tenant_id),
            "internal_token": internal_token,  # NEW: Security token
        },
        key=str(tenant_id),
    )

    # 6. Return job ID and stream URL
    return {
        "job_id": str(job_id),
        "status": "pending",
        "stream_url": f"{config.stream_edge_url}/api/v1/events/{job_id}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
```

**Key change**: Job is now persisted to database BEFORE Kafka publish, ensuring audit trail even if messaging fails.

### Testing Phase 2
- Unit: Billing service (credit checks, reservations)
- Unit: Rate limiting (waterfall inheritance, tenant vs user limits)
- Integration: Job creation flow (DB insert + Kafka publish)
- Integration: Insufficient credits rejection (should return 402)
- Security: Internal token generation and verification

---

## Phase 3: Orchestrator Suspend/Resume Refactor (Week 3-4)

**Goal**: Implement true suspend/resume where orchestrator exits after tool dispatch

### 3.1 Kafka Topic Configuration

**File to modify:**
- [infrastructure/docker/kafka/create-topics.sh](infrastructure/docker/kafka/create-topics.sh)

**Add new topic:**
```bash
# Topic for job resumption signals from tool workers
kafka-topics --create \
    --topic agent.job-resume \
    --partitions 6 \
    --replication-factor 1 \
    --config retention.ms=3600000  # 1 hour retention
```

**Topic purpose**: Tool workers publish here when tool execution completes, triggering job resumption.

### 3.2 Orchestrator Suspend Logic

**File to modify:**
- [services/orchestrator/src/engine/agent.py](services/orchestrator/src/engine/agent.py)

**Critical change to execution loop:**

```python
async def execute_streaming(self, state: AgentState) -> AgentState:
    """Execute agent loop with streaming. EXITS on tool dispatch (suspend)."""

    while state.iteration < self.config.max_iterations:
        # Think: LLM completion
        response = await self.llm_service.complete(state)

        # Emit thinking events
        await self._emit_event("message", {"role": "assistant", "content": response.content})

        # Check for tool calls
        if response.tool_calls:
            # SUSPEND POINT - Critical change from old behavior
            logger.info(
                "Tool calls detected - suspending job",
                job_id=str(state.job_id),
                tool_count=len(response.tool_calls)
            )

            # Mark state as waiting for tools
            state.mark_waiting_tool(response.tool_calls)

            # Save snapshot to PostgreSQL
            await self.snapshot_service.save_snapshot(state)

            # Dispatch tools to Kafka (async, no waiting)
            for tool_call in response.tool_calls:
                await self._dispatch_tool_to_kafka(state, tool_call)

            # Emit tool dispatch events
            await self._emit_event("tool_calls", {"calls": [tc.dict() for tc in response.tool_calls]})

            # CRITICAL: EXIT instead of waiting for results
            # Another orchestrator will resume from snapshot when tool completes
            logger.info("Job suspended - exiting orchestrator", job_id=str(state.job_id))
            return state  # Orchestrator exits here, frees CPU

        # No tools - add response to state and continue
        state.add_message("assistant", response.content)

        # Check if complete
        if response.stop_reason == "end_turn":
            state.mark_completed()
            break

        state.iteration += 1

    return state
```

**Old behavior**: Orchestrator blocked waiting for tool results (CPU waste)
**New behavior**: Orchestrator saves state, dispatches tools, exits immediately

### 3.3 Tool Dispatch with Resume Metadata

**File to modify:**
- [services/orchestrator/src/handlers/tool_handler.py](services/orchestrator/src/handlers/tool_handler.py)

**Updated dispatch:**
```python
async def _dispatch_tool_to_kafka(
    self,
    state: AgentState,
    tool_call: ToolCall
) -> None:
    """Dispatch tool to Kafka. Does NOT wait for results."""

    producer = await get_producer()

    message = {
        "tool_call_id": tool_call.id,
        "job_id": str(state.job_id),
        "tenant_id": str(state.tenant_id),
        "tool_name": tool_call.name,
        "arguments": tool_call.arguments,
        "snapshot_sequence": state.iteration,  # For resumption
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await producer.send(
        topic=self.config.tools_topic,  # "agent.tools"
        message=message,
        key=str(state.tenant_id),  # Partition by tenant
    )

    logger.info(
        "Tool dispatched to Kafka",
        tool_name=tool_call.name,
        tool_call_id=tool_call.id,
        job_id=str(state.job_id)
    )

    # DO NOT POLL REDIS HERE - just return immediately
```

### 3.4 Tool Worker Resume Signal

**File to modify:**
- [services/tool-workers/src/main.py](services/tool-workers/src/main.py)

**Updated tool completion:**
```python
async def handle_tool_request(message: dict, headers: dict) -> None:
    """Execute tool and publish resume signal"""

    tool_name = message["tool_name"]
    tool_call_id = message["tool_call_id"]
    job_id = message["job_id"]

    # Execute tool
    tool = registry.get(tool_name)
    result = await tool.execute(message["arguments"], context={
        "job_id": job_id,
        "tenant_id": message["tenant_id"],
    })

    # Store result in Redis (orchestrator will fetch during resume)
    redis = await get_redis_client()
    result_key = f"tool_result:{tool_call_id}"
    await redis.set(result_key, json.dumps(result), ex=300)  # 5 min TTL

    # CRITICAL: Publish resume signal to Kafka (not just Redis)
    producer = await get_producer()
    await producer.send(
        topic="agent.job-resume",
        message={
            "job_id": job_id,
            "tool_call_id": tool_call_id,
            "snapshot_sequence": message["snapshot_sequence"],
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        key=job_id,  # Partition by job_id for ordered delivery
    )

    logger.info(
        "Tool completed - resume signal sent",
        tool_name=tool_name,
        job_id=job_id
    )
```

### 3.5 Job Resume Handler (New Consumer)

**File to create:**
- [services/orchestrator/src/handlers/resume_handler.py](services/orchestrator/src/handlers/resume_handler.py)

```python
class JobResumeHandler:
    """Handles job resumption signals from tool workers"""

    def __init__(self, snapshot_service, llm_service, config):
        self.snapshot_service = snapshot_service
        self.llm_service = llm_service
        self.config = config

    async def handle_resume(self, message: dict, headers: dict) -> None:
        """Resume job from snapshot after tool completion"""
        job_id = UUID(message["job_id"])
        snapshot_seq = message["snapshot_sequence"]

        logger.info(
            "Resuming job from snapshot",
            job_id=str(job_id),
            snapshot_sequence=snapshot_seq
        )

        # Load snapshot from PostgreSQL
        state = await self.snapshot_service.load_snapshot(job_id, snapshot_seq)

        if not state:
            logger.error("Cannot resume - snapshot not found", job_id=str(job_id))
            return

        # Fetch tool results from Redis
        tool_results = await self._fetch_tool_results(state.pending_tool_calls)

        # Add tool results to state
        for tool_call, result in zip(state.pending_tool_calls, tool_results):
            state.add_tool_result(tool_call.id, result)

        state.pending_tool_calls = []  # Clear pending

        # Continue execution from where we left off
        executor = AgentExecutor(
            llm_service=self.llm_service,
            snapshot_service=self.snapshot_service,
            config=self.config,
        )

        state = await executor.execute_streaming(state)

        # Save final state
        await self.snapshot_service.update_job(state)

        logger.info("Job resumed and completed", job_id=str(job_id), status=state.status)

    async def _fetch_tool_results(self, tool_calls: list[ToolCall]) -> list[str]:
        """Fetch all tool results from Redis"""
        redis = await get_redis_client()
        results = []

        for tc in tool_calls:
            result_key = f"tool_result:{tc.id}"
            result = await redis.get(result_key)

            if result:
                results.append(json.loads(result))
                await redis.delete(result_key)  # Cleanup
            else:
                # Tool timeout or failure
                results.append({"error": "Tool execution timeout"})

        return results
```

**File to modify:**
- [services/orchestrator/src/main.py](services/orchestrator/src/main.py)

**Add second consumer:**
```python
# Existing: Consumer for new jobs
job_consumer = await create_consumer(
    topics=["agent.jobs.main"],
    group_id="orchestrator-jobs",
)
job_handler = JobHandler(...)
job_consumer.register_handler("agent.jobs.main", job_handler.handle_job)

# NEW: Consumer for job resumption
resume_consumer = await create_consumer(
    topics=["agent.job-resume"],
    group_id="orchestrator-resume",
)
resume_handler = JobResumeHandler(...)
resume_consumer.register_handler("agent.job-resume", resume_handler.handle_resume)

# Run both consumers concurrently
await asyncio.gather(
    job_consumer.run(),
    resume_consumer.run(),
)
```

### 3.6 Distributed State Locking

**File to create:**
- [services/orchestrator/src/services/state_lock.py](services/orchestrator/src/services/state_lock.py)

```python
class DistributedStateLock:
    """Prevent multiple orchestrators from processing same job"""

    def __init__(self, redis_client):
        self.redis = redis_client

    async def acquire(self, job_id: UUID, ttl: int = 300) -> bool:
        """Acquire lock for job (SETNX with expiration)"""
        lock_key = f"lock:job:{job_id}"
        acquired = await self.redis.set(lock_key, "locked", ex=ttl, nx=True)
        return bool(acquired)

    async def release(self, job_id: UUID) -> None:
        """Release lock"""
        await self.redis.delete(f"lock:job:{job_id}")

    async def extend(self, job_id: UUID, ttl: int = 300) -> None:
        """Extend lock TTL during long-running execution"""
        await self.redis.expire(f"lock:job:{job_id}", ttl)
```

**Usage in JobHandler:**
```python
async def handle_job(self, message: dict, headers: dict) -> None:
    job_id = UUID(message["job_id"])

    # Acquire distributed lock
    lock = DistributedStateLock(await get_redis_client())
    if not await lock.acquire(job_id):
        logger.warning("Job already being processed", job_id=str(job_id))
        return

    try:
        # ... execute job ...
    finally:
        await lock.release(job_id)
```

### 3.7 Incremental Message Persistence

**File to modify:**
- [services/orchestrator/src/handlers/job_handler.py](services/orchestrator/src/handlers/job_handler.py)

**Add to event publisher:**
```python
async def _publish_event(self, job_id: UUID, event_type: str, data: dict) -> None:
    """Publish event to Redis AND persist important events to PostgreSQL"""

    # Existing: Publish to Redis Pub/Sub + Streams
    await self.pubsub.publish(f"job:{job_id}", {"type": event_type, "data": data})
    await self.streams.add(f"events:{job_id}", {"type": event_type, "data": data})

    # NEW: Incrementally persist to PostgreSQL
    if event_type in ("message", "tool_result", "complete", "error"):
        await self._persist_to_db(job_id, event_type, data)

async def _persist_to_db(self, job_id: UUID, event_type: str, data: dict) -> None:
    """Persist event to PostgreSQL chat_messages table"""
    async with get_session_context() as session:
        # Get current max sequence
        result = await session.execute(
            select(func.max(ChatMessage.sequence_num))
            .where(ChatMessage.job_id == job_id)
        )
        max_seq = result.scalar() or -1

        if event_type == "message":
            msg = ChatMessage(
                job_id=job_id,
                sequence_num=max_seq + 1,
                role=MessageRole.ASSISTANT,
                content=data.get("content"),
                input_tokens=data.get("input_tokens"),
                output_tokens=data.get("output_tokens"),
            )
            session.add(msg)
            await session.commit()
```

### Testing Phase 3
- Unit: State lock (acquire, release, extend)
- Unit: Snapshot serialization/deserialization
- Integration: Full suspend/resume cycle (job dispatches tool → exits → tool completes → job resumes)
- Integration: Multiple orchestrators (ensure distributed locking prevents duplicate processing)
- Chaos: Kill orchestrator during tool execution (verify resume on different instance)

---

## Phase 4: Tool Workers & Archiver Completion (Week 4-5)

**Goal**: Implement actual tools and complete message archival

### 4.1 Core Tool Implementations

**Files to create/modify:**

**Web Search Tool:**
- [services/tool-workers/src/tools/web_search.py](services/tool-workers/src/tools/web_search.py)

```python
class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for current information"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "default": 5}
        },
        "required": ["query"]
    }

    async def execute(self, arguments: dict, context: dict) -> str:
        import httpx

        query = arguments["query"]
        max_results = arguments.get("max_results", 5)

        # Use DuckDuckGo or Brave Search API
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json"}
            )
            results = response.json()

        # Format results as markdown
        formatted = f"# Search results for: {query}\n\n"
        for i, result in enumerate(results[:max_results]):
            formatted += f"{i+1}. **{result['title']}**\n   {result['snippet']}\n   {result['url']}\n\n"

        return formatted
```

**Calculator Tool:**
- [services/tool-workers/src/tools/calculator.py](services/tool-workers/src/tools/calculator.py)

```python
class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Perform mathematical calculations"
    parameters = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "Math expression to evaluate"}
        },
        "required": ["expression"]
    }

    async def execute(self, arguments: dict, context: dict) -> str:
        import ast
        import operator

        # Safe eval using AST (prevents code injection)
        expr = arguments["expression"]
        try:
            result = self._safe_eval(expr)
            return f"Result: {result}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _safe_eval(self, expr: str) -> float:
        """Safely evaluate math expressions"""
        # Parse and validate AST
        # Only allow: numbers, +, -, *, /, ()
        # ... implementation ...
```

**File Operations Tool (with sandboxing):**
- [services/tool-workers/src/tools/file_operations.py](services/tool-workers/src/tools/file_operations.py)

### 4.2 Tool Registry Update

**File to modify:**
- [services/tool-workers/src/registry.py](services/tool-workers/src/registry.py)

```python
def register_all(self) -> None:
    """Register all available tools"""
    from .tools.web_search import WebSearchTool
    from .tools.calculator import CalculatorTool
    from .tools.file_operations import FileOperationsTool

    self.register(WebSearchTool())
    self.register(CalculatorTool())
    self.register(FileOperationsTool())

    logger.info("Registered tools", count=len(self._tools))
```

### 4.3 Archiver Event Mapping

**File to modify:**
- [services/archiver/src/services/postgres_writer.py](services/archiver/src/services/postgres_writer.py)

```python
async def _write_events(self, events: list[dict]) -> None:
    """Write events to PostgreSQL with type-specific handling"""

    async with get_session_context() as session:
        for event in events:
            event_type = event.get("type")

            if event_type == "message":
                await self._handle_message_event(event, session)
            elif event_type == "tool_call":
                await self._handle_tool_call_event(event, session)
            elif event_type == "tool_result":
                await self._handle_tool_result_event(event, session)
            elif event_type == "complete":
                await self._handle_complete_event(event, session)
            elif event_type == "error":
                await self._handle_error_event(event, session)

        await session.commit()

async def _handle_message_event(self, event: dict, session) -> None:
    """Persist assistant message to chat_messages table"""
    data = event["data"]
    job_id = UUID(event["job_id"])

    # Get next sequence number
    max_seq = await self._get_max_sequence(session, job_id)

    msg = ChatMessage(
        job_id=job_id,
        sequence_num=max_seq + 1,
        role=MessageRole.ASSISTANT,
        content=data.get("content"),
        input_tokens=data.get("input_tokens"),
        output_tokens=data.get("output_tokens"),
    )
    session.add(msg)
```

### Testing Phase 4
- Unit: Each tool (web search, calculator, file ops)
- Unit: Tool timeout handling
- Integration: Tool dispatch → execution → result → resume
- Integration: Archiver event mapping for all event types

---

## Phase 5: Configuration, Testing & Production Readiness (Week 5-6)

**Goal**: Comprehensive testing, documentation, and deployment preparation

### 5.1 Environment Configuration

**File to update:**
- [.env.example](.env.example)

```bash
# Master Admin Key (CRITICAL: Change in production)
MASTER_ADMIN_KEY=change_in_production_use_long_random_string

# Internal JWT Secret (separate from user JWT)
INTERNAL_JWT_SECRET=change_in_production_internal_secret_different_from_jwt

# Billing
ENABLE_BILLING_CHECKS=true
DEFAULT_CREDIT_BALANCE=100.00

# Suspend/Resume
ENABLE_SUSPEND_RESUME=true
MAX_TOOL_TIMEOUT_SECONDS=300
SNAPSHOT_SAVE_INTERVAL_ITERATIONS=5

# Rate Limiting
DEFAULT_TENANT_RPM=100
DEFAULT_TENANT_TPM=10000
DEFAULT_USER_RPM=10

# Tool Workers
ENABLE_WEB_SEARCH=true
ENABLE_CODE_EXECUTION=false  # Disabled by default for security
TOOL_WORKER_CONCURRENCY=10

# Redis
REDIS_STREAM_TTL_SECONDS=3600
REDIS_CACHE_TTL_SECONDS=300

# Kafka
KAFKA_CONSUMER_MAX_POLL_INTERVAL_MS=300000  # 5 minutes
KAFKA_JOB_RESUME_PARTITIONS=6
```

### 5.2 Comprehensive Test Suite

**Test structure:**
```
tests/
├── unit/
│   ├── api_gateway/
│   │   ├── test_auth.py               # JWT, API key, master admin
│   │   ├── test_billing.py            # Credit checks, reservations
│   │   ├── test_rate_limiting.py      # Waterfall logic
│   │   └── test_admin_router.py       # Tenant/user management
│   ├── orchestrator/
│   │   ├── test_agent_loop.py         # Think→Act→Observe
│   │   ├── test_suspend_resume.py     # State serialization
│   │   ├── test_state_lock.py         # Distributed locking
│   │   └── test_snapshot_service.py   # Snapshot persistence
│   ├── tool_workers/
│   │   ├── test_web_search.py
│   │   ├── test_calculator.py
│   │   └── test_tool_timeout.py
│   └── libs/
│       ├── test_internal_token.py     # Transaction token generation
│       └── test_api_key_cache.py      # Cache behavior
│
├── integration/
│   ├── test_job_lifecycle.py         # Full job flow (create → execute → complete)
│   ├── test_suspend_resume_flow.py   # Suspend → tool → resume
│   ├── test_admin_flows.py           # Master admin → tenant → user → job
│   ├── test_billing_flow.py          # Credit check → job → deduction
│   └── test_streaming.py             # SSE streaming with reconnection
│
└── e2e/
    ├── test_full_system.py           # End-to-end with all services
    └── test_chaos.py                 # Chaos testing (kill services mid-execution)
```

**Key test scenarios:**

1. **Admin Flow (test_admin_flows.py):**
   - Master admin creates tenant
   - Tenant receives API key
   - Tenant creates virtual user
   - User exchanges for JWT
   - User submits job

2. **Job Lifecycle (test_job_lifecycle.py):**
   - User submits chat completion
   - Job persisted to database
   - Orchestrator processes job
   - Events streamed to SSE client
   - Job completes, final message saved

3. **Suspend/Resume (test_suspend_resume_flow.py):**
   - Job requires tool execution
   - Orchestrator saves snapshot, exits
   - Tool worker processes tool
   - Resume signal sent to Kafka
   - Different orchestrator resumes from snapshot
   - Job continues and completes

4. **Billing (test_billing_flow.py):**
   - Tenant has 10 credits
   - Job estimated at 5 credits
   - Credits reserved (balance: 5)
   - Job executes
   - Actual cost calculated (3 credits)
   - Difference refunded (balance: 7)
   - Insufficient credits rejected (402 error)

5. **Rate Limiting (test_rate_limiting.py):**
   - Tenant limit: 100 RPM
   - User inherits 100 RPM (no custom limit)
   - User makes 101 requests → rate limited
   - User custom limit set to 200 RPM
   - User makes 150 requests → succeeds (override works)

6. **Chaos Testing (test_chaos.py):**
   - Start job with tool execution
   - Kill orchestrator during tool processing
   - Tool completes and sends resume signal
   - New orchestrator picks up resume
   - Job completes successfully (no data loss)

### 5.3 Monitoring & Observability

**File to create:**
- [libs/common/metrics.py](libs/common/metrics.py)

```python
class MetricsCollector:
    """Collect system metrics for monitoring"""

    async def record_job_duration(self, job_id: UUID, duration_ms: int, status: str):
        """Record job execution time"""
        # Publish to Prometheus, Datadog, etc.
        logger.info(
            "job_duration",
            job_id=str(job_id),
            duration_ms=duration_ms,
            status=status,
            extra={"metric_type": "job_duration"}
        )

    async def record_tool_execution(
        self,
        tool_name: str,
        duration_ms: int,
        success: bool
    ):
        """Record tool execution metrics"""
        logger.info(
            "tool_execution",
            tool_name=tool_name,
            duration_ms=duration_ms,
            success=success,
            extra={"metric_type": "tool_execution"}
        )

    async def record_token_usage(
        self,
        tenant_id: UUID,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cost: float
    ):
        """Record token usage for billing analytics"""
        logger.info(
            "token_usage",
            tenant_id=str(tenant_id),
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            extra={"metric_type": "token_usage"}
        )
```

**Integrate metrics:**
- Orchestrator: Record job duration, token usage
- Tool workers: Record tool execution time, success/failure rate
- API Gateway: Record rate limit hits, billing rejections

### 5.4 Documentation

**Files to create:**

1. **API Reference** - [docs/api_reference.md](docs/api_reference.md)
   - OpenAPI spec for all endpoints
   - Authentication requirements
   - Request/response examples
   - Error codes and handling

2. **Deployment Guide** - [docs/deployment.md](docs/deployment.md)
   - Production deployment steps
   - Infrastructure requirements (Kafka, Redis, PostgreSQL sizing)
   - Environment variable configuration
   - Scaling guidelines (horizontal scaling of orchestrators)
   - Health check endpoints

3. **Admin Guide** - [docs/admin_guide.md](docs/admin_guide.md)
   - How to create tenants
   - API key management
   - User management
   - Billing and credit management
   - Monitoring and troubleshooting

4. **Developer Guide** - [docs/developer_guide.md](docs/developer_guide.md)
   - How to add new tools
   - How to add new LLM providers
   - Testing guidelines
   - Local development setup

5. **Troubleshooting Guide** - [docs/troubleshooting.md](docs/troubleshooting.md)
   - Common issues and solutions
   - Debugging job failures
   - Kafka consumer lag issues
   - Redis memory management

**Update README.md:**
- Add architecture diagram
- Quick start guide
- Links to documentation
- Contributing guidelines

### Testing Phase 5
- Full test suite execution (unit + integration + e2e)
- Load testing: 100 concurrent jobs, measure latency/throughput
- Chaos testing: Random service kills, verify recovery
- Security audit: Rate limit bypass attempts, cross-tenant access attempts

---

## Implementation Sequencing & Dependencies

### Critical Path
```
Phase 1 (Auth) → Phase 2 (Billing + Internal Tokens) → Phase 3 (Suspend/Resume) → Phase 5 (Testing)
```

### Parallel Tracks
- **Track A**: Phase 1 → Phase 2 → Phase 3 (API Gateway + Orchestrator)
- **Track B**: Phase 4 (Tool Workers - can develop in parallel with Phase 3)

### Must Complete First
1. Phase 1.1-1.3: Auth system (blocks all other phases)
2. Phase 2.1: Internal transaction tokens (required for Phase 3)

### Can Parallelize
- Phase 1.5 (API key caching) + Phase 2.2 (Billing) - independent features
- Phase 4.1 (Tool implementations) + Phase 3 (Orchestrator refactor) - independent services

---

## Verification & Testing Strategy

### After Each Phase

**Phase 1 Verification:**
```bash
# Test master admin creates tenant
curl -X POST http://localhost:8000/admin/tenants \
  -H "Authorization: Bearer ${MASTER_ADMIN_KEY}" \
  -d '{"name": "Test Tenant", "slug": "test-tenant"}'

# Verify API key works
curl -X GET http://localhost:8000/health \
  -H "Authorization: Bearer ${TENANT_API_KEY}"

# Test token exchange
curl -X POST http://localhost:8000/v1/auth/token \
  -H "Authorization: Bearer ${TENANT_API_KEY}" \
  -d '{"user_id": "user_123"}'
```

**Phase 2 Verification:**
```bash
# Check billing pre-check
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -d '{
    "model": "claude-3-5-sonnet",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# Verify job in database
psql $DATABASE_URL -c "SELECT id, status, tenant_id FROM jobs.jobs ORDER BY created_at DESC LIMIT 1;"

# Check initial message saved
psql $DATABASE_URL -c "SELECT job_id, role, content FROM jobs.chat_messages ORDER BY created_at DESC LIMIT 1;"
```

**Phase 3 Verification:**
```bash
# Submit job with tool requirement
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -d '{
    "model": "claude-3-5-sonnet",
    "messages": [{"role": "user", "content": "Search for latest AI news"}],
    "tools": [{"name": "web_search"}]
  }'

# Check snapshot was created
psql $DATABASE_URL -c "SELECT job_id, sequence_num, state_data->'iteration' FROM jobs.job_snapshots ORDER BY created_at DESC LIMIT 1;"

# Verify resume signal in Kafka
kafka-console-consumer --bootstrap-server localhost:9092 --topic agent.job-resume --from-beginning
```

**Phase 4 Verification:**
```bash
# Test web search tool
# (Submit job that requires web search, verify results)

# Check archiver persisted events
psql $DATABASE_URL -c "SELECT job_id, role, content FROM jobs.chat_messages WHERE role = 'tool' ORDER BY created_at DESC LIMIT 5;"
```

---

## Risk Mitigation

### High-Risk Changes

1. **Orchestrator Suspend/Resume Refactor (Phase 3)**
   - **Risk**: Breaking existing job execution
   - **Mitigation**:
     - Feature flag `ENABLE_SUSPEND_RESUME` (default: false initially)
     - Keep old polling code as fallback
     - Gradual rollout: test on 10% of jobs first

2. **Billing Pre-Checks (Phase 2.2)**
   - **Risk**: False positives blocking legitimate requests
   - **Mitigation**:
     - Start with logging-only mode (don't reject)
     - Monitor logs for 1 week
     - Enable enforcement after validation

3. **Database Transaction in Job Creation (Phase 2.4)**
   - **Risk**: Performance degradation, deadlocks
   - **Mitigation**:
     - Use short transactions (insert Job + messages only)
     - Kafka publish happens after DB commit
     - Add database query timeout (5 seconds max)

### Rollback Strategy

- **Database migrations**: All migrations have down() methods
- **Feature flags**: Can disable new features without redeployment
- **Kafka topics**: New topics deployed alongside old ones
- **API versioning**: Maintain backward compatibility for one version

### Monitoring During Rollout

**Key metrics to watch:**
- Job creation latency (should stay < 500ms)
- Orchestrator CPU usage (should decrease with suspend/resume)
- Tool execution success rate (should be > 95%)
- Rate limit false positives (should be 0)
- Billing rejection rate (track legitimate vs insufficient credits)

---

## Critical Files Summary

**Top 5 Most Critical Files for Implementation:**

1. **[services/api-gateway/src/routers/chat.py](services/api-gateway/src/routers/chat.py)**
   - Add DB transaction (Job + ChatMessage persistence)
   - Add billing pre-check
   - Generate internal transaction token
   - Most impactful change

2. **[services/orchestrator/src/engine/agent.py](services/orchestrator/src/engine/agent.py)**
   - Refactor to exit on tool dispatch (suspend)
   - Remove blocking tool result polling
   - Core architectural change

3. **[services/orchestrator/src/handlers/resume_handler.py](services/orchestrator/src/handlers/resume_handler.py)** *(new file)*
   - Handle job resumption from snapshots
   - Fetch tool results from Redis
   - Continue execution
   - Enables true suspend/resume

4. **[services/api-gateway/src/routers/admin.py](services/api-gateway/src/routers/admin.py)** *(new file)*
   - Tenant creation and management
   - API key generation
   - Foundation for multi-tier auth

5. **[services/tool-workers/src/main.py](services/tool-workers/src/main.py)**
   - Publish resume signal to Kafka (not just Redis)
   - Enable orchestrator resumption
   - Critical for suspend/resume workflow

---

## Success Criteria

### Phase Completion Criteria

**Phase 1 Complete When:** -- ALL MET
- [x] Master admin can create tenants via API
- [x] Tenants receive API keys and can authenticate
- [x] Virtual users can exchange for JWT tokens
- [x] API key cache reduces DB queries by >80%

**Phase 2 Complete When:** -- ALL MET
- [x] Jobs are persisted to database before Kafka publish
- [x] Initial user messages saved to chat_messages table
- [x] Billing pre-check rejects jobs with insufficient credits (feature-flagged)
- [x] Internal transaction tokens generated with internal_jwt_secret (10-min TTL)
- [x] Waterfall rate limiting enforces tenant and user limits (custom + inheritance)

**Phase 3 Complete When:**
- ✅ Orchestrator exits immediately after tool dispatch
- ✅ Job snapshot saved to PostgreSQL
- ✅ Tool completion triggers Kafka resume signal
- ✅ Different orchestrator instance can resume job
- ✅ Distributed locking prevents duplicate processing

**Phase 4 Complete When:**
- ✅ At least 3 tools implemented (web search, calculator, file ops)
- ✅ Tool timeout handling works correctly
- ✅ Archiver persists all event types to PostgreSQL

**Phase 5 Complete When:**
- ✅ All tests pass (unit + integration + e2e)
- ✅ Load test: 100 concurrent jobs complete successfully
- ✅ Chaos test: Service kills don't cause data loss
- ✅ Documentation complete (API, deployment, admin, developer)

### Production Readiness Checklist

- [ ] All database migrations applied
- [ ] Environment variables configured (.env from .env.example)
- [ ] Master admin key rotated to secure value
- [ ] Internal JWT secret set (different from user JWT)
- [ ] Redis eviction policy configured (volatile-lru)
- [ ] Kafka topics created with correct partitioning
- [ ] Health check endpoints return 200
- [ ] Monitoring/logging configured
- [ ] Rate limits configured per tenant tier
- [ ] Default credit balance set for new tenants
- [ ] CORS allowed origins configured (not "*")
- [ ] SSL/TLS certificates installed
- [ ] Backup strategy implemented (PostgreSQL daily backups)

---

## Estimated Timeline

- **Phase 1**: 1.5 weeks (auth is complex)
- **Phase 2**: 1 week (billing + job persistence)
- **Phase 3**: 1.5 weeks (suspend/resume is critical)
- **Phase 4**: 1 week (tool implementations)
- **Phase 5**: 1 week (testing + docs)

**Total**: 6 weeks (with some buffer for unexpected issues)

**Can be accelerated to 4-5 weeks** if Phase 4 (tools) is parallelized with Phase 3.

---

## Next Steps

Phase 1 and Phase 2 are complete. Continuing with:

1. **Phase 3**: Orchestrator suspend/resume refactor (highest priority, core architecture change)
2. **Phase 4**: Tool worker enhancements + archiver completion (can parallelize with Phase 3)
3. **Phase 5**: Comprehensive testing, load testing, chaos testing, documentation

See **[docs/next-phases.md](next-phases.md)** for the full technical implementation plan for Phases 3-5.

---

## Questions -- RESOLVED

1. **Billing**: Integer microdollars (1,000,000 = $1.00). Avoids floating-point, Redis DECRBY compatible.
2. **Rate Limiting**: RPM (requests per minute) with 60-second sliding window via Redis sorted sets.
3. **Tool Workers**: Web search (real API) and calculator are Phase 4 priority. Code executor exists.
4. **Suspend/Resume**: Feature-flagged approach — old polling kept as fallback initially.
5. **Admin Access**: Single master admin key via env var (current approach). Database-backed multi-admin deferred.

---

## Appendix: Key Design Decisions

### Why Suspend/Resume?

**Problem**: Long-running tool executions (e.g., video rendering) block orchestrator CPU.

**Solution**: Orchestrator saves state to PostgreSQL, dispatches tool to Kafka, exits immediately. When tool completes, Kafka resume signal triggers another orchestrator to load snapshot and continue.

**Benefit**:
- Orchestrator pool can handle 10x more concurrent jobs
- Fault tolerant (orchestrator crash doesn't lose job progress)
- Scales horizontally (any orchestrator can resume any job)

### Why Internal Transaction Tokens?

**Problem**: Kafka consumers can't access HTTP request context (API key, tenant_id validation).

**Solution**: Gateway generates signed JWT containing job_id, tenant_id, billing status. Token travels in Kafka message headers. Workers verify signature before execution.

**Benefit**:
- Security: Workers can verify job legitimacy
- Audit trail: Tokens contain trace_id for distributed tracing
- Prevents malicious Kafka message injection

### Why Waterfall Rate Limiting?

**Problem**: Different users within same tenant may have different usage patterns (enterprise customer with VIP users).

**Solution**: Check tenant-level limit first (hard cap), then check user-specific limit (if set) or inherit tenant default.

**Benefit**:
- Flexible: Per-user overrides without affecting tenant quota
- Fair: Prevents single user from consuming entire tenant quota
- Scalable: Redis atomic counters handle high throughput

---

*End of Plan*