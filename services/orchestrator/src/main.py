"""Orchestrator - Job processing entry point."""

import asyncio
import signal
from typing import Any

from libs.common import setup_logging, get_logger
from libs.db import init_db, close_db
from libs.messaging.kafka import create_consumer
from libs.messaging.redis import get_redis_client

from .config import get_config
from .handlers.job_handler import JobHandler

logger = get_logger(__name__)

# Graceful shutdown flag
_shutdown = False


async def main() -> None:
    """Main entry point for the orchestrator."""
    config = get_config()

    # Setup logging
    setup_logging(
        service_name="orchestrator",
        log_level=config.log_level,
        log_format=config.log_format,
    )

    logger.info("Starting Orchestrator")

    # Initialize connections
    await init_db()
    await get_redis_client()

    # Create job handler
    job_handler = JobHandler()

    # Create Kafka consumer
    consumer = await create_consumer(
        topics=[config.jobs_topic],
        group_id=config.consumer_group,
        dlq_topic=config.jobs_dlq_topic,
    )

    # Register handler
    consumer.register_handler(config.jobs_topic, job_handler.handle_job)

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler():
        global _shutdown
        _shutdown = True
        logger.info("Shutdown signal received")

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    logger.info("Orchestrator started successfully")

    # Start consumer
    try:
        await consumer.start()
        await consumer.run()
    except asyncio.CancelledError:
        pass
    finally:
        # Cleanup
        logger.info("Shutting down Orchestrator")
        await consumer.stop()
        await close_db()
        logger.info("Orchestrator shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
