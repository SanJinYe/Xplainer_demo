"""Coding task routes for the B0 vertical slice."""

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from tailevents.api.dependencies import get_task_service
from tailevents.models.task import CodingTaskRequest
from tailevents.tasks import CodingTaskService


router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/stream")
async def stream_task(
    request: CodingTaskRequest,
    task_service: CodingTaskService = Depends(get_task_service),
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event_name, payload in task_service.run_stream(request):
                yield _encode_sse(event_name, payload)
        except Exception as error:
            yield _encode_sse("error", {"message": str(error)})
        finally:
            yield _encode_sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _encode_sse(event_name: str, payload: dict[str, object]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


__all__ = ["router"]
