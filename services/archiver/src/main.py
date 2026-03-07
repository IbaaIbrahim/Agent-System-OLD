"""Archiver - Write-behind service for persisting events."""

import asyncio
import signal
import sys

from libs.common import get_logger, setup_logging
from libs.db import close_db, init_db
from libs.messaging.redis import get_redis_client

from .config import get_config
from .services.postgres_writer import PostgresWriter
from .services.redis_reader import RedisStreamReader

logger = get_logger(__name__)


async def main() -> None:
    """Main entry point for the archiver."""
    config = get_config()

    # Setup logging
    setup_logging(
        service_name="archiver",
        log_level=config.log_level,
        log_format=config.log_format,
    )

    logger.info("Starting Archiver")

    # Initialize connections
    await init_db()
    await get_redis_client()

    # Create components
    title_generator = None
    if config.title_generation_enabled:
        from .services.title_generator import TitleGenerator

        title_generator = TitleGenerator(provider_type=config.default_llm_provider)
        logger.info("Title generation enabled")

    writer = PostgresWriter(
        batch_size=config.batch_size,
        flush_interval=config.flush_interval,
        title_generator=title_generator,
    )
    reader = RedisStreamReader(
        writer=writer,
        consumer_group=config.consumer_group,
        consumer_name=config.consumer_name,
    )

    # Setup signal handlers (add_signal_handler not supported on Windows)
    shutdown_event = asyncio.Event()

    def signal_handler() -> None:
        shutdown_event.set()
        logger.info("Shutdown signal received")

    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)

    # Periodic cleanup task
    async def periodic_cleanup():
        """Clean up old Redis streams periodically."""
        while not shutdown_event.is_set():
            try:
                # Sleep for cleanup interval (default 1 hour)
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=config.cleanup_interval,
                )
                # If we get here, shutdown was triggered
                break
            except asyncio.TimeoutError:
                # Timeout means it's time to cleanup
                try:
                    await reader.cleanup_old_streams()
                    logger.info("Periodic stream cleanup completed")
                except Exception as e:
                    logger.error("Stream cleanup failed", error=str(e))

    logger.info("Archiver started successfully")

    # Start processing
    try:
        # Start writer's periodic flush
        await writer.start()

        # Start reader and cleanup concurrently
        reader_task = asyncio.create_task(reader.start())
        cleanup_task = asyncio.create_task(periodic_cleanup())

        # Wait for shutdown signal
        await shutdown_event.wait()

        # Graceful shutdown
        reader.stop()
        await reader_task

        # Cancel cleanup task
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

        # Flush remaining data
        await writer.stop()

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down Archiver")
        await close_db()
        logger.info("Archiver shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
