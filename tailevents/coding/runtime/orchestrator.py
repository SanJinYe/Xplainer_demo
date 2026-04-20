"""Session lifecycle orchestration for coding tasks."""

import asyncio
from datetime import datetime
import hashlib
import sqlite3
from typing import AsyncIterator, Optional, cast

from tailevents.coding.capability.code import CodeCapability
from tailevents.coding.capability.policy import CapabilityPolicy
from tailevents.coding.capability.registry import CapabilityRegistry
from tailevents.coding.context.adapter import TaileventsContextAdapter
from tailevents.coding.context.model import ObservedFileView
from tailevents.coding.exceptions import (
    CodingTaskCancelledError,
    CodingTaskConflictError,
    CodingTaskNotFoundError,
    CodingTaskValidationError,
)
from tailevents.coding.runtime.session import PendingToolRequest, TaskRuntimeSession
from tailevents.coding.runtime.scope import ScopeResolver
from tailevents.models.protocols import (
    CodingProfileRegistryProtocol,
    CodingTaskStoreProtocol,
    LLMClientProtocol,
    TaskStepStoreProtocol,
)
from tailevents.models.task import (
    AppliedEventRecord,
    CodingTaskCreateRequest,
    CodingTaskCreateResponse,
    CodingTaskDraftResult,
    CodingTaskRecord,
    CodingTaskToolResultRequest,
    TaskStepEvent,
    ToolCallPayload,
    VerifiedFileDraft,
    new_call_id,
    new_task_id,
    new_step_id,
)


MAX_CONTEXT_FILES = 3
MAX_TOTAL_EDITABLE_FILES = 2
MAX_RETRIES = 1
MAX_SUMMARY_LENGTH = 240
WORKSPACE_SCOPE_SENTINEL = "<workspace>"


class TaskOrchestrator:
    """Own in-memory coding sessions and the main retry/tool loop."""

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        task_store: CodingTaskStoreProtocol,
        step_store: TaskStepStoreProtocol,
        capability_registry: CapabilityRegistry,
        capability_policy: CapabilityPolicy,
        context_adapter: TaileventsContextAdapter,
        profile_registry: Optional[CodingProfileRegistryProtocol] = None,
    ) -> None:
        self._llm_client = llm_client
        self._task_store = task_store
        self._step_store = step_store
        self._capability_registry = capability_registry
        self._capability_policy = capability_policy
        self._context_adapter = context_adapter
        self._profile_registry = profile_registry
        self._scope_resolver = ScopeResolver()
        self._sessions: dict[str, TaskRuntimeSession] = {}

    @property
    def sessions(self) -> dict[str, TaskRuntimeSession]:
        return self._sessions

    async def create_task(
        self,
        request: CodingTaskCreateRequest,
    ) -> CodingTaskCreateResponse:
        self._validate_request(request)
        task_id = new_task_id()
        llm_client = self._resolve_llm_client(request.selected_profile_id)
        target_hint_path = self._normalize_optional_path(request.target_file_path)
        requested_lanes = self._capability_policy.resolve_requested_lanes(
            list(request.requested_capabilities)
        )
        record = CodingTaskRecord(
            task_id=task_id,
            target_file_path=target_hint_path or "",
            target_hint_path=target_hint_path,
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

        expected_versions = self._build_expected_versions(request)
        session = TaskRuntimeSession(
            task_id=task_id,
            request=request,
            record=record,
            llm_client=llm_client,
            requested_lanes=requested_lanes,
            editable_paths=set(),
            readonly_paths=set(),
            expected_versions=expected_versions,
            target_hint_path=target_hint_path,
        )
        self._sessions[task_id] = session

        session.worker = asyncio.create_task(self._run_task(session))
        return CodingTaskCreateResponse(task_id=task_id)

    async def stream_events(
        self,
        task_id: str,
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        session = self._require_session(task_id)
        async for item in session.event_sink.stream():
            yield item

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
        if result.error:
            pending.future.set_exception(CodingTaskValidationError(result.error))
            session.pending_tool = None
            return

        if pending.payload.tool_name == "view_file":
            if result.file_path != pending.payload.file_path:
                raise CodingTaskConflictError("Tool result file_path does not match the pending request")
            if result.content is None or result.content_hash is None or result.file_path is None:
                raise CodingTaskValidationError("Tool result must include file_path, content, and content_hash")
            pending.future.set_result(
                ObservedFileView(
                    file_path=result.file_path,
                    content=result.content,
                    content_hash=result.content_hash,
                    document_version=result.document_version,
                )
            )
        else:
            pending.future.set_result([item.strip() for item in result.matches if item.strip()])
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

    def replace_session_record(self, task_id: str, record: CodingTaskRecord) -> None:
        session = self._sessions.get(task_id)
        if session is not None:
            session.record = record

    async def _run_task(self, session: TaskRuntimeSession) -> None:
        try:
            await self._update_task_record(
                session,
                status="running",
                last_error=None,
            )
            await session.event_sink.emit("status", {"status": "running"})

            resolved_scope = await self._scope_resolver.resolve(
                session=session,
                search_workspace=self._search_workspace,
            )
            session.resolved_primary_target_path = resolved_scope.primary_target_path
            session.resolved_target_files = list(resolved_scope.target_files)
            session.resolved_editable_files = list(resolved_scope.editable_files)
            session.resolved_context_files = list(resolved_scope.context_files)
            session.editable_paths = set(resolved_scope.editable_files)
            session.readonly_paths = set(resolved_scope.context_files)
            session.observation_candidates = set(resolved_scope.target_files) | set(
                resolved_scope.context_files
            )
            session.scope_summary = resolved_scope.scope_summary
            await self._update_task_record(
                session,
                target_file_path=resolved_scope.primary_target_path,
                resolved_primary_target_path=resolved_scope.primary_target_path,
                resolved_target_files=resolved_scope.target_files,
                resolved_editable_files=resolved_scope.editable_files,
                resolved_context_files=resolved_scope.context_files,
            )

            context_bundle = await self._context_adapter.build_bundle(
                session=session,
                request_view=self._request_view,
                validate_expected_version=self._validate_expected_version,
            )
            initial_hashes = {
                file_path: observed.content_hash
                for file_path, observed in context_bundle.editable_views.items()
            }
            failure_hint: Optional[str] = None
            attempt = 0
            code_capability = cast(
                CodeCapability,
                self._capability_registry.require_enabled("code"),
            )

            while attempt <= MAX_RETRIES:
                try:
                    outcome = await code_capability.execute_attempt(
                        session=session,
                        context_bundle=context_bundle,
                        failure_hint=failure_hint,
                        attempt_metadata={"attempt_number": session.next_edit_attempt()},
                        request_view=self._request_view,
                        record_step=self._record_step,
                        capture_model_output=self._capture_model_output,
                        emit_model_delta=self._emit_model_delta,
                    )
                    primary_draft = next(
                        (
                            item.content
                            for item in outcome.verified_files
                            if item.file_path == session.resolved_primary_target_path
                        ),
                        None,
                    )
                    session.result = CodingTaskDraftResult(
                        task_id=session.task_id,
                        verified_files=outcome.verified_files,
                        resolved_primary_target_path=session.resolved_primary_target_path,
                        updated_file_content=primary_draft,
                        intent=outcome.plan.intent,
                        reasoning=outcome.plan.reasoning,
                        session_id=session.task_id,
                        agent_step_id=outcome.verify_step_id or outcome.edit_step_id,
                    )
                    await self._update_task_record(
                        session,
                        status="ready_to_apply",
                        verified_draft_content=None,
                        verified_files=outcome.verified_files,
                        applied_events=self._build_pending_applied_events(
                            outcome.verified_files
                        ),
                        target_file_path=session.resolved_primary_target_path or "",
                        resolved_primary_target_path=session.resolved_primary_target_path,
                        resolved_target_files=list(session.resolved_target_files),
                        resolved_editable_files=list(session.resolved_editable_files),
                        resolved_context_files=list(session.resolved_context_files),
                        intent=outcome.plan.intent,
                        reasoning=outcome.plan.reasoning,
                        last_error=None,
                    )
                    await session.event_sink.emit(
                        "result",
                        session.result.model_dump(mode="json"),
                    )
                    await session.event_sink.emit("status", {"status": "ready_to_apply"})
                    break
                except CodingTaskCancelledError:
                    raise
                except Exception as error:
                    failure_hint = str(error)
                    if attempt >= MAX_RETRIES:
                        raise
                    attempt += 1
                    context_bundle = await self._context_adapter.rebuild_bundle_for_retry(
                        session=session,
                        request_view=self._request_view,
                        validate_expected_version=self._validate_expected_version,
                        initial_hashes=initial_hashes,
                    )
        except CodingTaskCancelledError:
            await self._update_task_record_if_available(
                session,
                status="cancelled",
                last_error="Task cancelled",
            )
            await session.event_sink.emit("status", {"status": "cancelled"})
        except asyncio.CancelledError:
            await self._update_task_record_if_available(
                session,
                status="cancelled",
                last_error="Task cancelled",
            )
            await session.event_sink.emit("status", {"status": "cancelled"})
        except Exception as error:
            await self._update_task_record(
                session,
                status="failed",
                last_error=str(error),
            )
            await session.event_sink.emit("error", {"message": str(error)})
            await session.event_sink.emit("status", {"status": "failed"})
        finally:
            await session.event_sink.emit("done", {})
            session.done = True
            await session.event_sink.mark_done()

    async def _request_view(
        self,
        session: TaskRuntimeSession,
        file_path: str,
        intent: str,
    ) -> ObservedFileView:
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
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        session.pending_tool = PendingToolRequest(payload=payload, future=future)
        await session.event_sink.emit("tool_call", payload.model_dump(mode="json"))

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

        if not isinstance(observed, ObservedFileView):
            raise CodingTaskValidationError("view_file tool returned an invalid result payload")

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

    async def _search_workspace(
        self,
        session: TaskRuntimeSession,
        query: str,
        limit: int,
        intent: str,
    ) -> list[str]:
        step_id = new_step_id()
        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="view",
                status="started",
                file_path=WORKSPACE_SCOPE_SENTINEL,
                intent=intent,
                tool_name="search_workspace",
                input_summary=self._truncate(f"query={query!r}, limit={limit}"),
            ),
        )

        payload = ToolCallPayload(
            task_id=session.task_id,
            call_id=new_call_id(),
            step_id=step_id,
            tool_name="search_workspace",
            query=query,
            limit=limit,
            intent=intent,
        )
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        session.pending_tool = PendingToolRequest(payload=payload, future=future)
        await session.event_sink.emit("tool_call", payload.model_dump(mode="json"))

        try:
            matches = await future
        except Exception as error:
            await self._record_step(
                session,
                TaskStepEvent(
                    task_id=session.task_id,
                    step_id=step_id,
                    step_kind="view",
                    status="failed",
                    file_path=WORKSPACE_SCOPE_SENTINEL,
                    intent=intent,
                    tool_name="search_workspace",
                    reasoning_summary=self._truncate(str(error)),
                    input_summary=self._truncate(f"query={query!r}, limit={limit}"),
                    output_summary=self._truncate("workspace search failed"),
                ),
            )
            raise

        if not isinstance(matches, list):
            raise CodingTaskValidationError("search_workspace tool returned an invalid result payload")

        normalized_matches = [str(item).strip() for item in matches if str(item).strip()]
        await self._record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="view",
                status="succeeded",
                file_path=WORKSPACE_SCOPE_SENTINEL,
                intent=intent,
                tool_name="search_workspace",
                input_summary=self._truncate(f"query={query!r}, limit={limit}"),
                output_summary=self._truncate(
                    f"matched {len(normalized_matches)} workspace file(s)"
                ),
            ),
        )
        return normalized_matches

    async def _record_step(self, session: TaskRuntimeSession, event: TaskStepEvent) -> None:
        await self._step_store.put(event)
        await session.event_sink.emit("step", event.model_dump(mode="json"))

    async def _capture_model_output(
        self,
        session: TaskRuntimeSession,
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

    async def _emit_model_delta(self, session: TaskRuntimeSession, text: str) -> None:
        await session.event_sink.emit("model_delta", {"text": text})

    async def _update_task_record(self, session: TaskRuntimeSession, **changes: object) -> None:
        session.record = session.record.model_copy(
            update={
                **changes,
                "updated_at": datetime.utcnow(),
            }
        )
        await self._task_store.put(session.record)

    async def _update_task_record_if_available(
        self,
        session: TaskRuntimeSession,
        **changes: object,
    ) -> None:
        try:
            await self._update_task_record(session, **changes)
        except sqlite3.ProgrammingError as error:
            if "closed database" not in str(error).lower():
                raise

    def _validate_request(self, request: CodingTaskCreateRequest) -> None:
        if not request.user_prompt.strip():
            raise CodingTaskValidationError("user_prompt must not be empty")
        if len(request.context_files) > MAX_CONTEXT_FILES:
            raise CodingTaskValidationError("context_files must contain at most 3 items")
        target_path = self._normalize_optional_path(request.target_file_path)
        target_version = request.target_file_version
        if target_path is not None and (target_version is None or target_version < 1):
            raise CodingTaskValidationError("target_file_version must be >= 1 when target_file_path is provided")
        if target_path is None and target_version is not None:
            raise CodingTaskValidationError("target_file_version is only valid when target_file_path is provided")
        total_editable_files = len(request.editable_files) + (1 if target_path else 0)
        if total_editable_files > MAX_TOTAL_EDITABLE_FILES:
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
        if target_path and target_path in context_paths:
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
        if target_path and target_path in editable_paths:
            raise CodingTaskValidationError("editable_files must not include the target file")
        if set(context_paths) & set(editable_paths):
            raise CodingTaskValidationError("context_files and editable_files must not overlap")

        capabilities = list(request.requested_capabilities)
        if len(set(capabilities)) != len(capabilities):
            raise CodingTaskValidationError("requested_capabilities must not contain duplicates")
        self._capability_policy.resolve_requested_lanes(capabilities)

    def _build_expected_versions(
        self,
        request: CodingTaskCreateRequest,
    ) -> dict[str, Optional[int]]:
        expected_versions: dict[str, Optional[int]] = {}
        target_path = self._normalize_optional_path(request.target_file_path)
        if target_path is not None:
            expected_versions[target_path] = request.target_file_version
        for editable in request.editable_files:
            expected_versions[editable.file_path] = editable.document_version
        return expected_versions

    def _resolve_llm_client(self, profile_id: Optional[str]) -> LLMClientProtocol:
        if self._profile_registry is None:
            return self._llm_client
        try:
            return self._profile_registry.get_llm_client(profile_id)
        except Exception as error:
            raise CodingTaskValidationError(str(error)) from error

    def _validate_expected_version(
        self,
        file_path: str,
        observed: ObservedFileView,
        expected_version: Optional[int],
    ) -> None:
        if expected_version is None:
            return
        if observed.document_version is None:
            raise CodingTaskValidationError(
                f"Observed file did not include a document version: {file_path}"
            )
        if observed.document_version != expected_version:
            raise CodingTaskValidationError(
                f"Editable file changed before the task could start: {file_path}"
            )

    def _require_session(self, task_id: str) -> TaskRuntimeSession:
        session = self._sessions.get(task_id)
        if session is None:
            raise CodingTaskNotFoundError(f"Task not found: {task_id}")
        return session

    def _build_pending_applied_events(
        self,
        verified_files: list[VerifiedFileDraft],
    ) -> list[AppliedEventRecord]:
        return [
            AppliedEventRecord(file_path=item.file_path, status="pending")
            for item in verified_files
        ]

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

    def _normalize_optional_path(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return stripped


__all__ = ["TaskOrchestrator"]
