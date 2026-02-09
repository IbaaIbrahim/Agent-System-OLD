"""Common utilities shared across all services."""

from libs.common.auth import (
    StreamOTTPayload,
    TokenPayload,
    create_access_token,
    create_internal_transaction_token,
    create_stream_ott,
    decode_access_token,
    generate_partner_api_key,
    hash_api_key,
    verify_api_key,
    verify_internal_transaction_token,
    verify_stream_ott,
)
from libs.common.config import Settings, get_settings
from libs.common.exceptions import (
    AgentSystemError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from libs.common.logging import get_logger, setup_logging
from libs.common.tool_catalog import (
    TOOL_CATALOG,
    ToolBehavior,
    ToolMetadata,
    get_confirm_required_tools,
    get_tool_metadata,
    get_tools_for_plan,
    get_user_toggleable_tools,
)

__all__ = [
    # Config
    "Settings",
    "get_settings",
    # Logging
    "setup_logging",
    "get_logger",
    # Exceptions
    "AgentSystemError",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitError",
    "ValidationError",
    "NotFoundError",
    "ConflictError",
    "ExternalServiceError",
    # Auth
    "create_access_token",
    "decode_access_token",
    "hash_api_key",
    "verify_api_key",
    "TokenPayload",
    "create_internal_transaction_token",
    "verify_internal_transaction_token",
    "generate_partner_api_key",
    "create_stream_ott",
    "verify_stream_ott",
    "StreamOTTPayload",
    # Tool catalog
    "ToolBehavior",
    "ToolMetadata",
    "TOOL_CATALOG",
    "get_tool_metadata",
    "get_tools_for_plan",
    "get_user_toggleable_tools",
    "get_confirm_required_tools",
]
