"""Runtime session models for coding-task execution."""

import asyncio
from dataclasses import dataclass, field
from typing import Any
from typing import Optional

from tailevents.coding.context.model import ObservedFileView
from tailevents.coding.runtime.events import RuntimeEventSink
from tailevents.models.protocols import LLMClientProtocol
from tailevents.models.task import (
    CodingTaskCreateRequest,
    CodingTaskDraftResult,
    CodingTaskRecord,
    ToolCallPayload,
)


@dataclass
class PendingToolRequest:
    """A pending local tool call waiting on the extension."""

    payload: ToolCallPayload
    future: asyncio.Future[Any]


@dataclass
class TaskRuntimeSession:
    """Pure in-memory runtime state for one coding task."""

    task_id: str
    request: CodingTaskCreateRequest
    record: CodingTaskRecord
    llm_client: LLMClientProtocol
    requested_lanes: set[str]
    editable_paths: set[str]
    readonly_paths: set[str]
    expected_versions: dict[str, Optional[int]]
    target_hint_path: Optional[str] = None
    observation_candidates: set[str] = field(default_factory=set)
    resolved_primary_target_path: Optional[str] = None
    resolved_target_files: list[str] = field(default_factory=list)
    resolved_editable_files: list[str] = field(default_factory=list)
    resolved_context_files: list[str] = field(default_factory=list)
    scope_summary: Optional[str] = None
    event_sink: RuntimeEventSink = field(default_factory=RuntimeEventSink)
    pending_tool: Optional[PendingToolRequest] = None
    worker: Optional[asyncio.Task] = None
    result: Optional[CodingTaskDraftResult] = None
    model_output_text: str = ""
    edit_attempts: int = 0
    done: bool = False
    cancelled: bool = False

    def next_edit_attempt(self) -> int:
        self.edit_attempts += 1
        return self.edit_attempts


__all__ = ["PendingToolRequest", "TaskRuntimeSession"]
