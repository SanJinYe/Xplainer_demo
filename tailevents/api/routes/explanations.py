"""Explanation-related API routes."""

from fastapi import APIRouter, Depends

from tailevents.api.dependencies import (
    AppContainer,
    get_container,
    get_explanation_engine,
    get_query_router,
)
from tailevents.explanation import ExplanationEngine
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


__all__ = ["router"]
