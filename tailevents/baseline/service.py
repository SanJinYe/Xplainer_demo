"""Baseline onboarding service."""

import hashlib

from tailevents.baseline.exceptions import BaselineOnboardingValidationError
from tailevents.ingestion import IngestionPipeline
from tailevents.models.baseline import (
    BaselineOnboardFileRequest,
    BaselineOnboardFileResponse,
)
from tailevents.models.enums import ActionType
from tailevents.models.event import RawEvent
from tailevents.models.protocols import EventStoreProtocol


MAX_BASELINE_FILE_BYTES = 512 * 1024


class BaselineOnboardingService:
    """Create per-file baseline events for existing repository files."""

    def __init__(
        self,
        event_store: EventStoreProtocol,
        ingestion_pipeline: IngestionPipeline,
    ):
        self._event_store = event_store
        self._ingestion_pipeline = ingestion_pipeline

    async def onboard_file(
        self,
        request: BaselineOnboardFileRequest,
    ) -> BaselineOnboardFileResponse:
        file_path = request.file_path.strip()
        if not file_path:
            raise BaselineOnboardingValidationError("file_path must not be empty")

        snapshot_bytes = request.code_snapshot.encode("utf-8")
        if len(snapshot_bytes) > MAX_BASELINE_FILE_BYTES:
            raise BaselineOnboardingValidationError("code_snapshot exceeds the 512 KB limit")

        existing_events = await self._event_store.get_by_file(file_path)
        for event in existing_events:
            if event.action_type != ActionType.BASELINE:
                return BaselineOnboardFileResponse(
                    status="skipped",
                    file_path=file_path,
                    reason="existing_traced_history",
                )

        incoming_hash = self._hash_content(snapshot_bytes)
        for event in existing_events:
            if self._hash_content(event.code_snapshot.encode("utf-8")) == incoming_hash:
                return BaselineOnboardFileResponse(
                    status="skipped",
                    file_path=file_path,
                    reason="duplicate_baseline",
                )

        created_event = await self._ingestion_pipeline.ingest(
            RawEvent(
                action_type=ActionType.BASELINE,
                file_path=file_path,
                code_snapshot=request.code_snapshot,
                intent="Bootstrap existing repository file",
                reasoning=None,
                decision_alternatives=None,
                line_range=None,
                external_refs=[],
                session_id=None,
                agent_step_id=None,
            )
        )
        return BaselineOnboardFileResponse(
            status="created",
            file_path=file_path,
            event_id=created_event.event_id,
        )

    def _hash_content(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


__all__ = ["BaselineOnboardingService", "MAX_BASELINE_FILE_BYTES"]
