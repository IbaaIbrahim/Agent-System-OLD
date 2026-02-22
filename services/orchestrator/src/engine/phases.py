"""Multi-phase agent execution data structures.

Defines phases, sub-tasks, task plan items, and phase state
for the multi-phase agent execution framework.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentPhase(str, Enum):
    """Phases of multi-phase agent execution."""

    TRIAGE = "triage"
    DECOMPOSE = "decompose"
    EXECUTE = "execute"
    SYNTHESIZE = "synthesize"
    EVALUATE = "evaluate"
    RESPOND = "respond"
    WAITING_USER = "waiting_user"
    SIMPLE = "simple"


class SubTaskStrategy(str, Enum):
    """How a sub-task should be executed."""

    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"


class SubTaskStatus(str, Enum):
    """Execution status of a sub-task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(str, Enum):
    """Status of a high-level task in the todo list."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ADJUSTED = "adjusted"
    BLOCKED = "blocked"


@dataclass
class SubTask:
    """A decomposed unit of work within the execute phase."""

    id: str
    description: str
    strategy: SubTaskStrategy
    status: SubTaskStatus = SubTaskStatus.PENDING
    dependencies: list[str] = field(default_factory=list)

    # For TOOL_CALL strategy
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_call_id: str | None = None

    # For LLM_CALL strategy
    llm_prompt: str | None = None

    # Result
    result: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON storage in metadata."""
        return {
            "id": self.id,
            "description": self.description,
            "strategy": self.strategy.value,
            "status": self.status.value,
            "dependencies": self.dependencies,
            "tool_name": self.tool_name,
            "tool_arguments": self.tool_arguments,
            "tool_call_id": self.tool_call_id,
            "llm_prompt": self.llm_prompt,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubTask":
        """Deserialize from dict."""
        return cls(
            id=data["id"],
            description=data["description"],
            strategy=SubTaskStrategy(data["strategy"]),
            status=SubTaskStatus(data.get("status", "pending")),
            dependencies=data.get("dependencies", []),
            tool_name=data.get("tool_name"),
            tool_arguments=data.get("tool_arguments"),
            tool_call_id=data.get("tool_call_id"),
            llm_prompt=data.get("llm_prompt"),
            result=data.get("result"),
            error=data.get("error"),
        )


@dataclass
class TaskItem:
    """A high-level task in the agent's todo list. Visible to user via SSE."""

    id: str
    title: str
    status: TaskStatus = TaskStatus.PENDING
    phase: AgentPhase | None = None
    sub_task_ids: list[str] = field(default_factory=list)
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON storage and SSE events."""
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "phase": self.phase.value if self.phase else None,
            "sub_task_ids": self.sub_task_ids,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskItem":
        """Deserialize from dict."""
        return cls(
            id=data["id"],
            title=data["title"],
            status=TaskStatus(data.get("status", "pending")),
            phase=AgentPhase(data["phase"]) if data.get("phase") else None,
            sub_task_ids=data.get("sub_task_ids", []),
            notes=data.get("notes"),
        )


@dataclass
class PhaseState:
    """Tracks multi-phase execution progress.

    Stored in AgentState.metadata["phase_state"] and survives
    serialization through the existing snapshot mechanism.
    """

    current_phase: AgentPhase
    phase_history: list[dict[str, Any]] = field(default_factory=list)

    # Todo list
    task_plan: list[TaskItem] = field(default_factory=list)

    # Sub-tasks (detailed execution units)
    sub_tasks: list[SubTask] = field(default_factory=list)
    execution_order: list[list[str]] = field(default_factory=list)
    current_group_index: int = 0
    synthesis_guidance: str | None = None

    # Draft response from synthesis
    draft_response: str | None = None

    # Evaluation tracking
    evaluation_count: int = 0
    max_evaluations: int = 3

    # Human-in-the-loop
    pending_question: str | None = None
    question_context: str | None = None

    # Phase to resume to after WAITING_USER
    resume_phase: AgentPhase | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON storage in metadata."""
        return {
            "current_phase": self.current_phase.value,
            "phase_history": self.phase_history,
            "task_plan": [t.to_dict() for t in self.task_plan],
            "sub_tasks": [st.to_dict() for st in self.sub_tasks],
            "execution_order": self.execution_order,
            "current_group_index": self.current_group_index,
            "synthesis_guidance": self.synthesis_guidance,
            "draft_response": self.draft_response,
            "evaluation_count": self.evaluation_count,
            "max_evaluations": self.max_evaluations,
            "pending_question": self.pending_question,
            "question_context": self.question_context,
            "resume_phase": self.resume_phase.value if self.resume_phase else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PhaseState":
        """Deserialize from dict."""
        return cls(
            current_phase=AgentPhase(data["current_phase"]),
            phase_history=data.get("phase_history", []),
            task_plan=[TaskItem.from_dict(t) for t in data.get("task_plan", [])],
            sub_tasks=[SubTask.from_dict(st) for st in data.get("sub_tasks", [])],
            execution_order=data.get("execution_order", []),
            current_group_index=data.get("current_group_index", 0),
            synthesis_guidance=data.get("synthesis_guidance"),
            draft_response=data.get("draft_response"),
            evaluation_count=data.get("evaluation_count", 0),
            max_evaluations=data.get("max_evaluations", 3),
            pending_question=data.get("pending_question"),
            question_context=data.get("question_context"),
            resume_phase=(
                AgentPhase(data["resume_phase"])
                if data.get("resume_phase")
                else None
            ),
        )

    def get_sub_task(self, sub_task_id: str) -> SubTask | None:
        """Get a sub-task by ID."""
        for st in self.sub_tasks:
            if st.id == sub_task_id:
                return st
        return None

    def get_current_group_subtasks(self) -> list[SubTask]:
        """Get sub-tasks for the current execution group."""
        if not self.execution_order or self.current_group_index >= len(
            self.execution_order
        ):
            return []
        group_ids = self.execution_order[self.current_group_index]
        return [st for st in self.sub_tasks if st.id in group_ids]

    def all_groups_complete(self) -> bool:
        """Check if all execution groups have been processed."""
        return self.current_group_index >= len(self.execution_order)

    def current_group_complete(self) -> bool:
        """Check if all sub-tasks in the current group are complete."""
        for st in self.get_current_group_subtasks():
            if st.status not in (SubTaskStatus.COMPLETED, SubTaskStatus.FAILED,
                                 SubTaskStatus.SKIPPED):
                return False
        return True
