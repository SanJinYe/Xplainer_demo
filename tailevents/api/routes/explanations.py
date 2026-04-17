"""Explanation-related API routes."""

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from tailevents.api.dependencies import (
    get_explanation_engine,
    get_query_router,
)
from tailevents.explanation import ExplanationEngine
from tailevents.explanation.exceptions import EntityExplanationNotFoundError
from tailevents.models.explanation import (
    EntityExplanation,
    ExplanationRequest,
    ExplanationResponse,
)
from tailevents.query import QueryRouter


router = APIRouter(prefix="/explain", tags=["explanations"])


@router.post("", response_model=ExplanationResponse)
async def explain(
    request: ExplanationRequest,
    query_router: QueryRouter = Depends(get_query_router),
) -> ExplanationResponse:
    return await query_router.route(request)


@router.get("/{entity_id}/summary", response_model=EntityExplanation)
async def explain_summary(
    entity_id: str,
    explanation_engine: ExplanationEngine = Depends(get_explanation_engine),
) -> EntityExplanation:
    return await explanation_engine.explain_entity(
        entity_id=entity_id,
        detail_level="summary",
        include_relations=False,
    )


@router.get("/{entity_id}", response_model=EntityExplanation)
async def explain_entity(
    entity_id: str,
    explanation_engine: ExplanationEngine = Depends(get_explanation_engine),
) -> EntityExplanation:
    return await explanation_engine.explain_entity(
        entity_id=entity_id,
        detail_level="detailed",
        include_relations=True,
    )


@router.get("/{entity_id}/stream")
async def explain_entity_stream(
    entity_id: str,
    request: Request,
    explanation_engine: ExplanationEngine = Depends(get_explanation_engine),
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in explanation_engine.stream_explain_entity(
                entity_id=entity_id,
                include_relations=True,
            ):
                if await request.is_disconnected():
                    break
                yield _format_sse(event.event, event.model_dump(mode="json"))
        except EntityExplanationNotFoundError as error:
            yield _format_sse("error", {"message": str(error)})
        except Exception as error:  # noqa: BLE001
            yield _format_sse("error", {"message": str(error)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _format_sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


__all__ = ["router"]
