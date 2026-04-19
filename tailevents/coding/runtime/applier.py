"""Apply confirmation and event-write coordination."""

from datetime import datetime
from typing import Callable, Optional

from tailevents.coding.exceptions import CodingTaskNotFoundError, CodingTaskValidationError
from tailevents.models.event import RawEvent
from tailevents.models.protocols import (
    CodingTaskStoreProtocol,
    IngestionPipelineProtocol,
    TaskStepStoreProtocol,
)
from tailevents.models.task import (
    AppliedEventRecord,
    CodingTaskAppliedRequest,
    CodingTaskRecord,
    VerifiedFileDraft,
)


MAX_EVENT_WRITE_RETRIES = 3


class ApplyCoordinator:
    """Coordinate apply confirmation and deferred event writes."""

    def __init__(
        self,
        task_store: CodingTaskStoreProtocol,
        step_store: TaskStepStoreProtocol,
        ingestion_pipeline: Optional[IngestionPipelineProtocol] = None,
        session_record_updater: Optional[Callable[[str, CodingTaskRecord], None]] = None,
    ) -> None:
        self._task_store = task_store
        self._step_store = step_store
        self._ingestion_pipeline = ingestion_pipeline
        self._session_record_updater = session_record_updater

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

    def build_pending_applied_events(
        self,
        verified_files: list[VerifiedFileDraft],
    ) -> list[AppliedEventRecord]:
        return [
            AppliedEventRecord(file_path=item.file_path, status="pending")
            for item in verified_files
        ]

    async def _require_task_record(self, task_id: str) -> CodingTaskRecord:
        record = await self._task_store.get(task_id)
        if record is None:
            raise CodingTaskNotFoundError(f"Task not found: {task_id}")
        return record

    def _replace_session_record(self, task_id: str, record: CodingTaskRecord) -> None:
        if self._session_record_updater is not None:
            self._session_record_updater(task_id, record)

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
        return self.build_pending_applied_events(record.verified_files)

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


__all__ = ["ApplyCoordinator", "MAX_EVENT_WRITE_RETRIES"]
