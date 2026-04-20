"""Facade for the coding-task runtime."""

from dataclasses import dataclass
from typing import AsyncIterator, Optional

from tailevents.coding.capability.code import CodeCapability
from tailevents.coding.capability.explain import ExplanationCapability
from tailevents.coding.capability.graph import GraphCapability
from tailevents.coding.capability.policy import CapabilityPolicy
from tailevents.coding.capability.registry import CapabilityRegistry
from tailevents.coding.context.adapter import TaileventsContextAdapter
from tailevents.coding.exceptions import CodingTaskNotFoundError
from tailevents.coding.runtime.applier import ApplyCoordinator
from tailevents.coding.runtime.executor import CodeAttemptExecutor
from tailevents.coding.runtime.orchestrator import TaskOrchestrator
from tailevents.coding.runtime.prompt import CodingPromptBuilder
from tailevents.coding.runtime.verifier import DraftVerifier
from tailevents.models.protocols import (
    CodingProfileRegistryProtocol,
    CodingTaskStoreProtocol,
    ExplanationEngineProtocol,
    GraphServiceProtocol,
    IngestionPipelineProtocol,
    LLMClientProtocol,
    TaskStepStoreProtocol,
)
from tailevents.models.task import (
    CodingTaskAppliedRequest,
    CodingTaskCreateRequest,
    CodingTaskCreateResponse,
    CodingTaskDraftResult,
    CodingTaskHistoryDetail,
    CodingTaskHistoryItem,
    CodingTaskHistoryListResponse,
    CodingTaskHistoryTargetsResponse,
    CodingTaskRecord,
    CodingTaskToolResultRequest,
)


@dataclass(frozen=True)
class _DisabledCapability:
    """Placeholder for a reserved capability slot."""

    name: str


class CodingTaskService:
    """Expose the stable coding-task protocol while delegating runtime work."""

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        task_store: CodingTaskStoreProtocol,
        step_store: TaskStepStoreProtocol,
        ingestion_pipeline: Optional[IngestionPipelineProtocol] = None,
        profile_registry: Optional[CodingProfileRegistryProtocol] = None,
        explanation_engine: Optional[ExplanationEngineProtocol] = None,
        graph_service: Optional[GraphServiceProtocol] = None,
    ) -> None:
        prompt_builder = CodingPromptBuilder()
        code_capability = CodeCapability(
            executor=CodeAttemptExecutor(prompt_builder),
            verifier=DraftVerifier(),
        )
        capability_policy = CapabilityPolicy(profile_registry)
        capability_registry = CapabilityRegistry()
        capability_registry.register(
            "code",
            code_capability,
            enabled=capability_policy.is_runtime_capability_enabled("code"),
        )

        explain_capability = ExplanationCapability(explanation_engine)
        capability_registry.register(
            "explain",
            explain_capability,
            enabled=(
                capability_policy.is_runtime_capability_enabled("explain")
                and explain_capability.available
            ),
        )

        graph_capability = GraphCapability(graph_service)
        capability_registry.register(
            "graph",
            graph_capability,
            enabled=(
                capability_policy.is_runtime_capability_enabled("graph")
                and graph_capability.available
            ),
        )
        capability_registry.register(
            "graphrag",
            _DisabledCapability("graphrag"),
            enabled=capability_policy.is_runtime_capability_enabled("graphrag"),
        )

        self._task_store = task_store
        self._step_store = step_store
        self._capability_policy = capability_policy
        self._capability_registry = capability_registry
        self._orchestrator = TaskOrchestrator(
            llm_client=llm_client,
            task_store=task_store,
            step_store=step_store,
            capability_registry=capability_registry,
            capability_policy=capability_policy,
            context_adapter=TaileventsContextAdapter(),
            profile_registry=profile_registry,
        )
        self._apply_coordinator = ApplyCoordinator(
            task_store=task_store,
            step_store=step_store,
            ingestion_pipeline=ingestion_pipeline,
            session_record_updater=self._orchestrator.replace_session_record,
        )

    @property
    def _sessions(self) -> dict[str, object]:
        return self._orchestrator.sessions

    async def create_task(
        self,
        request: CodingTaskCreateRequest,
    ) -> CodingTaskCreateResponse:
        return await self._orchestrator.create_task(request)

    async def stream_events(
        self,
        task_id: str,
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        async for item in self._orchestrator.stream_events(task_id):
            yield item

    async def submit_tool_result(
        self,
        task_id: str,
        result: CodingTaskToolResultRequest,
    ) -> None:
        await self._orchestrator.submit_tool_result(task_id, result)

    async def cancel_task(self, task_id: str) -> None:
        await self._orchestrator.cancel_task(task_id)

    async def get_result(self, task_id: str) -> Optional[CodingTaskDraftResult]:
        return await self._orchestrator.get_result(task_id)

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
            target_hint_path=record.target_hint_path,
            resolved_primary_target_path=record.resolved_primary_target_path,
            resolved_target_files=record.resolved_target_files,
            resolved_editable_files=record.resolved_editable_files,
            resolved_context_files=record.resolved_context_files,
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
        await self._apply_coordinator.mark_applied(task_id, request)

    async def retry_event_writes(self, task_id: str) -> None:
        await self._apply_coordinator.retry_event_writes(task_id)

    async def reset_all_sessions(self) -> int:
        return await self._orchestrator.reset_all_sessions()

    async def _require_task_record(self, task_id: str) -> CodingTaskRecord:
        record = await self._task_store.get(task_id)
        if record is None:
            raise CodingTaskNotFoundError(f"Task not found: {task_id}")
        return record


__all__ = ["CodingTaskService"]
