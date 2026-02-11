"""Tool-related endpoints including client tool results."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from libs.common import get_logger
from libs.messaging.kafka import get_producer
from libs.messaging.redis import get_redis_client

from ..config import get_config
from ..middleware.tenant import get_tenant_id

logger = get_logger(__name__)

router = APIRouter(prefix="/tools", tags=["Tools"])


class ToolResultRequest(BaseModel):
    """Request body for submitting a client-side tool result."""

    tool_call_id: str = Field(..., description="ID of the tool call")
    tool_name: str = Field(..., description="Name of the tool")
    result: str = Field(..., description="Result of the tool execution")


class ToolResultResponse(BaseModel):
    """Response after submitting a tool result."""

    status: str = "accepted"
    tool_call_id: str


class AvailableToolInfo(BaseModel):
    """Information about an available tool."""

    name: str
    description: str
    category: str  # builtin, configurable, client_side
    plan_allowed: bool
    user_enabled: bool
    required_plan: str | None = None


class AvailableToolsResponse(BaseModel):
    """Response with list of available tools and their status."""

    tools: list[AvailableToolInfo]


@router.post("/jobs/{job_id}/tool-results", response_model=ToolResultResponse)
async def submit_tool_result(
    job_id: uuid.UUID,
    body: ToolResultRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ToolResultResponse:
    """Submit a client-side tool execution result.

    This endpoint is called by the frontend after executing a client-side
    tool (like read_page or get_element). The result is stored in Redis
    and a resume signal is published to continue the agent execution.

    Args:
        job_id: The job ID this tool result belongs to
        body: Tool result details
        tenant_id: Tenant ID from auth

    Returns:
        Confirmation of result acceptance
    """
    config = get_config()

    logger.info(
        "Received client tool result",
        job_id=str(job_id),
        tool_call_id=body.tool_call_id,
        tool_name=body.tool_name,
        result_length=len(body.result),
        tenant_id=str(tenant_id),
    )

    try:
        import json

        # Parse result to check for screenshot
        result_data = json.loads(body.result)
        enhanced_result = body.result  # Default to original result

        # Check if result contains screenshot that needs to be saved as a real file
        if (
            isinstance(result_data, dict)
            and result_data.get("screenshot")
            and result_data.get("screenshot_analysis_pending")
        ):
            logger.info(
                "Screenshot detected, saving as file for analysis",
                job_id=str(job_id),
                tool_call_id=body.tool_call_id,
            )

            try:
                import base64
                import uuid as uuid_mod

                from ..services.file_storage import FileStorageService
                from libs.db.models import FileUpload as FileUploadModel
                from libs.db.session import get_session_context

                screenshot_base64 = result_data["screenshot"]
                screenshot_query = result_data.get(
                    "screenshot_query", "Describe everything visible in this screenshot in full detail."
                )

                # Decode base64 to raw bytes
                screenshot_bytes = base64.b64decode(screenshot_base64)

                # Store as a real file in Redis via FileStorageService
                file_metadata = {
                    "filename": f"screenshot_{body.tool_call_id[:8]}.png",
                    "content_type": "image/png",
                    "tenant_id": str(tenant_id),
                    "source": "take_screenshot",
                    "job_id": str(job_id),
                }

                file_id = await FileStorageService.store_file(screenshot_bytes, file_metadata)

                # Persist file metadata to PostgreSQL
                async with get_session_context() as session:
                    file_upload = FileUploadModel(
                        id=uuid_mod.UUID(file_id),
                        tenant_id=tenant_id,
                        job_id=job_id,
                        filename=file_metadata["filename"],
                        content_type="image/png",
                        size_bytes=len(screenshot_bytes),
                        storage_key=f"file:{file_id}",
                        metadata_=file_metadata,
                    )
                    session.add(file_upload)
                    await session.commit()

                logger.info(
                    "Screenshot saved as file",
                    file_id=file_id,
                    size_bytes=len(screenshot_bytes),
                    tool_call_id=body.tool_call_id,
                )

                # Build compact result: file_id + instructions for the agent
                result_data = {
                    "screenshot_file_id": file_id,
                    "screenshot_filename": file_metadata["filename"],
                    "screenshot_size_bytes": len(screenshot_bytes),
                    "screenshot_query": screenshot_query,
                    "screenshot_analysis_pending": True,
                    "instructions": (
                        f"Screenshot has been saved as file '{file_id}'. "
                        "Use the 'analyze_file' tool with this file_id and the screenshot_query "
                        "to get a detailed visual analysis. After analysis, the result will be "
                        "cached and retrievable via 'get_file_description'."
                    ),
                }

            except Exception as file_error:
                logger.error(
                    "Failed to save screenshot as file",
                    tool_call_id=body.tool_call_id,
                    error=str(file_error),
                )
                # Remove base64 and provide error
                result_data = {
                    "error": f"Failed to save screenshot: {str(file_error)}",
                    "screenshot_analysis_pending": False,
                }

            enhanced_result = json.dumps(result_data)

        # Store result in Redis
        redis = await get_redis_client()
        result_key = f"tool_result:{body.tool_call_id}"
        await redis.set(result_key, enhanced_result, ex=300)  # 5 minute expiry

        # Publish resume signal to Kafka
        producer = await get_producer()
        await producer.send(
            topic=config.resume_topic,
            message={
                "job_id": str(job_id),
                "tool_call_id": body.tool_call_id,
                "status": "completed",
                "tool_name": body.tool_name,
            },
            key=str(job_id),
        )

        logger.info(
            "Client tool result stored and resume signal sent",
            job_id=str(job_id),
            tool_call_id=body.tool_call_id,
        )

        return ToolResultResponse(
            status="accepted",
            tool_call_id=body.tool_call_id,
        )

    except Exception as e:
        logger.error(
            "Failed to process client tool result",
            job_id=str(job_id),
            tool_call_id=body.tool_call_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process tool result: {str(e)}",
        )


@router.get("/available", response_model=AvailableToolsResponse)
async def get_available_tools(
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> AvailableToolsResponse:
    """Get list of available tools with plan and user status.

    Returns all tools that could be available, including:
    - Whether the user's plan allows each tool
    - Whether the user has enabled each tool
    - Required plan for locked tools

    This is used by the frontend to render the tool toggle UI.
    """
    from sqlalchemy import select

    from libs.db.models import Tenant, TenantSubscription, User
    from libs.db.session import get_session_context

    from ..services.feature import get_feature_service
    from ..services.subscription import get_subscription_service

    # Get user ID from request (if authenticated as user)
    user_id = getattr(request.state, "user_id", None)

    # Get user's subscription and preferences
    subscription_service = get_subscription_service()
    feature_service = get_feature_service()

    subscription = await subscription_service.get_active_subscription(tenant_id)
    plan_id = subscription.plan_id if subscription else None

    # Get partner_id and user preferences
    partner_id = None
    user_enabled_tools: list[str] = []

    async with get_session_context() as session:
        # Get tenant to find partner_id
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = tenant_result.scalar_one_or_none()
        partner_id = tenant.partner_id if tenant else None

        # Get user's tool preferences if user is authenticated
        if user_id:
            user_result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = user_result.scalar_one_or_none()
            if user and user.tool_preferences:
                user_enabled_tools = user.tool_preferences.get("enabled_tools", [])

    # Define available tools with their categories
    # In a full implementation, this would come from the tool registry
    available_tools_config = [
        {
            "name": "generate_checklist",
            "description": "Generate structured Flowdit checklists",
            "category": "configurable",
            "feature_slug": "checklist_generation",
        },
        {
            "name": "web_search",
            "description": "Search the web for information",
            "category": "builtin",
            "feature_slug": None,  # Built-in, always available
        },
        {
            "name": "read_page_content",
            "description": "Read the content of the current page",
            "category": "client_side",
            "feature_slug": "read_page",
        },
        {
            "name": "take_screenshot",
            "description": "Capture a screenshot of the current page",
            "category": "client_side",
            "feature_slug": "screenshot",
        },
        {
            "name": "inspect_dom_element",
            "description": "Inspect a specific element by CSS selector",
            "category": "client_side",
            "feature_slug": "get_element",
        },
        {
            "name": "analyze_file",
            "description": "Analyze uploaded files using vision models with detailed descriptions",
            "category": "builtin",
            "feature_slug": None,
        },
        {
            "name": "get_file_description",
            "description": "Fetch a cached file analysis description from the database",
            "category": "builtin",
            "feature_slug": None,
        },
    ]

    tools = []
    for tool_config in available_tools_config:
        # Check plan access
        plan_allowed = True
        required_plan = None

        if tool_config["category"] != "builtin" and partner_id and tool_config["feature_slug"]:
            plan_allowed = await feature_service.check_feature_enabled(
                partner_id=partner_id,
                plan_id=plan_id,
                feature_slug=tool_config["feature_slug"],
            )
            if not plan_allowed:
                required_plan = "pro"  # Default to "pro" for locked features

        # Check user enablement
        user_enabled = tool_config["name"] in user_enabled_tools

        tools.append(AvailableToolInfo(
            name=tool_config["name"],
            description=tool_config["description"],
            category=tool_config["category"],
            plan_allowed=plan_allowed,
            user_enabled=user_enabled,
            required_plan=required_plan,
        ))

    return AvailableToolsResponse(tools=tools)
