"""Coding-task and task-step models."""

from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


CodingTaskHistoryStatus = Literal[
    "created",
    "running",
    "ready_to_apply",
    "cancelled",
    "failed",
    "applied",
    "applied_event_pending",
    "applied_without_events",
]
LaunchMode = Literal["new", "replay"]
CodingTaskRequestedCapability = Literal[
    "repo_observe",
    "multi_file",
    "mcp",
    "skills",
]


class EditableFileReference(BaseModel):
    """A single editable file selected for the task."""

    file_path: str
    document_version: int


class AppliedFileConfirmation(BaseModel):
    """Extension confirmation that a verified file was written locally."""

    file_path: str
    content_hash: str


class AppliedEventRecord(BaseModel):
    """Per-file event write status stored on the task record."""

    file_path: str
    event_id: Optional[str] = None
    status: Literal["pending", "written", "failed"] = "pending"
    last_error: Optional[str] = None


class VerifiedFileDraft(BaseModel):
    """A verified per-file draft returned before Apply."""

    file_path: str
    content: str
    content_hash: str
    original_content_hash: str
    original_document_version: Optional[int] = None


class CodingTaskCreateRequest(BaseModel):
    """Create a new coding task session."""

    target_file_path: str
    target_file_version: int
    user_prompt: str
    context_files: list[str] = Field(default_factory=list)
    editable_files: list[EditableFileReference] = Field(default_factory=list)
    launch_mode: LaunchMode = "new"
    source_task_id: Optional[str] = None
    selected_profile_id: Optional[str] = None
    requested_capabilities: list[CodingTaskRequestedCapability] = Field(
        default_factory=list
    )


class CodingTaskCreateResponse(BaseModel):
    """Response returned after creating a coding task session."""

    task_id: str
    status: Literal["created"] = "created"


class CodingTaskRecord(BaseModel):
    """Persistent task record used for history views."""

    task_id: str
    target_file_path: str
    user_prompt: str
    context_files: list[str] = Field(default_factory=list)
    editable_files: list[str] = Field(default_factory=list)
    status: CodingTaskHistoryStatus = "created"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    model_output_text: Optional[str] = None
    verified_draft_content: Optional[str] = None
    verified_files: list[VerifiedFileDraft] = Field(default_factory=list)
    intent: Optional[str] = None
    reasoning: Optional[str] = None
    last_error: Optional[str] = None
    applied_events: list[AppliedEventRecord] = Field(default_factory=list)
    launch_mode: LaunchMode = "new"
    source_task_id: Optional[str] = None
    selected_profile_id: Optional[str] = None
    requested_capabilities: list[CodingTaskRequestedCapability] = Field(
        default_factory=list
    )
    applied_event_retry_count: int = 0


class CodingTaskHistoryItem(BaseModel):
    """Compact task history list item."""

    task_id: str
    target_file_path: str
    user_prompt: str
    status: CodingTaskHistoryStatus
    created_at: datetime
    updated_at: datetime


class CodingTaskHistoryListResponse(BaseModel):
    """Paginated task history response."""

    items: list[CodingTaskHistoryItem] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
    has_more: bool


class CodingTaskHistoryTargetsResponse(BaseModel):
    """Deduped target path suggestions for history filters."""

    items: list[str] = Field(default_factory=list)


class CodingTaskEdit(BaseModel):
    """A local exact-match replacement used inside the backend loop."""

    file_path: str
    old_text: str
    new_text: str


class CodingTaskDraftResult(BaseModel):
    """Verified draft returned to the extension before Apply."""

    task_id: str
    verified_files: list[VerifiedFileDraft] = Field(default_factory=list)
    updated_file_content: Optional[str] = None
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


class CodingTaskAppliedRequest(BaseModel):
    """Apply confirmation request sent after local files were written."""

    applied_files: list[AppliedFileConfirmation] = Field(default_factory=list)


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


class CodingTaskHistoryDetail(BaseModel):
    """Expanded task history detail for the sidebar."""

    task_id: str
    target_file_path: str
    user_prompt: str
    context_files: list[str] = Field(default_factory=list)
    editable_files: list[str] = Field(default_factory=list)
    status: CodingTaskHistoryStatus
    created_at: datetime
    updated_at: datetime
    steps: list[TaskStepEvent] = Field(default_factory=list)
    model_output_text: Optional[str] = None
    verified_draft_content: Optional[str] = None
    verified_files: list[VerifiedFileDraft] = Field(default_factory=list)
    intent: Optional[str] = None
    reasoning: Optional[str] = None
    last_error: Optional[str] = None
    applied_events: list[AppliedEventRecord] = Field(default_factory=list)
    launch_mode: LaunchMode = "new"
    source_task_id: Optional[str] = None
    selected_profile_id: Optional[str] = None
    requested_capabilities: list[CodingTaskRequestedCapability] = Field(
        default_factory=list
    )


def new_task_id() -> str:
    return f"task_{uuid4().hex[:12]}"


def new_step_id() -> str:
    return f"step_{uuid4().hex[:12]}"


def new_call_id() -> str:
    return f"call_{uuid4().hex[:12]}"


__all__ = [
    "AppliedEventRecord",
    "AppliedFileConfirmation",
    "CodingTaskAppliedRequest",
    "CodingTaskCreateRequest",
    "CodingTaskCreateResponse",
    "CodingTaskDraftResult",
    "CodingTaskEdit",
    "CodingTaskHistoryDetail",
    "CodingTaskHistoryItem",
    "CodingTaskHistoryListResponse",
    "CodingTaskHistoryTargetsResponse",
    "CodingTaskHistoryStatus",
    "CodingTaskRequestedCapability",
    "CodingTaskRecord",
    "CodingTaskToolResultRequest",
    "EditableFileReference",
    "LaunchMode",
    "TaskStepEvent",
    "ToolCallPayload",
    "VerifiedFileDraft",
    "new_call_id",
    "new_step_id",
    "new_task_id",
]
