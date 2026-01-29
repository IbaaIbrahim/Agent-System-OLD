"""Custom exceptions for the Agent System."""

from typing import Any


class AgentSystemError(Exception):
    """Base exception for all Agent System errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for API responses."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class AuthenticationError(AgentSystemError):
    """Raised when authentication fails."""

    def __init__(
        self,
        message: str = "Authentication failed",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code="AUTHENTICATION_ERROR",
            status_code=401,
            details=details,
        )


class AuthorizationError(AgentSystemError):
    """Raised when authorization fails."""

    def __init__(
        self,
        message: str = "Access denied",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code="AUTHORIZATION_ERROR",
            status_code=403,
            details=details,
        )


class RateLimitError(AgentSystemError):
    """Raised when rate limit is exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if retry_after:
            details["retry_after"] = retry_after
        super().__init__(
            message=message,
            code="RATE_LIMIT_EXCEEDED",
            status_code=429,
            details=details,
        )
        self.retry_after = retry_after


class ValidationError(AgentSystemError):
    """Raised when request validation fails."""

    def __init__(
        self,
        message: str = "Validation failed",
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            status_code=422,
            details={"errors": errors or []},
        )


class NotFoundError(AgentSystemError):
    """Raised when a resource is not found."""

    def __init__(
        self,
        resource: str,
        identifier: str | None = None,
    ) -> None:
        message = f"{resource} not found"
        if identifier:
            message = f"{resource} with id '{identifier}' not found"
        super().__init__(
            message=message,
            code="NOT_FOUND",
            status_code=404,
            details={"resource": resource, "identifier": identifier},
        )


class ConflictError(AgentSystemError):
    """Raised when there's a conflict with existing data."""

    def __init__(
        self,
        message: str = "Resource conflict",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code="CONFLICT",
            status_code=409,
            details=details,
        )


class ExternalServiceError(AgentSystemError):
    """Raised when an external service call fails."""

    def __init__(
        self,
        service: str,
        message: str = "External service error",
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        details["service"] = service
        super().__init__(
            message=message,
            code="EXTERNAL_SERVICE_ERROR",
            status_code=502,
            details=details,
        )


class JobError(AgentSystemError):
    """Raised when job processing fails."""

    def __init__(
        self,
        job_id: str,
        message: str = "Job processing failed",
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        details["job_id"] = job_id
        super().__init__(
            message=message,
            code="JOB_ERROR",
            status_code=500,
            details=details,
        )


class LLMError(AgentSystemError):
    """Raised when LLM provider call fails."""

    def __init__(
        self,
        provider: str,
        message: str = "LLM provider error",
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        details["provider"] = provider
        super().__init__(
            message=message,
            code="LLM_ERROR",
            status_code=502,
            details=details,
        )
