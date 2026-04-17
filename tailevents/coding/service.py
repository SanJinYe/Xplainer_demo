"""Backend orchestration for the B-next coding task loop."""

import ast
import asyncio
from datetime import datetime
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from pydantic import BaseModel

from tailevents.coding.exceptions import (
    CodingTaskCancelledError,
    CodingTaskConflictError,
    CodingTaskNotFoundError,
    CodingTaskValidationError,
)
from tailevents.models.protocols import (
    CodingTaskStoreProtocol,
    LLMClientProtocol,
    TaskStepStoreProtocol,
)
from tailevents.models.task import (
    CodingTaskAppliedRequest,
    CodingTaskCreateRequest,
    CodingTaskCreateResponse,
    CodingTaskDraftResult,
    CodingTaskEdit,
    CodingTaskHistoryDetail,
    CodingTaskHistoryItem,
    CodingTaskRecord,
    CodingTaskToolResultRequest,
    TaskStepEvent,
    ToolCallPayload,
    new_call_id,
    new_step_id,
    new_task_id,
)


SYSTEM_PROMPT = """
You are a coding agent for a single target Python file.

You already have the exact observed contents of:
- one editable target file
- zero to two read-only context files

You must return exactly one JSON object and nothing else.

The JSON object must contain exactly:
- edits
- intent
- reasoning

Rules:
- edits must be an array of exact-match replacements.
- Each edit must contain exactly old_text and new_text.
- old_text must be copied exactly from the observed target file content.
- Only edit the target file.
- Do not modify context files.
- Keep edits as small and local as possible.
- Preserve indentation, spacing, and blank lines.
- The final target file must remain valid Python.
""".strip()

USER_PROMPT_TEMPLATE = """
Task goal:
{user_prompt}

Target file path:
{target_file_path}

Target file content:
<target_file>
{target_file_content}
</target_file>

Readonly context files:
{context_block}

Previous failure to fix:
{failure_hint}
""".strip()

CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
MAX_CONTEXT_FILES = 2
MAX_RETRIES = 1
MAX_SUMMARY_LENGTH = 240


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
    allowed_files: set[str]
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
    """Coordinate a minimal backend-driven coding task loop."""

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        task_store: CodingTaskStoreProtocol,
        step_store: TaskStepStoreProtocol,
    ):
        self._llm_client = llm_client
        self._task_store = task_store
        self._step_store = step_store
        self._sessions: dict[str, _TaskSession] = {}

    async def create_task(
        self,
        request: CodingTaskCreateRequest,
    ) -> CodingTaskCreateResponse:
        self._validate_request(request)
        task_id = new_task_id()
        record = CodingTaskRecord(
            task_id=task_id,
            target_file_path=request.target_file_path,
            user_prompt=request.user_prompt,
            context_files=request.context_files,
            status="created",
        )
        await self._task_store.put(record)
        session = _TaskSession(
            task_id=task_id,
            request=request,
            record=record,
            allowed_files={request.target_file_path, *request.context_files},
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

    async def list_history(self, limit: int = 20) -> list[CodingTaskHistoryItem]:
        records = await self._task_store.list_recent(limit=limit)
        return [
            CodingTaskHistoryItem(
                task_id=record.task_id,
                target_file_path=record.target_file_path,
                status=record.status,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in records
        ]

    async def get_history_detail(self, task_id: str) -> CodingTaskHistoryDetail:
        record = await self._require_task_record(task_id)
        steps = await self._step_store.get_by_task(task_id)
        return CodingTaskHistoryDetail(
            task_id=record.task_id,
            target_file_path=record.target_file_path,
            user_prompt=record.user_prompt,
            context_files=record.context_files,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            steps=steps,
            model_output_text=record.model_output_text,
            verified_draft_content=record.verified_draft_content,
            intent=record.intent,
            reasoning=record.reasoning,
            last_error=record.last_error,
            applied_event_id=record.applied_event_id,
        )

    async def mark_applied(
        self,
        task_id: str,
        request: CodingTaskAppliedRequest,
    ) -> None:
        record = await self._require_task_record(task_id)
        updated = record.model_copy(
            update={
                "status": "applied",
                "applied_event_id": request.event_id,
                "last_error": None,
                "updated_at": datetime.utcnow(),
            }
        )
        await self._task_store.put(updated)
        session = self._sessions.get(task_id)
        if session is not None:
            session.record = updated

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

            target_view = await self._request_view(
                session,
                file_path=session.request.target_file_path,
                intent="Observe the target file before editing",
            )
            self._validate_initial_target(session, target_view)
            initial_hash = target_view.content_hash

            context_views: list[_ObservedFile] = []
            for context_file in session.request.context_files:
                context_views.append(
                    await self._request_view(
                        session,
                        file_path=context_file,
                        intent=f"Observe readonly context file {context_file}",
                    )
                )

            failure_hint: Optional[str] = None
            attempt = 0
            while attempt <= MAX_RETRIES:
                try:
                    plan, draft_content, edit_step_id = await self._run_edit_step(
                        session,
                        target_view,
                        context_views,
                        failure_hint,
                    )
                    verify_step_id = await self._run_verify_step(
                        session,
                        target_view=target_view,
                        draft_content=draft_content,
                    )
                    session.result = CodingTaskDraftResult(
                        task_id=session.task_id,
                        updated_file_content=draft_content,
                        intent=plan.intent,
                        reasoning=plan.reasoning,
                        session_id=session.task_id,
                        agent_step_id=verify_step_id or edit_step_id,
                    )
                    await self._update_task_record(
                        session,
                        status="ready_to_apply",
                        verified_draft_content=draft_content,
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
                    target_view = await self._request_view(
                        session,
                        file_path=session.request.target_file_path,
                        intent="Re-observe the target file after a failed attempt",
                    )
                    self._validate_retry_target(
                        session,
                        target_view=target_view,
                        initial_hash=initial_hash,
                    )
        except CodingTaskCancelledError:
            await self._update_task_record(
                session,
                status="cancelled",
                last_error="Task cancelled",
            )
            await self._emit(session, "status", {"status": "cancelled"})
        except asyncio.CancelledError:
            await self._update_task_record(
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

    async def _run_edit_step(
        self,
        session: _TaskSession,
        target_view: _ObservedFile,
        context_views: list[_ObservedFile],
        failure_hint: Optional[str],
    ) -> tuple[_EditPlan, str, str]:
        step_id = new_step_id()
        attempt_number = self._next_edit_attempt(session)
        started = TaskStepEvent(
            task_id=session.task_id,
            step_id=step_id,
            step_kind="edit",
            status="started",
            file_path=target_view.file_path,
            content_hash=target_view.content_hash,
            intent="Generate a local edit plan for the target file",
            input_summary=self._truncate(
                f"target={target_view.file_path}, contexts={len(context_views)}"
            ),
        )
        await self._record_step(session, started)

        try:
            raw_output = ""
            captured_output = False
            async for chunk in self._llm_client.stream_generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=self._build_user_prompt(
                    session.request,
                    target_view=target_view,
                    context_views=context_views,
                    failure_hint=failure_hint,
                ),
                max_tokens=4000,
                temperature=0.1,
            ):
                if not chunk:
                    continue
                raw_output += chunk
                await self._emit(
                    session,
                    "model_delta",
                    {"text": chunk},
                )
            await self._capture_model_output(session, attempt_number, raw_output)
            captured_output = True
            plan = self._parse_edit_plan(raw_output)
            draft_content = self._apply_edits(target_view.content, plan.edits)
            if draft_content == target_view.content:
                raise CodingTaskValidationError("Edit plan did not change the target file")
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
                    file_path=target_view.file_path,
                    content_hash=target_view.content_hash,
                    intent="Generate a local edit plan for the target file",
                    reasoning_summary=self._truncate(str(error)),
                    input_summary=self._truncate(
                        f"target={target_view.file_path}, contexts={len(context_views)}"
                    ),
                    output_summary=self._truncate(str(error)),
                ),
            )
            raise

        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="edit",
                status="succeeded",
                file_path=target_view.file_path,
                content_hash=self._hash_content(draft_content),
                intent=plan.intent,
                reasoning_summary=self._truncate(plan.reasoning),
                input_summary=self._truncate(
                    f"target={target_view.file_path}, contexts={len(context_views)}"
                ),
                output_summary=self._truncate(
                    f"generated {len(plan.edits)} edit(s) for the target draft"
                ),
            ),
        )
        return (plan, draft_content, step_id)

    async def _run_verify_step(
        self,
        session: _TaskSession,
        target_view: _ObservedFile,
        draft_content: str,
    ) -> str:
        step_id = new_step_id()
        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="verify",
                status="started",
                file_path=target_view.file_path,
                content_hash=self._hash_content(draft_content),
                intent="Verify the draft before Apply",
                input_summary=self._truncate("python syntax + target drift check"),
            ),
        )

        try:
            self._validate_python_source(target_view.file_path, draft_content)
        except Exception as error:
            await self._record_step(
                session,
                TaskStepEvent(
                    task_id=session.task_id,
                    step_id=step_id,
                    step_kind="verify",
                    status="failed",
                    file_path=target_view.file_path,
                    content_hash=self._hash_content(draft_content),
                    intent="Verify the draft before Apply",
                    reasoning_summary=self._truncate(str(error)),
                    input_summary=self._truncate("python syntax + target drift check"),
                    output_summary=self._truncate(str(error)),
                ),
            )
            raise

        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="verify",
                status="succeeded",
                file_path=target_view.file_path,
                content_hash=self._hash_content(draft_content),
                intent="Verify the draft before Apply",
                reasoning_summary="Python syntax is valid and the target file did not drift.",
                input_summary=self._truncate("python syntax + target drift check"),
                output_summary=self._truncate("verified draft ready for Apply"),
            ),
        )
        return step_id

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
        target_view: _ObservedFile,
        context_views: list[_ObservedFile],
        failure_hint: Optional[str],
    ) -> str:
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
            target_file_path=target_view.file_path,
            target_file_content=target_view.content,
            context_block=context_block,
            failure_hint=failure_hint or "None",
        )

    def _parse_edit_plan(self, raw_output: str) -> _EditPlan:
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

    def _apply_edits(self, original_content: str, edits: list[CodingTaskEdit]) -> str:
        working_content = original_content
        for index, edit in enumerate(edits, start=1):
            if not edit.old_text:
                raise CodingTaskValidationError(f"Edit {index} old_text must not be empty")

            matches = working_content.count(edit.old_text)
            if matches == 0:
                raise CodingTaskValidationError(
                    f"Edit {index} old_text did not match the observed target file"
                )
            if matches > 1:
                raise CodingTaskValidationError(
                    f"Edit {index} old_text matched multiple locations in the observed target file"
                )

            working_content = working_content.replace(edit.old_text, edit.new_text, 1)

        return working_content

    def _validate_request(self, request: CodingTaskCreateRequest) -> None:
        if not request.target_file_path.strip():
            raise CodingTaskValidationError("target_file_path must not be empty")
        if request.target_file_version < 1:
            raise CodingTaskValidationError("target_file_version must be >= 1")
        if not request.user_prompt.strip():
            raise CodingTaskValidationError("user_prompt must not be empty")
        if len(request.context_files) > MAX_CONTEXT_FILES:
            raise CodingTaskValidationError("context_files must contain at most 2 items")
        deduped = {item for item in request.context_files}
        if len(deduped) != len(request.context_files):
            raise CodingTaskValidationError("context_files must not contain duplicates")
        if request.target_file_path in deduped:
            raise CodingTaskValidationError("context_files must not include the target file")

    def _validate_initial_target(self, session: _TaskSession, target_view: _ObservedFile) -> None:
        version = target_view.document_version
        if version is None:
            raise CodingTaskValidationError("Target view did not include a document version")
        if version != session.request.target_file_version:
            raise CodingTaskValidationError("Target file changed before the task could start")

    def _validate_retry_target(
        self,
        session: _TaskSession,
        target_view: _ObservedFile,
        initial_hash: str,
    ) -> None:
        version = target_view.document_version
        if version is None or version != session.request.target_file_version:
            raise CodingTaskValidationError("Target file changed during task execution")
        if target_view.content_hash != initial_hash:
            raise CodingTaskValidationError("Target file content drifted during task execution")

    def _validate_python_source(self, file_path: str, source: str) -> None:
        if not file_path.lower().endswith(".py"):
            return
        try:
            ast.parse(source)
        except SyntaxError as error:
            message = error.msg or "invalid syntax"
            line = error.lineno or "unknown"
            raise CodingTaskValidationError(
                f"Draft is not valid Python: line {line}: {message}"
            ) from error

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


__all__ = ["CodingTaskService"]
