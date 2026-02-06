"""Orchestrator - Job processing entry point."""

import asyncio
import signal
import sys

from libs.common import get_logger, setup_logging
from libs.db import close_db, init_db
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

    logger.info(
        "Starting Orchestrator",
        suspend_resume_enabled=config.enable_suspend_resume,
    )

    # Initialize connections
    await init_db()
    await get_redis_client()

    # Create shared services
    from .services.snapshot_service import SnapshotService
    from .services.llm_service import LLMService
    from .handlers.tool_handler import ToolHandler

    snapshot_service = SnapshotService()
    llm_service = LLMService()
    tool_handler = ToolHandler()

    # Create job handler
    job_handler = JobHandler()

    # Create job consumer
    job_consumer = await create_consumer(
        topics=[config.jobs_topic],
        group_id=config.consumer_group,
        dlq_topic=config.jobs_dlq_topic,
    )
    job_consumer.register_handler(config.jobs_topic, job_handler.handle_job)

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler():
        global _shutdown
        _shutdown = True
        logger.info("Shutdown signal received")

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)
    else:
        logger.info("Signal handlers skipped (not supported on Windows asyncio loop)")

    logger.info("Orchestrator started successfully")

    # Start consumers based on feature flag
    if config.enable_suspend_resume:
        # Create resume handler and consumer
        from .handlers.resume_handler import ResumeHandler

        resume_handler = ResumeHandler(
            snapshot_service=snapshot_service,
            llm_service=llm_service,
            tool_handler=tool_handler,
        )

        resume_consumer = await create_consumer(
            topics=[config.resume_topic],
            group_id=config.resume_consumer_group,
        )
        resume_consumer.register_handler(
            config.resume_topic,
            resume_handler.handle_resume,
        )

        logger.info(
            "Resume consumer configured",
            topic=config.resume_topic,
            group_id=config.resume_consumer_group,
        )

        # Run both consumers concurrently
        try:
            await job_consumer.start()
            await resume_consumer.start()

            await asyncio.gather(
                job_consumer.run(),
                resume_consumer.run(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Shutting down Orchestrator")
            await job_consumer.stop()
            await resume_consumer.stop()
            await close_db()
            logger.info("Orchestrator shutdown complete")
    else:
        # Single consumer mode (legacy blocking behavior)
        logger.info("Running in legacy blocking mode")
        try:
            await job_consumer.start()
            await job_consumer.run()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Shutting down Orchestrator")
            await job_consumer.stop()
            await close_db()
            logger.info("Orchestrator shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
