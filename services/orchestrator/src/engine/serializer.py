"""State serialization for persistence."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from libs.llm import LLMMessage, MessageRole, ToolCall
from .state import AgentState, AgentStatus


class StateSerializer:
    """Serializes and deserializes agent state for persistence."""

    @staticmethod
    def serialize(state: AgentState) -> dict[str, Any]:
        """Serialize AgentState to a dictionary.

        Args:
            state: Agent state to serialize

        Returns:
            Dictionary representation
        """
        return {
            "job_id": str(state.job_id),
            "tenant_id": str(state.tenant_id),
            "user_id": str(state.user_id) if state.user_id else None,
            "provider": state.provider,
            "model": state.model,
            "system_prompt": state.system_prompt,
            "tools": state.tools,
            "temperature": state.temperature,
            "max_tokens": state.max_tokens,
            "status": state.status.value,
            "messages": [
                StateSerializer._serialize_message(msg)
                for msg in state.messages
            ],
            "iteration": state.iteration,
            "pending_tool_calls": [
                StateSerializer._serialize_tool_call(tc)
                for tc in state.pending_tool_calls
            ],
            "total_input_tokens": state.total_input_tokens,
            "total_output_tokens": state.total_output_tokens,
            "started_at": state.started_at.isoformat() if state.started_at else None,
            "completed_at": state.completed_at.isoformat() if state.completed_at else None,
            "error": state.error,
            "error_details": state.error_details,
            "metadata": state.metadata,
        }

    @staticmethod
    def deserialize(data: dict[str, Any]) -> AgentState:
        """Deserialize a dictionary to AgentState.

        Args:
            data: Dictionary representation

        Returns:
            AgentState instance
        """
        return AgentState(
            job_id=UUID(data["job_id"]),
            tenant_id=UUID(data["tenant_id"]),
            user_id=UUID(data["user_id"]) if data.get("user_id") else None,
            provider=data["provider"],
            model=data["model"],
            system_prompt=data.get("system_prompt"),
            tools=data.get("tools"),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 4096),
            status=AgentStatus(data.get("status", "pending")),
            messages=[
                StateSerializer._deserialize_message(msg)
                for msg in data.get("messages", [])
            ],
            iteration=data.get("iteration", 0),
            pending_tool_calls=[
                StateSerializer._deserialize_tool_call(tc)
                for tc in data.get("pending_tool_calls", [])
            ],
            total_input_tokens=data.get("total_input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            started_at=(
                datetime.fromisoformat(data["started_at"])
                if data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at") else None
            ),
            error=data.get("error"),
            error_details=data.get("error_details"),
            metadata=data.get("metadata", {}),
        )

    @staticmethod
    def _serialize_message(message: LLMMessage) -> dict[str, Any]:
        """Serialize an LLMMessage."""
        return {
            "role": message.role.value,
            "content": message.content,
            "tool_calls": [
                StateSerializer._serialize_tool_call(tc)
                for tc in (message.tool_calls or [])
            ] if message.tool_calls else None,
            "tool_call_id": message.tool_call_id,
            "name": message.name,
        }

    @staticmethod
    def _deserialize_message(data: dict[str, Any]) -> LLMMessage:
        """Deserialize an LLMMessage."""
        return LLMMessage(
            role=MessageRole(data["role"]),
            content=data.get("content"),
            tool_calls=[
                StateSerializer._deserialize_tool_call(tc)
                for tc in (data.get("tool_calls") or [])
            ] if data.get("tool_calls") else None,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
        )

    @staticmethod
    def _serialize_tool_call(tool_call: ToolCall) -> dict[str, Any]:
        """Serialize a ToolCall."""
        return {
            "id": tool_call.id,
            "name": tool_call.name,
            "arguments": tool_call.arguments,
        }

    @staticmethod
    def _deserialize_tool_call(data: dict[str, Any]) -> ToolCall:
        """Deserialize a ToolCall."""
        return ToolCall(
            id=data.get("id", ""),
            name=data.get("name", "unknown"),
            arguments=data.get("arguments", {}),
        )

    @staticmethod
    def to_json(state: AgentState) -> str:
        """Serialize state to JSON string."""
        return json.dumps(StateSerializer.serialize(state))

    @staticmethod
    def from_json(json_str: str) -> AgentState:
        """Deserialize state from JSON string."""
        return StateSerializer.deserialize(json.loads(json_str))
