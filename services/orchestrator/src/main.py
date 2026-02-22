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
        "✅ Starting Orchestrator",
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
    shutdown_event = asyncio.Event()

    def signal_handler():
        shutdown_event.set()
        logger.info("⚠️ Shutdown signal received")

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)
    else:
        logger.info("⚠️ Signal handlers skipped (not supported on Windows asyncio loop)")

    logger.info("✅ Orchestrator started successfully")

    # Start consumers based on feature flag
    if config.enable_suspend_resume:
        # Create resume, confirm, and user response handlers
        from .handlers.resume_handler import ResumeHandler
        from .handlers.confirm_handler import ConfirmHandler
        from .handlers.user_response_handler import UserResponseHandler

        resume_handler = ResumeHandler(
            snapshot_service=snapshot_service,
            llm_service=llm_service,
            tool_handler=tool_handler,
        )

        confirm_handler = ConfirmHandler(
            snapshot_service=snapshot_service,
            llm_service=llm_service,
            tool_handler=tool_handler,
        )

        user_response_handler = UserResponseHandler(
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

        confirm_consumer = await create_consumer(
            topics=[config.confirm_topic],
            group_id=config.confirm_consumer_group,
        )
        confirm_consumer.register_handler(
            config.confirm_topic,
            confirm_handler.handle_confirmation,
        )

        logger.info(
            "Resume consumer configured",
            topic=config.resume_topic,
            group_id=config.resume_consumer_group,
        )

        logger.info(
            "Confirm consumer configured",
            topic=config.confirm_topic,
            group_id=config.confirm_consumer_group,
        )

        # Create user response consumer (human-in-the-loop)
        user_response_consumer = await create_consumer(
            topics=[config.user_response_topic],
            group_id=config.user_response_consumer_group,
        )
        user_response_consumer.register_handler(
            config.user_response_topic,
            user_response_handler.handle_user_response,
        )

        logger.info(
            "User response consumer configured",
            topic=config.user_response_topic,
            group_id=config.user_response_consumer_group,
        )

        # Run all consumers concurrently
        all_consumers = [job_consumer, resume_consumer, confirm_consumer, user_response_consumer]
        try:
            for consumer in all_consumers:
                await consumer.start()

            # Create tasks for consumers
            consumer_tasks = [asyncio.create_task(c.run()) for c in all_consumers]

            # Wait for shutdown signal or tasks to finish
            wait_for_shutdown = asyncio.create_task(shutdown_event.wait())

            done, pending = await asyncio.wait(
                [*consumer_tasks, wait_for_shutdown],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if wait_for_shutdown in done:
                logger.info("Shutdown signal received, stopping consumers...")
            else:
                logger.warning("One of the consumers stopped unexpectedly")

            # Shutdown signal received or consumer stopped, cancel tasks
            for task in [*consumer_tasks, wait_for_shutdown]:
                if not task.done():
                    task.cancel()

            # Wait for tasks to clean up
            await asyncio.gather(*consumer_tasks, return_exceptions=True)

        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Shutting down Orchestrator")
            for consumer in all_consumers:
                await consumer.stop()
            await close_db()
            logger.info("Orchestrator shutdown complete")
    else:
        # Single consumer mode (legacy blocking behavior)
        logger.info("Running in legacy blocking mode")
        try:
            await job_consumer.start()
            
            # Create task for consumer
            job_task = asyncio.create_task(job_consumer.run())
            
            # Wait for shutdown signal or task to finish
            wait_for_shutdown = asyncio.create_task(shutdown_event.wait())
            
            done, pending = await asyncio.wait(
                [job_task, wait_for_shutdown],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if wait_for_shutdown in done:
                logger.info("Shutdown signal received, stopping consumer...")
            
            # Cancel tasks
            for task in [job_task, wait_for_shutdown]:
                if not task.done():
                    task.cancel()
            
            await asyncio.gather(job_task, return_exceptions=True)

        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Shutting down Orchestrator")
            await job_consumer.stop()
            await close_db()
            logger.info("Orchestrator shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
