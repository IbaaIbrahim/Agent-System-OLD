"""Structured logging configuration."""

import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

import structlog
from structlog.types import EventDict, Processor


def _set_console_title(title: str) -> None:
    """Set the terminal/console window title (for VS Code debug terminals)."""
    try:
        if sys.platform == "win32":
            os.system(f'title {title}')
        else:
            # ANSI escape: set icon name and window title
            sys.stdout.write(f"\033]0;{title}\007")
            sys.stdout.flush()
    except Exception:
        pass


def add_timestamp(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Add ISO 8601 timestamp to log events."""
    event_dict["timestamp"] = datetime.now(UTC).isoformat()
    return event_dict


def add_service_info(service_name: str) -> Processor:
    """Create processor that adds service name to events."""

    def processor(
        logger: logging.Logger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        event_dict["service"] = service_name
        return event_dict

    return processor


def setup_logging(
    service_name: str,
    log_level: str = "INFO",
    log_format: str = "json",
) -> None:
    """Configure structured logging for a service.

    Args:
        service_name: Name of the service for log identification
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Output format ('json' or 'text')
    """
    # Set terminal tab title when launched from VS Code with TERMINAL_TITLE env
    terminal_title = os.environ.get("TERMINAL_TITLE")
    if terminal_title:
        _set_console_title(terminal_title)

    # Shared processors
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        add_timestamp,
        add_service_info(service_name),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if log_format == "json":
        # JSON format for production
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # Human-readable format for development
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Configured structlog logger
    """
    return structlog.get_logger(name)


class LogContext:
    """Context manager for adding temporary context to logs."""

    def __init__(self, **kwargs: Any) -> None:
        self.context = kwargs
        self._token: Any = None

    def __enter__(self) -> "LogContext":
        self._token = structlog.contextvars.bind_contextvars(**self.context)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._token:
            structlog.contextvars.unbind_contextvars(*self.context.keys())
