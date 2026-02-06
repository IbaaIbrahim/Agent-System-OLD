"""Agent state management."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from libs.llm import LLMMessage, ToolCall


class AgentStatus(str, Enum):
    """Agent execution status."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentState:
    """Holds the current state of an agent execution."""

    job_id: UUID
    tenant_id: UUID
    user_id: UUID | None

    # Configuration
    provider: str
    model: str
    system_prompt: str | None
    tools: list[dict[str, Any]] | None
    temperature: float
    max_tokens: int

    # Execution state
    status: AgentStatus = AgentStatus.PENDING
    messages: list[LLMMessage] = field(default_factory=list)
    iteration: int = 0
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    
    # Reasoning tracking
    reasoning_content: str | None = None

    # Token tracking
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    # Timestamps
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Error info
    error: str | None = None
    error_details: dict[str, Any] | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, message: LLMMessage) -> None:
        """Add a message to the conversation."""
        self.messages.append(message)

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        from libs.llm import MessageRole
        self.messages.append(LLMMessage(role=MessageRole.USER, content=content))

    def add_assistant_message(
        self,
        content: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        """Add an assistant message."""
        from libs.llm import MessageRole
        self.messages.append(LLMMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        ))

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """Add a tool result message."""
        from libs.llm import MessageRole
        self.messages.append(LLMMessage(
            role=MessageRole.TOOL,
            content=content,
            tool_call_id=tool_call_id,
        ))

    def mark_running(self) -> None:
        """Mark agent as running."""
        self.status = AgentStatus.RUNNING
        self.started_at = datetime.now(UTC)

    def mark_waiting_tool(self, tool_calls: list[ToolCall]) -> None:
        """Mark agent as waiting for tool results."""
        self.status = AgentStatus.WAITING_TOOL
        self.pending_tool_calls = tool_calls

    def mark_completed(self) -> None:
        """Mark agent as completed."""
        self.status = AgentStatus.COMPLETED
        self.completed_at = datetime.now(UTC)
        self.pending_tool_calls = []

    def mark_failed(self, error: str, details: dict[str, Any] | None = None) -> None:
        """Mark agent as failed."""
        self.status = AgentStatus.FAILED
        self.completed_at = datetime.now(UTC)
        self.error = error
        self.error_details = details
        self.pending_tool_calls = []

    def mark_cancelled(self) -> None:
        """Mark agent as cancelled."""
        self.status = AgentStatus.CANCELLED
        self.completed_at = datetime.now(UTC)
        self.pending_tool_calls = []

    def increment_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Update token counts."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens


class StateManager:
    """Manages agent state with caching."""

    def __init__(self) -> None:
        self._states: dict[UUID, AgentState] = {}

    def create_state(
        self,
        job_id: UUID,
        tenant_id: UUID,
        user_id: UUID | None,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        metadata: dict[str, Any] | None = None,
    ) -> AgentState:
        """Create a new agent state.

        Args:
            job_id: Job identifier
            tenant_id: Tenant identifier
            user_id: Optional user identifier
            provider: LLM provider name
            model: Model identifier
            messages: Initial conversation messages
            system_prompt: Optional system prompt
            tools: Optional tool definitions
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            metadata: Optional metadata

        Returns:
            New AgentState instance
        """
        from libs.llm import MessageRole

        # Convert message dicts to LLMMessage objects
        llm_messages = []
        for msg in messages:
            role = MessageRole(msg["role"])
            
            # Convert tool_calls dicts to ToolCall objects if present
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                tool_calls = [
                    ToolCall(
                        id=tc.get("id", ""),
                        name=tc.get("name", "unknown"),
                        arguments=tc.get("arguments", {}),
                    ) if isinstance(tc, dict) else tc
                    for tc in tool_calls
                ]
                
            llm_messages.append(LLMMessage(
                role=role,
                content=msg.get("content"),
                tool_calls=tool_calls,
                tool_call_id=msg.get("tool_call_id"),
            ))

        state = AgentState(
            job_id=job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=llm_messages,
            metadata=metadata or {},
        )

        self._states[job_id] = state
        return state

    def get_state(self, job_id: UUID) -> AgentState | None:
        """Get state by job ID."""
        return self._states.get(job_id)

    def remove_state(self, job_id: UUID) -> None:
        """Remove state from cache."""
        self._states.pop(job_id, None)

    def update_state(self, state: AgentState) -> None:
        """Update state in cache."""
        self._states[state.job_id] = state
