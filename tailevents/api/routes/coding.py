"""Coding-task API routes."""

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from tailevents.api.dependencies import get_coding_task_service
from tailevents.coding import (
    CodingTaskConflictError,
    CodingTaskNotFoundError,
    CodingTaskService,
    CodingTaskValidationError,
)
from tailevents.models.task import (
    CodingTaskAppliedRequest,
    CodingTaskCreateRequest,
    CodingTaskCreateResponse,
    CodingTaskHistoryDetail,
    CodingTaskHistoryItem,
    CodingTaskToolResultRequest,
)


router = APIRouter(prefix="/coding/tasks", tags=["coding"])


@router.post("", response_model=CodingTaskCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_coding_task(
    request: CodingTaskCreateRequest,
    service: CodingTaskService = Depends(get_coding_task_service),
) -> CodingTaskCreateResponse:
    try:
        return await service.create_task(request)
    except CodingTaskValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/history", response_model=list[CodingTaskHistoryItem])
async def list_coding_task_history(
    limit: int = 20,
    service: CodingTaskService = Depends(get_coding_task_service),
) -> list[CodingTaskHistoryItem]:
    return await service.list_history(limit=limit)


@router.get("/{task_id}/stream")
async def stream_coding_task(
    task_id: str,
    service: CodingTaskService = Depends(get_coding_task_service),
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event, payload in service.stream_events(task_id):
                yield _format_sse(event, payload)
        except CodingTaskNotFoundError as error:
            yield _format_sse("error", {"message": str(error)})
            yield _format_sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{task_id}", response_model=CodingTaskHistoryDetail)
async def get_coding_task_history_detail(
    task_id: str,
    service: CodingTaskService = Depends(get_coding_task_service),
) -> CodingTaskHistoryDetail:
    try:
        return await service.get_history_detail(task_id)
    except CodingTaskNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/{task_id}/tool-result", status_code=status.HTTP_204_NO_CONTENT)
async def submit_tool_result(
    task_id: str,
    result: CodingTaskToolResultRequest,
    service: CodingTaskService = Depends(get_coding_task_service),
) -> None:
    try:
        await service.submit_tool_result(task_id, result)
    except CodingTaskNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CodingTaskConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except CodingTaskValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{task_id}/applied", status_code=status.HTTP_204_NO_CONTENT)
async def mark_coding_task_applied(
    task_id: str,
    request: CodingTaskAppliedRequest,
    service: CodingTaskService = Depends(get_coding_task_service),
) -> None:
    try:
        await service.mark_applied(task_id, request)
    except CodingTaskNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CodingTaskValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{task_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_coding_task(
    task_id: str,
    service: CodingTaskService = Depends(get_coding_task_service),
) -> None:
    try:
        await service.cancel_task(task_id)
    except CodingTaskNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _format_sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


__all__ = ["router"]
