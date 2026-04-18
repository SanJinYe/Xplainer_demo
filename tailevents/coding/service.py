"""Backend orchestration for the Phase 4 coding task loop."""

import ast
import asyncio
from datetime import datetime
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from pydantic import BaseModel

from tailevents.coding.exceptions import (
    CodingTaskCancelledError,
    CodingTaskConflictError,
    CodingTaskNotFoundError,
    CodingTaskValidationError,
)
from tailevents.models.event import RawEvent
from tailevents.models.protocols import (
    CodingProfileRegistryProtocol,
    CodingTaskStoreProtocol,
    IngestionPipelineProtocol,
    LLMClientProtocol,
    TaskStepStoreProtocol,
)
from tailevents.models.task import (
    AppliedEventRecord,
    CodingTaskAppliedRequest,
    CodingTaskCreateRequest,
    CodingTaskCreateResponse,
    CodingTaskDraftResult,
    CodingTaskEdit,
    CodingTaskHistoryDetail,
    CodingTaskHistoryItem,
    CodingTaskHistoryListResponse,
    CodingTaskHistoryTargetsResponse,
    CodingTaskRecord,
    CodingTaskToolResultRequest,
    EditableFileReference,
    TaskStepEvent,
    ToolCallPayload,
    VerifiedFileDraft,
    new_call_id,
    new_step_id,
    new_task_id,
)


SYSTEM_PROMPT = """
You are a coding agent for one or two editable project files.

You already have the exact observed contents of:
- one primary editable target file
- zero or one additional editable files
- zero to three read-only context files

You must return exactly one JSON object and nothing else.

The JSON object must contain exactly:
- edits
- intent
- reasoning

Rules:
- edits must be an array of exact-match replacements.
- Each edit must contain exactly file_path, old_text, and new_text.
- file_path must refer to one of the explicitly editable files.
- old_text must match exactly once inside the referenced editable file.
- Do not modify context files.
- Keep edits as small and local as possible.
- Preserve indentation, spacing, and blank lines.
- Every changed Python file must remain valid Python.
""".strip()

USER_PROMPT_TEMPLATE = """
Task goal:
{user_prompt}

Primary target file:
{target_file_path}

Editable files:
{editable_block}

Readonly context files:
{context_block}

Previous failure to fix:
{failure_hint}
""".strip()

CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
MAX_CONTEXT_FILES = 3
MAX_TOTAL_EDITABLE_FILES = 2
MAX_RETRIES = 1
MAX_SUMMARY_LENGTH = 240
MAX_EVENT_WRITE_RETRIES = 3


class _EditPlan(BaseModel):
    edits: list[CodingTaskEdit]
    intent: str
    reasoning: Optional[str] = None


@dataclass
class _ObservedFile:
    file_path: str
    content: str
    content_hash: str
    document_version: Optional[int]


@dataclass
class _PendingToolRequest:
    payload: ToolCallPayload
    future: asyncio.Future[_ObservedFile]


@dataclass
class _StreamEvent:
    event: str
    data: dict[str, object]


@dataclass
class _TaskSession:
    task_id: str
    request: CodingTaskCreateRequest
    record: CodingTaskRecord
    llm_client: LLMClientProtocol
    editable_paths: set[str]
    readonly_paths: set[str]
    allowed_files: set[str]
    expected_versions: dict[str, int]
    events: list[_StreamEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    pending_tool: Optional[_PendingToolRequest] = None
    worker: Optional[asyncio.Task] = None
    result: Optional[CodingTaskDraftResult] = None
    model_output_text: str = ""
    edit_attempts: int = 0
    done: bool = False
    cancelled: bool = False


class CodingTaskService:
    """Coordinate the backend-driven coding task loop."""

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        task_store: CodingTaskStoreProtocol,
        step_store: TaskStepStoreProtocol,
        ingestion_pipeline: Optional[IngestionPipelineProtocol] = None,
        profile_registry: Optional[CodingProfileRegistryProtocol] = None,
    ):
        self._llm_client = llm_client
        self._task_store = task_store
        self._step_store = step_store
        self._ingestion_pipeline = ingestion_pipeline
        self._profile_registry = profile_registry
        self._sessions: dict[str, _TaskSession] = {}

    async def create_task(
        self,
        request: CodingTaskCreateRequest,
    ) -> CodingTaskCreateResponse:
        self._validate_request(request)
        task_id = new_task_id()
        llm_client = self._resolve_llm_client(request.selected_profile_id)
        record = CodingTaskRecord(
            task_id=task_id,
            target_file_path=request.target_file_path,
            user_prompt=request.user_prompt,
            context_files=list(request.context_files),
            editable_files=[item.file_path for item in request.editable_files],
            status="created",
            launch_mode=request.launch_mode,
            source_task_id=request.source_task_id,
            selected_profile_id=request.selected_profile_id,
            requested_capabilities=list(request.requested_capabilities),
        )
        await self._task_store.put(record)

        editable_paths = {
            request.target_file_path,
            *[item.file_path for item in request.editable_files],
        }
        readonly_paths = set(request.context_files)
        expected_versions = self._build_expected_versions(request)
        session = _TaskSession(
            task_id=task_id,
            request=request,
            record=record,
            llm_client=llm_client,
            editable_paths=editable_paths,
            readonly_paths=readonly_paths,
            allowed_files=editable_paths | readonly_paths,
            expected_versions=expected_versions,
        )
        self._sessions[task_id] = session
        session.worker = asyncio.create_task(self._run_task(session))
        return CodingTaskCreateResponse(task_id=task_id)

    async def stream_events(
        self,
        task_id: str,
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        session = self._require_session(task_id)
        index = 0

        while True:
            while index < len(session.events):
                item = session.events[index]
                index += 1
                yield (item.event, item.data)

            if session.done:
                break

            async with session.condition:
                if index >= len(session.events) and not session.done:
                    await session.condition.wait()

    async def submit_tool_result(
        self,
        task_id: str,
        result: CodingTaskToolResultRequest,
    ) -> None:
        session = self._require_session(task_id)
        pending = session.pending_tool
        if pending is None:
            raise CodingTaskConflictError("Task is not waiting for a tool result")
        if pending.payload.call_id != result.call_id:
            raise CodingTaskConflictError("Tool result call_id does not match the pending request")
        if pending.payload.tool_name != result.tool_name:
            raise CodingTaskConflictError("Tool result tool_name does not match the pending request")
        if result.file_path not in session.allowed_files:
            raise CodingTaskValidationError("Tool result file_path is outside the allowed set")

        if result.error:
            pending.future.set_exception(CodingTaskValidationError(result.error))
        else:
            if result.content is None or result.content_hash is None:
                raise CodingTaskValidationError("Tool result must include content and content_hash")
            pending.future.set_result(
                _ObservedFile(
                    file_path=result.file_path,
                    content=result.content,
                    content_hash=result.content_hash,
                    document_version=result.document_version,
                )
            )
        session.pending_tool = None

    async def cancel_task(self, task_id: str) -> None:
        session = self._require_session(task_id)
        if session.done or session.cancelled:
            return
        session.cancelled = True
        if session.pending_tool and not session.pending_tool.future.done():
            session.pending_tool.future.set_exception(
                CodingTaskCancelledError("Task cancelled")
            )
            session.pending_tool = None
        if session.worker is not None:
            session.worker.cancel()

    async def get_result(self, task_id: str) -> Optional[CodingTaskDraftResult]:
        session = self._require_session(task_id)
        return session.result

    async def list_history(
        self,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        target_file_path: Optional[str] = None,
    ) -> CodingTaskHistoryListResponse:
        records, total = await self._task_store.list_recent(
            limit=limit,
            offset=offset,
            status=status,
            target_file_path=target_file_path,
        )
        items = [
            CodingTaskHistoryItem(
                task_id=record.task_id,
                target_file_path=record.target_file_path,
                user_prompt=record.user_prompt,
                status=record.status,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in records
        ]
        return CodingTaskHistoryListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )

    async def list_history_target_paths(
        self,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> CodingTaskHistoryTargetsResponse:
        items = await self._task_store.list_recent_target_paths(
            query=query,
            limit=limit,
        )
        return CodingTaskHistoryTargetsResponse(items=items)

    async def get_history_detail(self, task_id: str) -> CodingTaskHistoryDetail:
        record = await self._require_task_record(task_id)
        steps = await self._step_store.get_by_task(task_id)
        return CodingTaskHistoryDetail(
            task_id=record.task_id,
            target_file_path=record.target_file_path,
            user_prompt=record.user_prompt,
            context_files=record.context_files,
            editable_files=record.editable_files,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            steps=steps,
            model_output_text=record.model_output_text,
            verified_draft_content=record.verified_draft_content,
            verified_files=record.verified_files,
            intent=record.intent,
            reasoning=record.reasoning,
            last_error=record.last_error,
            applied_events=record.applied_events,
            launch_mode=record.launch_mode,
            source_task_id=record.source_task_id,
            selected_profile_id=record.selected_profile_id,
            requested_capabilities=record.requested_capabilities,
        )

    async def mark_applied(
        self,
        task_id: str,
        request: CodingTaskAppliedRequest,
    ) -> None:
        record = await self._require_task_record(task_id)
        verified_files = list(record.verified_files)
        if not verified_files:
            raise CodingTaskValidationError("Task does not have verified files to apply")
        self._validate_applied_files(record, request)

        applied_events = self._normalize_applied_events(record)
        event_step_id = await self._resolve_event_step_id(task_id)
        updated_events = await self._write_missing_events(
            record=record,
            verified_files=verified_files,
            applied_events=applied_events,
            event_step_id=event_step_id,
        )

        unresolved = [item for item in updated_events if item.status != "written"]
        updated_status = "applied" if not unresolved else "applied_event_pending"
        updated = record.model_copy(
            update={
                "status": updated_status,
                "applied_events": updated_events,
                "last_error": self._first_failed_error(updated_events),
                "updated_at": datetime.utcnow(),
            }
        )
        await self._task_store.put(updated)
        self._replace_session_record(task_id, updated)

    async def retry_event_writes(self, task_id: str) -> None:
        record = await self._require_task_record(task_id)
        verified_files = list(record.verified_files)
        if not verified_files:
            raise CodingTaskValidationError("Task does not have verified files to retry")

        applied_events = self._normalize_applied_events(record)
        if not any(item.status != "written" for item in applied_events):
            return

        event_step_id = await self._resolve_event_step_id(task_id)
        updated_events = await self._write_missing_events(
            record=record,
            verified_files=verified_files,
            applied_events=applied_events,
            event_step_id=event_step_id,
        )

        unresolved = [item for item in updated_events if item.status != "written"]
        retry_count = record.applied_event_retry_count
        status = "applied"
        if unresolved:
            retry_count += 1
            status = (
                "applied_without_events"
                if retry_count >= MAX_EVENT_WRITE_RETRIES
                else "applied_event_pending"
            )

        updated = record.model_copy(
            update={
                "status": status,
                "applied_events": updated_events,
                "applied_event_retry_count": retry_count,
                "last_error": self._first_failed_error(updated_events),
                "updated_at": datetime.utcnow(),
            }
        )
        await self._task_store.put(updated)
        self._replace_session_record(task_id, updated)

    async def reset_all_sessions(self) -> int:
        sessions = list(self._sessions.values())
        self._sessions = {}

        cancelled = 0
        pending_workers: list[asyncio.Task] = []
        for session in sessions:
            if session.done or session.cancelled:
                continue
            session.cancelled = True
            if session.pending_tool and not session.pending_tool.future.done():
                session.pending_tool.future.set_exception(
                    CodingTaskCancelledError("Task cancelled by admin reset")
                )
                session.pending_tool = None
            if session.worker is not None:
                session.worker.cancel()
                pending_workers.append(session.worker)
            cancelled += 1
        if pending_workers:
            await asyncio.gather(*pending_workers, return_exceptions=True)
        return cancelled

    async def _run_task(self, session: _TaskSession) -> None:
        try:
            await self._update_task_record(
                session,
                status="running",
                last_error=None,
            )
            await self._emit(session, "status", {"status": "running"})

            editable_views = await self._observe_initial_editable_files(session)
            context_views = await self._observe_context_files(session)

            initial_hashes = {
                file_path: observed.content_hash
                for file_path, observed in editable_views.items()
            }
            failure_hint: Optional[str] = None
            attempt = 0

            while attempt <= MAX_RETRIES:
                try:
                    plan, draft_contents, edit_step_id = await self._run_edit_step(
                        session,
                        editable_views=editable_views,
                        context_views=context_views,
                        failure_hint=failure_hint,
                    )
                    verify_step_id, verified_files = await self._run_verify_step(
                        session,
                        editable_views=editable_views,
                        draft_contents=draft_contents,
                    )
                    primary_draft = next(
                        (
                            item.content
                            for item in verified_files
                            if item.file_path == session.request.target_file_path
                        ),
                        None,
                    )
                    session.result = CodingTaskDraftResult(
                        task_id=session.task_id,
                        verified_files=verified_files,
                        updated_file_content=primary_draft,
                        intent=plan.intent,
                        reasoning=plan.reasoning,
                        session_id=session.task_id,
                        agent_step_id=verify_step_id or edit_step_id,
                    )
                    await self._update_task_record(
                        session,
                        status="ready_to_apply",
                        verified_draft_content=None,
                        verified_files=verified_files,
                        applied_events=self._build_pending_applied_events(verified_files),
                        intent=plan.intent,
                        reasoning=plan.reasoning,
                        last_error=None,
                    )
                    await self._emit(session, "result", session.result.model_dump(mode="json"))
                    await self._emit(session, "status", {"status": "ready_to_apply"})
                    break
                except CodingTaskCancelledError:
                    raise
                except Exception as error:
                    failure_hint = str(error)
                    if attempt >= MAX_RETRIES:
                        raise
                    attempt += 1
                    editable_views = await self._reobserve_editable_files(session, initial_hashes)
                    context_views = await self._observe_context_files(session)
        except CodingTaskCancelledError:
            await self._update_task_record_if_available(
                session,
                status="cancelled",
                last_error="Task cancelled",
            )
            await self._emit(session, "status", {"status": "cancelled"})
        except asyncio.CancelledError:
            await self._update_task_record_if_available(
                session,
                status="cancelled",
                last_error="Task cancelled",
            )
            await self._emit(session, "status", {"status": "cancelled"})
        except Exception as error:
            await self._update_task_record(
                session,
                status="failed",
                last_error=str(error),
            )
            await self._emit(session, "error", {"message": str(error)})
            await self._emit(session, "status", {"status": "failed"})
        finally:
            await self._emit(session, "done", {})
            session.done = True
            async with session.condition:
                session.condition.notify_all()

    async def _observe_initial_editable_files(
        self,
        session: _TaskSession,
    ) -> dict[str, _ObservedFile]:
        observed: dict[str, _ObservedFile] = {}
        observed[session.request.target_file_path] = await self._request_view(
            session,
            file_path=session.request.target_file_path,
            intent="Observe the primary target file before editing",
        )
        self._validate_expected_version(
            file_path=session.request.target_file_path,
            observed=observed[session.request.target_file_path],
            expected_version=session.request.target_file_version,
        )

        for editable in session.request.editable_files:
            observed[editable.file_path] = await self._request_view(
                session,
                file_path=editable.file_path,
                intent=f"Observe additional editable file {editable.file_path}",
            )
            self._validate_expected_version(
                file_path=editable.file_path,
                observed=observed[editable.file_path],
                expected_version=editable.document_version,
            )

        return observed

    async def _observe_context_files(
        self,
        session: _TaskSession,
    ) -> list[_ObservedFile]:
        observed: list[_ObservedFile] = []
        for context_file in session.request.context_files:
            observed.append(
                await self._request_view(
                    session,
                    file_path=context_file,
                    intent=f"Observe readonly context file {context_file}",
                )
            )
        return observed

    async def _reobserve_editable_files(
        self,
        session: _TaskSession,
        initial_hashes: dict[str, str],
    ) -> dict[str, _ObservedFile]:
        refreshed = await self._observe_initial_editable_files(session)
        for file_path, observed in refreshed.items():
            if observed.content_hash != initial_hashes[file_path]:
                raise CodingTaskValidationError(
                    f"Editable file content drifted during task execution: {file_path}"
                )
        return refreshed

    async def _run_edit_step(
        self,
        session: _TaskSession,
        editable_views: dict[str, _ObservedFile],
        context_views: list[_ObservedFile],
        failure_hint: Optional[str],
    ) -> tuple[_EditPlan, dict[str, str], str]:
        step_id = new_step_id()
        attempt_number = self._next_edit_attempt(session)
        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="edit",
                status="started",
                file_path=session.request.target_file_path,
                content_hash=editable_views[session.request.target_file_path].content_hash,
                intent="Generate local edits for the declared editable files",
                input_summary=self._truncate(
                    f"editable={len(editable_views)}, contexts={len(context_views)}"
                ),
            ),
        )

        raw_output = ""
        captured_output = False
        try:
            async for chunk in session.llm_client.stream_generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=self._build_user_prompt(
                    session.request,
                    editable_views=editable_views,
                    context_views=context_views,
                    failure_hint=failure_hint,
                ),
                max_tokens=4000,
                temperature=0.1,
            ):
                if not chunk:
                    continue
                raw_output += chunk
                await self._emit(session, "model_delta", {"text": chunk})
            await self._capture_model_output(session, attempt_number, raw_output)
            captured_output = True

            default_file_path = None
            if len(editable_views) == 1:
                default_file_path = next(iter(editable_views))
            plan = self._parse_edit_plan(raw_output, default_file_path=default_file_path)
            draft_contents = self._apply_edits(
                editable_views=editable_views,
                editable_paths=session.editable_paths,
                edits=plan.edits,
            )
        except Exception as error:
            if not captured_output:
                await self._capture_model_output(session, attempt_number, raw_output)
            await self._record_step(
                session,
                TaskStepEvent(
                    task_id=session.task_id,
                    step_id=step_id,
                    step_kind="edit",
                    status="failed",
                    file_path=session.request.target_file_path,
                    content_hash=editable_views[session.request.target_file_path].content_hash,
                    intent="Generate local edits for the declared editable files",
                    reasoning_summary=self._truncate(str(error)),
                    input_summary=self._truncate(
                        f"editable={len(editable_views)}, contexts={len(context_views)}"
                    ),
                    output_summary=self._truncate(str(error)),
                ),
            )
            raise

        changed_count = len(draft_contents)
        edit_count = len(plan.edits)
        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="edit",
                status="succeeded",
                file_path=session.request.target_file_path,
                content_hash=self._hash_content(json.dumps(draft_contents, sort_keys=True)),
                intent=plan.intent,
                reasoning_summary=self._truncate(plan.reasoning),
                input_summary=self._truncate(
                    f"editable={len(editable_views)}, contexts={len(context_views)}"
                ),
                output_summary=self._truncate(
                    f"generated {edit_count} edit(s) across {changed_count} file(s)"
                ),
            ),
        )
        return (plan, draft_contents, step_id)

    async def _run_verify_step(
        self,
        session: _TaskSession,
        editable_views: dict[str, _ObservedFile],
        draft_contents: dict[str, str],
    ) -> tuple[str, list[VerifiedFileDraft]]:
        step_id = new_step_id()
        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="verify",
                status="started",
                file_path=session.request.target_file_path,
                content_hash=self._hash_content(json.dumps(draft_contents, sort_keys=True)),
                intent="Verify the task draft before Apply",
                input_summary=self._truncate(
                    f"task-level drift + python syntax across {len(draft_contents)} file(s)"
                ),
            ),
        )

        try:
            for file_path, draft_content in draft_contents.items():
                latest_view = await self._request_view(
                    session,
                    file_path=file_path,
                    intent=f"Re-observe editable file {file_path} before Apply verification",
                )
                original_view = editable_views[file_path]
                self._validate_expected_version(
                    file_path=file_path,
                    observed=latest_view,
                    expected_version=session.expected_versions[file_path],
                )
                if latest_view.content_hash != original_view.content_hash:
                    raise CodingTaskValidationError(
                        f"Editable file content drifted during task execution: {file_path}"
                    )
                self._validate_python_source(file_path, draft_content)
        except Exception as error:
            await self._record_step(
                session,
                TaskStepEvent(
                    task_id=session.task_id,
                    step_id=step_id,
                    step_kind="verify",
                    status="failed",
                    file_path=session.request.target_file_path,
                    content_hash=self._hash_content(json.dumps(draft_contents, sort_keys=True)),
                    intent="Verify the task draft before Apply",
                    reasoning_summary=self._truncate(str(error)),
                    input_summary=self._truncate(
                        f"task-level drift + python syntax across {len(draft_contents)} file(s)"
                    ),
                    output_summary=self._truncate(str(error)),
                ),
            )
            raise

        verified_files: list[VerifiedFileDraft] = []
        for file_path in self._ordered_editable_paths(session.request):
            if file_path not in draft_contents:
                continue
            original_view = editable_views[file_path]
            verified_files.append(
                VerifiedFileDraft(
                    file_path=file_path,
                    content=draft_contents[file_path],
                    content_hash=self._hash_content(draft_contents[file_path]),
                    original_content_hash=original_view.content_hash,
                    original_document_version=original_view.document_version,
                )
            )

        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="verify",
                status="succeeded",
                file_path=session.request.target_file_path,
                content_hash=self._hash_content(json.dumps(draft_contents, sort_keys=True)),
                intent="Verify the task draft before Apply",
                reasoning_summary=self._truncate(
                    "All changed files passed drift checks and Python syntax validation."
                ),
                input_summary=self._truncate(
                    f"task-level drift + python syntax across {len(draft_contents)} file(s)"
                ),
                output_summary=self._truncate(
                    f"verified {len(verified_files)} file(s) ready for Apply"
                ),
            ),
        )
        return (step_id, verified_files)

    async def _request_view(
        self,
        session: _TaskSession,
        file_path: str,
        intent: str,
    ) -> _ObservedFile:
        step_id = new_step_id()
        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="view",
                status="started",
                file_path=file_path,
                intent=intent,
                tool_name="view_file",
                input_summary=self._truncate(f"request file view for {file_path}"),
            ),
        )

        payload = ToolCallPayload(
            task_id=session.task_id,
            call_id=new_call_id(),
            step_id=step_id,
            tool_name="view_file",
            file_path=file_path,
            intent=intent,
        )
        future: asyncio.Future[_ObservedFile] = asyncio.get_running_loop().create_future()
        session.pending_tool = _PendingToolRequest(payload=payload, future=future)
        await self._emit(session, "tool_call", payload.model_dump(mode="json"))

        try:
            observed = await future
        except Exception as error:
            await self._record_step(
                session,
                TaskStepEvent(
                    task_id=session.task_id,
                    step_id=step_id,
                    step_kind="view",
                    status="failed",
                    file_path=file_path,
                    intent=intent,
                    tool_name="view_file",
                    reasoning_summary=self._truncate(str(error)),
                    input_summary=self._truncate(f"request file view for {file_path}"),
                    output_summary=self._truncate("view failed"),
                ),
            )
            raise

        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="view",
                status="succeeded",
                file_path=file_path,
                content_hash=observed.content_hash,
                intent=intent,
                tool_name="view_file",
                input_summary=self._truncate(f"request file view for {file_path}"),
                output_summary=self._truncate(
                    f"version={observed.document_version}, chars={len(observed.content)}"
                ),
            ),
        )
        return observed

    async def _record_step(self, session: _TaskSession, event: TaskStepEvent) -> None:
        await self._step_store.put(event)
        await self._emit(session, "step", event.model_dump(mode="json"))

    async def _update_task_record(self, session: _TaskSession, **changes: object) -> None:
        session.record = session.record.model_copy(
            update={
                **changes,
                "updated_at": datetime.utcnow(),
            }
        )
        await self._task_store.put(session.record)

    async def _update_task_record_if_available(
        self,
        session: _TaskSession,
        **changes: object,
    ) -> None:
        try:
            await self._update_task_record(session, **changes)
        except sqlite3.ProgrammingError as error:
            if "closed database" not in str(error).lower():
                raise

    async def _capture_model_output(
        self,
        session: _TaskSession,
        attempt_number: int,
        raw_output: str,
    ) -> None:
        attempt_block = f"--- attempt {attempt_number} ---\n{raw_output}".rstrip()
        if session.model_output_text:
            session.model_output_text = f"{session.model_output_text}\n{attempt_block}"
        else:
            session.model_output_text = attempt_block
        await self._update_task_record(
            session,
            model_output_text=session.model_output_text,
        )

    async def _emit(
        self,
        session: _TaskSession,
        event: str,
        data: dict[str, object],
    ) -> None:
        async with session.condition:
            session.events.append(_StreamEvent(event=event, data=data))
            session.condition.notify_all()

    def _build_user_prompt(
        self,
        request: CodingTaskCreateRequest,
        editable_views: dict[str, _ObservedFile],
        context_views: list[_ObservedFile],
        failure_hint: Optional[str],
    ) -> str:
        editable_block = "\n\n".join(
            [
                (
                    f"<editable_file path=\"{view.file_path}\">\n"
                    f"{view.content}\n"
                    f"</editable_file>"
                )
                for view in editable_views.values()
            ]
        )
        if context_views:
            context_block = "\n\n".join(
                [
                    (
                        f"<context_file path=\"{item.file_path}\">\n"
                        f"{item.content}\n"
                        f"</context_file>"
                    )
                    for item in context_views
                ]
            )
        else:
            context_block = "<none />"

        return USER_PROMPT_TEMPLATE.format(
            user_prompt=request.user_prompt,
            target_file_path=request.target_file_path,
            editable_block=editable_block,
            context_block=context_block,
            failure_hint=failure_hint or "None",
        )

    def _parse_edit_plan(
        self,
        raw_output: str,
        default_file_path: Optional[str] = None,
    ) -> _EditPlan:
        normalized = raw_output.strip()
        if not normalized:
            raise CodingTaskValidationError("Model returned an empty response")

        stripped = CODE_FENCE_PATTERN.sub("", normalized).strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise CodingTaskValidationError("Model output did not contain a JSON object")

        payload = stripped[start : end + 1]
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as error:
            raise CodingTaskValidationError(
                f"Model output was not valid JSON: {error.msg}"
            ) from error

        if default_file_path and isinstance(parsed, dict):
            edits = parsed.get("edits")
            if isinstance(edits, list):
                for item in edits:
                    if isinstance(item, dict) and "file_path" not in item:
                        item["file_path"] = default_file_path

        try:
            plan = _EditPlan.model_validate(parsed)
        except Exception as error:
            raise CodingTaskValidationError(
                f"Model output failed validation: {error}"
            ) from error

        if not plan.edits:
            raise CodingTaskValidationError("Model returned no edits")
        if not plan.intent.strip():
            raise CodingTaskValidationError("Model returned an empty intent")
        return plan

    def _apply_edits(
        self,
        editable_views: dict[str, _ObservedFile],
        editable_paths: set[str],
        edits: list[CodingTaskEdit],
    ) -> dict[str, str]:
        working_content = {
            file_path: observed.content
            for file_path, observed in editable_views.items()
        }

        for index, edit in enumerate(edits, start=1):
            if edit.file_path not in editable_paths:
                raise CodingTaskValidationError(
                    f"Edit {index} targeted a file outside the editable set: {edit.file_path}"
                )
            if not edit.old_text:
                raise CodingTaskValidationError(f"Edit {index} old_text must not be empty")

            current_content = working_content[edit.file_path]
            matches = current_content.count(edit.old_text)
            if matches == 0:
                raise CodingTaskValidationError(
                    f"Edit {index} old_text did not match the observed file: {edit.file_path}"
                )
            if matches > 1:
                raise CodingTaskValidationError(
                    f"Edit {index} old_text matched multiple locations in {edit.file_path}"
                )

            working_content[edit.file_path] = current_content.replace(
                edit.old_text,
                edit.new_text,
                1,
            )

        changed = {
            file_path: content
            for file_path, content in working_content.items()
            if content != editable_views[file_path].content
        }
        if not changed:
            raise CodingTaskValidationError("Edit plan did not change any editable file")
        return changed

    def _validate_request(self, request: CodingTaskCreateRequest) -> None:
        if not request.target_file_path.strip():
            raise CodingTaskValidationError("target_file_path must not be empty")
        if request.target_file_version < 1:
            raise CodingTaskValidationError("target_file_version must be >= 1")
        if not request.user_prompt.strip():
            raise CodingTaskValidationError("user_prompt must not be empty")
        if len(request.context_files) > MAX_CONTEXT_FILES:
            raise CodingTaskValidationError("context_files must contain at most 3 items")
        if len(request.editable_files) + 1 > MAX_TOTAL_EDITABLE_FILES:
            raise CodingTaskValidationError("editable files must contain at most 2 files total")
        if request.launch_mode == "replay" and not request.source_task_id:
            raise CodingTaskValidationError("source_task_id is required for replay tasks")
        if request.launch_mode == "new" and request.source_task_id:
            raise CodingTaskValidationError("source_task_id is only valid for replay tasks")

        context_paths = [item.strip() for item in request.context_files]
        if any(not item for item in context_paths):
            raise CodingTaskValidationError("context_files must not contain empty paths")
        if len(set(context_paths)) != len(context_paths):
            raise CodingTaskValidationError("context_files must not contain duplicates")
        if request.target_file_path in context_paths:
            raise CodingTaskValidationError("context_files must not include the target file")

        editable_paths = []
        for editable in request.editable_files:
            if not editable.file_path.strip():
                raise CodingTaskValidationError("editable_files must not contain empty file paths")
            if editable.document_version < 1:
                raise CodingTaskValidationError("editable file document_version must be >= 1")
            editable_paths.append(editable.file_path)

        if len(set(editable_paths)) != len(editable_paths):
            raise CodingTaskValidationError("editable_files must not contain duplicates")
        if request.target_file_path in editable_paths:
            raise CodingTaskValidationError("editable_files must not include the target file")
        if set(context_paths) & set(editable_paths):
            raise CodingTaskValidationError("context_files and editable_files must not overlap")

        capabilities = list(request.requested_capabilities)
        if len(set(capabilities)) != len(capabilities):
            raise CodingTaskValidationError("requested_capabilities must not contain duplicates")

    def _validate_expected_version(
        self,
        file_path: str,
        observed: _ObservedFile,
        expected_version: int,
    ) -> None:
        if observed.document_version is None:
            raise CodingTaskValidationError(
                f"Observed file did not include a document version: {file_path}"
            )
        if observed.document_version != expected_version:
            raise CodingTaskValidationError(
                f"Editable file changed before the task could start: {file_path}"
            )

    def _validate_python_source(self, file_path: str, source: str) -> None:
        if not file_path.lower().endswith(".py"):
            return
        try:
            ast.parse(source)
        except SyntaxError as error:
            message = error.msg or "invalid syntax"
            line = error.lineno or "unknown"
            raise CodingTaskValidationError(
                f"Draft is not valid Python: {file_path}: line {line}: {message}"
            ) from error

    def _build_expected_versions(self, request: CodingTaskCreateRequest) -> dict[str, int]:
        expected_versions = {request.target_file_path: request.target_file_version}
        for editable in request.editable_files:
            expected_versions[editable.file_path] = editable.document_version
        return expected_versions

    def _ordered_editable_paths(self, request: CodingTaskCreateRequest) -> list[str]:
        return [request.target_file_path] + [item.file_path for item in request.editable_files]

    def _resolve_llm_client(self, profile_id: Optional[str]) -> LLMClientProtocol:
        if self._profile_registry is None:
            return self._llm_client
        try:
            return self._profile_registry.get_llm_client(profile_id)
        except Exception as error:
            raise CodingTaskValidationError(str(error)) from error

    def _require_session(self, task_id: str) -> _TaskSession:
        session = self._sessions.get(task_id)
        if session is None:
            raise CodingTaskNotFoundError(f"Task not found: {task_id}")
        return session

    async def _require_task_record(self, task_id: str) -> CodingTaskRecord:
        record = await self._task_store.get(task_id)
        if record is None:
            raise CodingTaskNotFoundError(f"Task not found: {task_id}")
        return record

    def _replace_session_record(self, task_id: str, record: CodingTaskRecord) -> None:
        session = self._sessions.get(task_id)
        if session is not None:
            session.record = record

    def _next_edit_attempt(self, session: _TaskSession) -> int:
        session.edit_attempts += 1
        return session.edit_attempts

    def _hash_content(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _truncate(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) <= MAX_SUMMARY_LENGTH:
            return stripped
        return f"{stripped[: MAX_SUMMARY_LENGTH - 3]}..."

    def _validate_applied_files(
        self,
        record: CodingTaskRecord,
        request: CodingTaskAppliedRequest,
    ) -> None:
        provided = {item.file_path: item.content_hash for item in request.applied_files}
        expected = {item.file_path: item.content_hash for item in record.verified_files}
        if set(provided) != set(expected):
            raise CodingTaskValidationError("applied_files must exactly match the verified file set")
        for file_path, content_hash in provided.items():
            if expected[file_path] != content_hash:
                raise CodingTaskValidationError(
                    f"Applied file content hash did not match verified draft: {file_path}"
                )

    def _normalize_applied_events(self, record: CodingTaskRecord) -> list[AppliedEventRecord]:
        if record.applied_events:
            return [item.model_copy(deep=True) for item in record.applied_events]
        return self._build_pending_applied_events(record.verified_files)

    def _build_pending_applied_events(
        self,
        verified_files: list[VerifiedFileDraft],
    ) -> list[AppliedEventRecord]:
        return [
            AppliedEventRecord(file_path=item.file_path, status="pending")
            for item in verified_files
        ]

    async def _write_missing_events(
        self,
        record: CodingTaskRecord,
        verified_files: list[VerifiedFileDraft],
        applied_events: list[AppliedEventRecord],
        event_step_id: Optional[str],
    ) -> list[AppliedEventRecord]:
        if self._ingestion_pipeline is None:
            raise CodingTaskValidationError("Apply event writing is not configured on the backend")

        verified_by_path = {item.file_path: item for item in verified_files}
        updated: list[AppliedEventRecord] = []
        for event_record in applied_events:
            if event_record.status == "written" and event_record.event_id:
                updated.append(event_record)
                continue

            verified_file = verified_by_path.get(event_record.file_path)
            if verified_file is None:
                updated.append(
                    event_record.model_copy(
                        update={
                            "status": "failed",
                            "last_error": f"Missing verified draft for {event_record.file_path}",
                        }
                    )
                )
                continue

            try:
                tail_event = await self._ingestion_pipeline.ingest(
                    RawEvent(
                        action_type="modify",
                        file_path=verified_file.file_path,
                        code_snapshot=verified_file.content,
                        intent=record.intent or "Apply verified coding draft",
                        reasoning=record.reasoning,
                        agent_step_id=event_step_id,
                        session_id=record.task_id,
                    )
                )
                updated.append(
                    event_record.model_copy(
                        update={
                            "event_id": tail_event.event_id,
                            "status": "written",
                            "last_error": None,
                        }
                    )
                )
            except Exception as error:
                updated.append(
                    event_record.model_copy(
                        update={
                            "event_id": None,
                            "status": "failed",
                            "last_error": str(error),
                        }
                    )
                )
        return updated

    async def _resolve_event_step_id(self, task_id: str) -> Optional[str]:
        steps = await self._step_store.get_by_task(task_id)
        for preferred_kind in ("verify", "edit"):
            for step in reversed(steps):
                if step.status == "succeeded" and step.step_kind == preferred_kind:
                    return step.step_id
        return None

    def _first_failed_error(
        self,
        applied_events: list[AppliedEventRecord],
    ) -> Optional[str]:
        for item in applied_events:
            if item.status != "written" and item.last_error:
                return item.last_error
        return None


__all__ = ["CodingTaskService"]
