"""Host adapter API routes."""

from pathlib import Path

from fastapi import APIRouter, Depends, status

from tailevents.api.dependencies import get_ingestion_pipeline
from tailevents.host_adapters.cline import (
    ClineTraceBatchRequest,
    ClineTraceIngestResponse,
    convert_cline_messages,
)
from tailevents.ingestion import IngestionPipeline


router = APIRouter(prefix="/host", tags=["host"])


@router.post(
    "/cline/events",
    response_model=ClineTraceIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_cline_events(
    request: ClineTraceBatchRequest,
    ingestion_pipeline: IngestionPipeline = Depends(get_ingestion_pipeline),
) -> ClineTraceIngestResponse:
    conversion = convert_cline_messages(
        task_id=request.task_id,
        workspace_root=Path(request.cwd),
        messages=request.messages,
    )
    ingested_events = (
        await ingestion_pipeline.ingest_batch(conversion.raw_events)
        if conversion.raw_events
        else []
    )
    summary = conversion.summary
    return ClineTraceIngestResponse(
        task_id=summary.task_id,
        session_id=f"cline:{summary.task_id}",
        message_count=summary.message_count,
        tool_count=summary.tool_count,
        file_change_count=summary.file_change_count,
        raw_event_count=summary.raw_event_count,
        read_observation_count=summary.read_observation_count,
        completion_count=summary.completion_count,
        error_count=summary.error_count,
        ingested_count=len(ingested_events),
        guidance_score=conversion.guidance_score,
        guidance_hints=conversion.guidance_hints,
        skipped=dict(summary.skipped),
        event_ids=[event.event_id for event in ingested_events],
    )


__all__ = ["router"]
