"""Chat completion endpoint."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from libs.common import get_logger
from libs.common.exceptions import ValidationError
from libs.messaging.kafka import get_producer

from ..config import get_config
from ..middleware.tenant import get_tenant_id

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
    status: str = "pending"


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    request: Request,
    body: ChatCompletionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ChatCompletionResponse:
    """Create a new chat completion.

    This endpoint:
    1. Validates the request
    2. Creates a job in the queue
    3. Returns the job ID and stream URL

    The client should connect to the stream URL to receive real-time events.
    """
    config = get_config()

    # Generate job ID
    job_id = uuid.uuid4()

    # Validate messages
    if not body.messages:
        raise ValidationError(
            message="At least one message is required",
            errors=[{"field": "messages", "message": "Cannot be empty"}],
        )

    # Get user ID if available
    user_id = getattr(request.state, "user_id", None)

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

    # Create job payload
    job_payload = {
        "job_id": str(job_id),
        "tenant_id": str(tenant_id),
        "user_id": str(user_id) if user_id else None,
        "provider": provider,
        "model": model,
        "messages": [msg.model_dump() for msg in body.messages],
        "system": body.system,
        "tools": [tool.model_dump() for tool in body.tools] if body.tools else None,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "stream": body.stream,
        "metadata": body.metadata or {},
    }

    # Publish to Kafka
    producer = await get_producer()
    await producer.send(
        topic=config.jobs_topic,
        message=job_payload,
        key=str(tenant_id),  # Partition by tenant for ordering
        headers={
            "job_id": str(job_id),
            "tenant_id": str(tenant_id),
        },
    )

    logger.info(
        "Chat completion job created",
        job_id=str(job_id),
        tenant_id=str(tenant_id),
        provider=provider,
        model=model,
    )

    # Build stream URL
    stream_url = f"{config.stream_edge_url}/api/v1/events/{job_id}"

    return ChatCompletionResponse(
        job_id=str(job_id),
        stream_url=stream_url,
        status="pending",
    )
