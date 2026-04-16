"""Coding-task and task-step models."""

from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class CodingTaskCreateRequest(BaseModel):
    """Create a new coding task session."""

    target_file_path: str
    target_file_version: int
    user_prompt: str
    context_files: list[str] = Field(default_factory=list)


class CodingTaskCreateResponse(BaseModel):
    """Response returned after creating a coding task session."""

    task_id: str
    status: Literal["created"] = "created"


class CodingTaskEdit(BaseModel):
    """A local exact-match replacement used inside the backend loop."""

    old_text: str
    new_text: str


class CodingTaskDraftResult(BaseModel):
    """Verified draft returned to the extension before Apply."""

    task_id: str
    updated_file_content: str
    intent: str
    reasoning: Optional[str] = None
    session_id: str
    agent_step_id: str
    action_type: Literal["modify"] = "modify"


class ToolCallPayload(BaseModel):
    """A backend request for a local extension tool execution."""

    task_id: str
    call_id: str
    step_id: str
    tool_name: Literal["view_file"]
    file_path: str
    intent: str


class CodingTaskToolResultRequest(BaseModel):
    """Tool result posted back to the backend by the extension."""

    call_id: str
    tool_name: Literal["view_file"]
    file_path: str
    document_version: Optional[int] = None
    content: Optional[str] = None
    content_hash: Optional[str] = None
    error: Optional[str] = None


class TaskStepEvent(BaseModel):
    """Persistent trace record for the coding-task workflow."""

    task_id: str
    step_id: str
    step_kind: Literal["view", "edit", "verify"]
    status: Literal["started", "succeeded", "failed"]
    file_path: str
    content_hash: Optional[str] = None
    intent: str
    reasoning_summary: Optional[str] = None
    tool_name: Optional[str] = None
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


def new_task_id() -> str:
    return f"task_{uuid4().hex[:12]}"


def new_step_id() -> str:
    return f"step_{uuid4().hex[:12]}"


def new_call_id() -> str:
    return f"call_{uuid4().hex[:12]}"


__all__ = [
    "CodingTaskCreateRequest",
    "CodingTaskCreateResponse",
    "CodingTaskDraftResult",
    "CodingTaskEdit",
    "CodingTaskToolResultRequest",
    "TaskStepEvent",
    "ToolCallPayload",
    "new_call_id",
    "new_step_id",
    "new_task_id",
]
