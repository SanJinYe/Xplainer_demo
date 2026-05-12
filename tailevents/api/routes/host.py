"""Host adapter API routes."""

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, status

from tailevents.api.dependencies import AppContainer, get_container
from tailevents.host_adapters.cline import (
    ClineConversionResult,
    ClineTraceBatchRequest,
    ClineTraceIngestResponse,
    cline_session_id,
    convert_cline_messages,
)
from tailevents.models.event import TailEvent
from tailevents.models.task import AppliedEventRecord, CodingTaskRecord, TaskStepEvent


router = APIRouter(prefix="/host", tags=["host"])


@router.post(
    "/cline/events",
    response_model=ClineTraceIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_cline_events(
    request: ClineTraceBatchRequest,
    container: AppContainer = Depends(get_container),
) -> ClineTraceIngestResponse:
    conversion = convert_cline_messages(
        task_id=request.task_id,
        workspace_root=Path(request.cwd),
        messages=request.messages,
    )
    ingested_events = (
        await container.ingestion_pipeline.ingest_batch(conversion.raw_events)
        if conversion.raw_events
        else []
    )
    coding_task_id = await _persist_cline_task_binding(
        container=container,
        conversion=conversion,
        ingested_events=ingested_events,
    )
    summary = conversion.summary
    return ClineTraceIngestResponse(
        task_id=summary.task_id,
        session_id=cline_session_id(summary.task_id),
        coding_task_id=coding_task_id,
        message_count=summary.message_count,
        tool_count=summary.tool_count,
        file_change_count=summary.file_change_count,
        raw_event_count=summary.raw_event_count,
        task_prompt=summary.task_prompt,
        read_observation_count=summary.read_observation_count,
        completion_count=summary.completion_count,
        error_count=summary.error_count,
        ingested_count=len(ingested_events),
        skipped=dict(summary.skipped),
        event_ids=[event.event_id for event in ingested_events],
    )


async def _persist_cline_task_binding(
    *,
    container: AppContainer,
    conversion: ClineConversionResult,
    ingested_events: list[TailEvent],
) -> str:
    summary = conversion.summary
    coding_task_id = cline_session_id(summary.task_id)
    now = datetime.utcnow()
    existing = await container.task_store.get(coding_task_id)
    existing_steps = {
        step.step_id for step in await container.task_step_store.get_by_task(coding_task_id)
    }

    target_files = _merge_unique(
        list(existing.resolved_target_files if existing is not None else [])
        + [event.file_path for event in ingested_events]
    )
    applied_events = _merge_applied_events(existing, ingested_events)
    status = "applied" if applied_events else "applied_without_events"
    prompt = (
        summary.task_prompt
        or (existing.user_prompt if existing is not None else None)
        or f"Cline task {summary.task_id}"
    )

    record = CodingTaskRecord(
        task_id=coding_task_id,
        target_file_path=target_files[0] if target_files else "",
        target_hint_path=None,
        resolved_primary_target_path=target_files[0] if target_files else None,
        resolved_target_files=target_files,
        resolved_editable_files=target_files,
        resolved_context_files=[],
        user_prompt=prompt,
        context_files=[],
        editable_files=target_files,
        status=status,
        created_at=existing.created_at if existing is not None else now,
        updated_at=now,
        model_output_text=None,
        verified_files=[],
        intent=f"Cline trace ingestion for {summary.task_id}",
        reasoning=(
            f"Ingested {len(ingested_events)} Cline file-change event(s) "
            f"from session {coding_task_id}."
        ),
        last_error=None,
        applied_events=applied_events,
        launch_mode="new",
        source_task_id=None,
        selected_profile_id=None,
        requested_capabilities=[],
        applied_event_retry_count=existing.applied_event_retry_count
        if existing is not None
        else 0,
    )
    await container.task_store.put(record)

    for observation in conversion.observations:
        step_id = str(observation.get("agent_step_id") or "")
        if not step_id or step_id in existing_steps:
            continue
        await container.task_step_store.put(
            TaskStepEvent(
                task_id=coding_task_id,
                step_id=step_id,
                step_kind="view",
                status="succeeded",
                file_path=str(observation.get("path") or ""),
                intent=f"Cline read {observation.get('path') or ''}".strip(),
                tool_name=str(observation.get("tool") or "readFile"),
                input_summary=str(observation.get("path") or ""),
                output_summary="read observation",
            )
        )
        existing_steps.add(step_id)

    for event in ingested_events:
        step_id = event.agent_step_id or event.event_id
        if step_id in existing_steps:
            continue
        await container.task_step_store.put(
            TaskStepEvent(
                task_id=coding_task_id,
                step_id=step_id,
                step_kind="edit",
                status="succeeded",
                file_path=event.file_path,
                intent=event.intent,
                reasoning_summary=event.reasoning,
                tool_name="cline_file_change",
                input_summary=event.action_type.value,
                output_summary=event.event_id,
            )
        )
        existing_steps.add(step_id)

    return coding_task_id


def _merge_applied_events(
    existing: Optional[CodingTaskRecord],
    ingested_events: list[TailEvent],
) -> list[AppliedEventRecord]:
    by_file_and_event: dict[tuple[str, str], AppliedEventRecord] = {}
    if existing is not None:
        for item in existing.applied_events:
            if item.event_id is None:
                continue
            by_file_and_event[(item.file_path, item.event_id)] = item

    for event in ingested_events:
        by_file_and_event[(event.file_path, event.event_id)] = AppliedEventRecord(
            file_path=event.file_path,
            event_id=event.event_id,
            status="written",
        )

    return list(by_file_and_event.values())


def _merge_unique(values: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


__all__ = ["router"]
