"""Archiver - Write-behind service for persisting events."""

import asyncio
import signal

from libs.common import setup_logging, get_logger
from libs.db import init_db, close_db
from libs.messaging.redis import get_redis_client

from .config import get_config
from .services.redis_reader import RedisStreamReader
from .services.postgres_writer import PostgresWriter

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
    writer = PostgresWriter(
        batch_size=config.batch_size,
        flush_interval=config.flush_interval,
    )
    reader = RedisStreamReader(
        writer=writer,
        consumer_group=config.consumer_group,
        consumer_name=config.consumer_name,
    )

    # Setup signal handlers
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def signal_handler():
        shutdown_event.set()
        logger.info("Shutdown signal received")

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    logger.info("Archiver started successfully")

    # Start processing
    try:
        reader_task = asyncio.create_task(reader.start())
        await shutdown_event.wait()

        # Graceful shutdown
        reader.stop()
        await reader_task

        # Flush remaining data
        await writer.flush()

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down Archiver")
        await close_db()
        logger.info("Archiver shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
