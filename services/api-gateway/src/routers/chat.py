"""Chat completion endpoint with DB persistence and billing."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from libs.common import get_logger
from libs.common.auth import create_internal_transaction_token, create_stream_ott
from libs.common.exceptions import ValidationError
from libs.db.models import ChatMessage as ChatMessageModel
from libs.db.models import Job, JobStatus, MessageRole
from libs.db.session import get_session_context
from libs.messaging.kafka import get_producer

from ..config import get_config
from ..middleware.tenant import get_tenant_id
from ..services.billing import (
    BillingError,
    BillingService,
    estimate_tokens_from_messages,
)
from ..services.feature import get_feature_service
from ..services.subscription import get_subscription_service

logger = get_logger(__name__)

router = APIRouter()


class ChatMessage(BaseModel):
    """Chat message in request."""

    role: str = Field(..., pattern="^(system|user|assistant|tool)$")
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ToolDefinition(BaseModel):
    """Tool definition for function calling."""

    name: str
    description: str
    parameters: dict[str, Any]
    category: str = "builtin"  # builtin, configurable, or client_side


class ChatCompletionRequest(BaseModel):
    """Request body for chat completions."""

    messages: list[ChatMessage] = Field(..., min_length=1)
    model: str | None = None
    provider: str | None = Field(None, pattern="^(anthropic|openai)$")
    system: str | None = None
    tools: list[ToolDefinition] | None = None
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=1, le=200000)
    stream: bool = True
    metadata: dict[str, Any] | None = None


class ChatCompletionResponse(BaseModel):
    """Response from chat completions endpoint."""

    job_id: str
    stream_url: str
    stream_token: str
    status: str = "pending"
    created_at: str | None = None


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    request: Request,
    body: ChatCompletionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ChatCompletionResponse:
    """Create a new chat completion.

    Flow:
    1. Validate request
    2. Billing pre-check (if enabled)
    3. Persist Job + ChatMessages to DB
    4. Generate internal transaction token
    5. Publish to Kafka
    6. Return job ID and stream URL
    """
    config = get_config()

    # Entry log for request (don't log sensitive tokens)
    logger.info(
        "Create chat completion request received",
        tenant_id=str(tenant_id),
        provider=body.provider,
        model=body.model,
        message_count=len(body.messages),
        stream=body.stream,
    )

    # Generate job ID
    job_id = uuid.uuid4()

    # Validate messages
    if not body.messages:
        raise ValidationError(
            message="At least one message is required",
            errors=[{"field": "messages", "message": "Cannot be empty"}],
        )

    # Get user ID and partner ID if available
    user_id = getattr(request.state, "user_id", None)
    partner_id = getattr(request.state, "partner_id", None)

    # Determine provider and model
    provider = body.provider or config.default_llm_provider
    model = body.model

    if not model:
        if provider == "anthropic":
            model = config.anthropic_default_model
        elif provider == "openai":
            model = config.openai_default_model
        else:
            model = config.anthropic_default_model

    # --- Step 0.5: Inject builtin tools based on enabled_tools ---
    # If the frontend specifies enabled_tools in metadata, we inject tool definitions
    builtin_tools_catalog = {
        "web_search": ToolDefinition(
            name="web_search",
            description="Search the web for information. Use this when you need to find current information, facts, or data that may not be in your training data.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5, max: 10)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
    }

    # Start with user-provided tools (if any)
    final_tools = list(body.tools) if body.tools else []

    # Check for enabled_tools in metadata and inject builtin tools
    enabled_tools = (body.metadata or {}).get("enabled_tools", [])
    for tool_name in enabled_tools:
        if tool_name in builtin_tools_catalog:
            # Check if tool is already in the list
            if not any(t.name == tool_name for t in final_tools):
                final_tools.append(builtin_tools_catalog[tool_name])

    logger.debug(
        "Final tools after injection",
        job_id=str(job_id),
        enabled_tools=enabled_tools,
        final_tool_names=[t.name for t in final_tools],
    )

    # --- Step 1: Billing pre-check (feature-flagged) ---
    credit_check_passed = True
    reservation_id: str | None = None

    if config.enable_billing_checks:
        billing = BillingService()
        estimated_tokens = estimate_tokens_from_messages(body.messages)

        if not await billing.check_credit_balance(
            tenant_id, estimated_tokens, provider, model
        ):
            raise BillingError(
                "Insufficient credits",
                details={
                    "tenant_id": str(tenant_id),
                    "estimated_tokens": estimated_tokens,
                },
            )

        estimated_cost_micros = await billing.estimate_cost(
            estimated_tokens, provider, model
        )
        reservation_id = await billing.reserve_credits(
            tenant_id, estimated_cost_micros
        )

    # --- Step 2: DB transaction — persist Job + ChatMessages ---
    now = datetime.now(UTC)

    async with get_session_context() as session:
        # Create Job record
        job = Job(
            id=job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            status=JobStatus.PENDING,
            provider=provider,
            model_id=model,
            system_prompt=body.system,
            tools_config=(
                [tool.model_dump() for tool in final_tools] if final_tools else None
            ),
            metadata_=body.metadata or {},
        )
        session.add(job)

        # Create ChatMessage records for initial messages
        for idx, msg in enumerate(body.messages):
            chat_msg = ChatMessageModel(
                job_id=job_id,
                sequence_num=idx,
                role=MessageRole(msg.role),
                content=msg.content,
                tool_calls=msg.tool_calls,
                tool_call_id=msg.tool_call_id,
            )
            session.add(chat_msg)

        await session.flush()  # Ensure IDs are generated

    logger.info(
        "Job persisted to database",
        job_id=str(job_id),
        tenant_id=str(tenant_id),
        message_count=len(body.messages),
    )

    # --- Step 2.5: Determine plan-allowed tools ---
    plan_tools: list[str] | None = None
    if final_tools and partner_id:
        # Get tenant's subscription to find plan_id
        subscription_service = get_subscription_service()
        feature_service = get_feature_service()

        subscription = await subscription_service.get_active_subscription(tenant_id)
        plan_id = subscription.plan_id if subscription else None

        # Check which tools are allowed by the plan
        allowed_tools = []
        for tool in final_tools:
            # Map tool names to feature slugs (tool name = feature slug for simplicity)
            feature_slug = tool.name
            is_allowed = await feature_service.check_feature_enabled(
                partner_id=partner_id,
                plan_id=plan_id,
                feature_slug=feature_slug,
            )
            if is_allowed:
                allowed_tools.append(tool.name)

        plan_tools = allowed_tools
        logger.debug(
            "Plan tools determined",
            job_id=str(job_id),
            plan_tools=plan_tools,
            requested_tools=[t.name for t in final_tools],
        )

    # --- Step 3: Generate internal transaction token ---
    internal_token = create_internal_transaction_token(
        job_id=job_id,
        tenant_id=tenant_id,
        credit_check_passed=credit_check_passed,
        max_tokens=body.max_tokens,
        partner_id=partner_id,
    )

    # --- Step 4: Publish to Kafka ---
    # Merge plan_tools into metadata
    job_metadata = body.metadata.copy() if body.metadata else {}
    if plan_tools is not None:
        job_metadata["plan_tools"] = plan_tools

    job_payload = {
        "job_id": str(job_id),
        "tenant_id": str(tenant_id),
        "partner_id": str(partner_id) if partner_id else None,
        "user_id": str(user_id) if user_id else None,
        "provider": provider,
        "model": model,
        "messages": [msg.model_dump() for msg in body.messages],
        "system": body.system,
        "tools": [tool.model_dump() for tool in final_tools] if final_tools else None,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "stream": body.stream,
        "metadata": job_metadata,
    }

    producer = await get_producer()
    # Log summary before sending to Kafka
    try:
        logger.info(
            "Publishing job to Kafka",
            job_id=str(job_id),
            topic=config.jobs_topic,
            provider=provider,
            model=model,
            message_size=len(str(job_payload)) if job_payload else 0,
            headers_count=4,
        )
        await producer.send(
            topic=config.jobs_topic,
            message=job_payload,
            key=str(tenant_id),
            headers={
                "job_id": str(job_id),
                "tenant_id": str(tenant_id),
                "partner_id": str(partner_id) if partner_id else "",
                # do NOT log internal_token value to avoid leaking secrets
                "internal_token": "present" if internal_token else "",
            },
        )
        logger.info(
            "Chat completion job published to Kafka (confirmed send)",
            job_id=str(job_id),
            topic=config.jobs_topic,
        )
    except Exception as e:
        logger.exception(
            "Failed to publish chat completion job to Kafka",
            job_id=str(job_id),
            error=str(e),
        )
        raise

    logger.debug(
        "Chat completion job metadata",
        job_id=str(job_id),
        tenant_id=str(tenant_id),
        provider=provider,
        model=model,
        billing_enabled=config.enable_billing_checks,
        reservation_id=reservation_id,
    )

    # Generate stream one-time token
    stream_ott = create_stream_ott(
        job_id=job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        partner_id=partner_id,
    )

    # Build stream URL with OTT
    stream_url = f"{config.stream_edge_url}/api/v1/stream?token={stream_ott}"

    return ChatCompletionResponse(
        job_id=str(job_id),
        stream_url=stream_url,
        stream_token=stream_ott,
        status="pending",
        created_at=now.isoformat(),
    )
