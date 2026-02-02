"""Tool Workers - Entry point for tool execution workers."""

import asyncio
import signal
from typing import Any

from libs.common import get_logger, setup_logging
from libs.messaging.kafka import create_consumer
from libs.messaging.redis import get_redis_client

from .config import get_config
from .registry import ToolRegistry

logger = get_logger(__name__)


async def handle_tool_request(
    message: dict[str, Any],
    headers: dict[str, str],
) -> None:
    """Handle an incoming tool execution request.

    Args:
        message: Tool request payload
        headers: Kafka message headers
    """
    tool_call_id = message["tool_call_id"]
    tool_name = message["tool_name"]
    arguments = message.get("arguments", {})
    job_id = message["job_id"]
    tenant_id = message["tenant_id"]

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

        if not tool:
            result = f"Error: Unknown tool '{tool_name}'"
        else:
            # Execute tool
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
        result = f"Error: {str(e)}"

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

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    logger.info("Tool Workers started successfully")

    # Start consumer
    try:
        await consumer.start()
        await consumer.run()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down Tool Workers")
        await consumer.stop()
        logger.info("Tool Workers shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
