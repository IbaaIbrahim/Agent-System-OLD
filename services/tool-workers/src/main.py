"""Tool Workers - Entry point for tool execution workers."""

import asyncio
import json
import signal
import sys
from typing import Any

from libs.common import get_logger, setup_logging
from libs.common.tool_catalog import ToolBehavior
from libs.messaging.kafka import create_consumer
from libs.messaging.redis import get_redis_client

from .config import get_config
from .registry import ToolRegistry

logger = get_logger(__name__)


def _create_error_result(error_code: str, message: str) -> str:
    """Create a standardized error result JSON string.

    Args:
        error_code: Error code (e.g., "tool_not_found", "plan_access_denied")
        message: Human-readable error message

    Returns:
        JSON string with error details
    """
    return json.dumps({
        "error": error_code,
        "message": message,
        "success": False,
    })


async def handle_tool_request(
    message: dict[str, Any],
    headers: dict[str, str],
) -> None:
    """Handle an incoming tool execution request.

    Validates tool access before execution:
    1. Check if tool exists
    2. Check plan access (required_plan_feature)
    3. Check user-enabled status (for USER_ENABLED tools)
    4. Execute tool

    Args:
        message: Tool request payload
        headers: Kafka message headers
    """
    tool_call_id = message["tool_call_id"]
    tool_name = message["tool_name"]
    arguments = message.get("arguments", {})
    job_id = message["job_id"]
    tenant_id = message["tenant_id"]
    plan_features = message.get("plan_features", [])  # From ITT v2
    enabled_tools = message.get("enabled_tools", [])  # From frontend (user toggles)

    logger.info(
        "Processing tool request",
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        job_id=job_id,
    )

    try:
        # Get tool from registry
        registry = ToolRegistry()
        tool = registry.get_tool(tool_name)

        # 1. Check if tool exists
        if tool is None:
            logger.warning(
                "Tool not found",
                tool_name=tool_name,
                job_id=job_id,
            )
            result = _create_error_result(
                "tool_not_found",
                f"Tool '{tool_name}' does not exist",
            )
        # 2. Check plan access
        elif (
            tool.required_plan_feature
            and tool.required_plan_feature not in plan_features
        ):
            logger.warning(
                "Plan access denied",
                tool_name=tool_name,
                required_feature=tool.required_plan_feature,
                plan_features=plan_features,
                job_id=job_id,
            )
            result = _create_error_result(
                "plan_access_denied",
                f"Tool '{tool_name}' requires plan feature: {tool.required_plan_feature}",
            )
        # 3. Check user enabled (for USER_ENABLED tools)
        elif (
            tool.behavior == ToolBehavior.USER_ENABLED
            and tool_name not in enabled_tools
        ):
            logger.warning(
                "Tool not enabled by user",
                tool_name=tool_name,
                enabled_tools=enabled_tools,
                job_id=job_id,
            )
            result = _create_error_result(
                "tool_not_enabled",
                f"Tool '{tool_name}' is not enabled by user",
            )
        else:
            # 4. Execute tool
            result = await tool.execute(
                arguments=arguments,
                context={
                    "job_id": job_id,
                    "tenant_id": tenant_id,
                    "tool_call_id": tool_call_id,
                },
            )

    except Exception as e:
        logger.error(
            "Tool execution failed",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            error=str(e),
        )
        result = _create_error_result(
            "execution_failed",
            str(e),
        )

    # Store result in Redis for orchestrator to pick up
    redis = await get_redis_client()
    result_key = f"tool_result:{tool_call_id}"
    await redis.set(result_key, result, ex=300)  # 5 minute expiry

    logger.info(
        "Tool execution complete",
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        result_length=len(result),
    )

    # Publish resume signal to Kafka for suspend/resume architecture
    try:
        from libs.messaging.kafka import get_producer

        config = get_config()
        producer = await get_producer()

        await producer.send(
            topic=config.resume_topic,
            message={
                "job_id": job_id,
                "tool_call_id": tool_call_id,
                "snapshot_sequence": message.get("snapshot_sequence", 0),
                "status": "completed",
                "tool_name": tool_name,
            },
            key=job_id,  # Partition by job_id to maintain ordering
            headers={
                "tool_call_id": tool_call_id,
                "job_id": job_id,
            },
        )

        logger.info(
            "Resume signal published",
            job_id=job_id,
            tool_call_id=tool_call_id,
        )
    except Exception as e:
        logger.error(
            "Failed to publish resume signal",
            job_id=job_id,
            tool_call_id=tool_call_id,
            error=str(e),
        )
        # Don't fail the tool execution if resume signal fails
        # The orchestrator will timeout and handle it


async def main() -> None:
    """Main entry point for tool workers."""
    config = get_config()

    # Setup logging
    setup_logging(
        service_name="tool-workers",
        log_level=config.log_level,
        log_format=config.log_format,
    )

    logger.info("Starting Tool Workers")

    # Initialize Redis
    await get_redis_client()

    # Initialize tool registry
    registry = ToolRegistry()
    registry.register_all()
    logger.info(f"Registered {len(registry.tools)} tools")

    # Create Kafka consumer
    consumer = await create_consumer(
        topics=[config.tools_topic],
        group_id=config.consumer_group,
        dlq_topic=config.tools_dlq_topic,
    )

    consumer.register_handler(config.tools_topic, handle_tool_request)

    # Setup signal handlers
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def signal_handler():
        shutdown_event.set()
        logger.info("Shutdown signal received")

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)
    else:
        logger.info("Signal handlers skipped (not supported on Windows asyncio loop)")

    logger.info("Tool Workers started successfully")

    # Start consumer
    try:
        await consumer.start()
        
        # Create task for consumer
        consumer_task = asyncio.create_task(consumer.run())
        
        # Wait for shutdown signal or task to finish
        wait_for_shutdown = asyncio.create_task(shutdown_event.wait())
        
        done, pending = await asyncio.wait(
            [consumer_task, wait_for_shutdown],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if wait_for_shutdown in done:
            logger.info("Shutdown signal received, stopping consumer...")
        
        # Cancel tasks
        for task in [consumer_task, wait_for_shutdown]:
            if not task.done():
                task.cancel()
        
        await asyncio.gather(consumer_task, return_exceptions=True)

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down Tool Workers")
        await consumer.stop()
        logger.info("Tool Workers shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
