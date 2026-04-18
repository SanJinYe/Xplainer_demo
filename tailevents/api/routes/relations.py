"""Relation-related API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query

from tailevents.api.dependencies import AppContainer, get_container, get_graph_service
from tailevents.graph import GraphService
from tailevents.models.graph import GlobalImpactPath, GraphSubgraph
from tailevents.models.relation import Relation


router = APIRouter(prefix="/relations", tags=["relations"])


@router.get("/{entity_id}/outgoing", response_model=list[Relation])
async def get_outgoing_relations(
    entity_id: str,
    container: AppContainer = Depends(get_container),
) -> list[Relation]:
    await _ensure_active_entity(container, entity_id)
    return await container.relation_store.get_outgoing(entity_id)


@router.get("/{entity_id}/incoming", response_model=list[Relation])
async def get_incoming_relations(
    entity_id: str,
    container: AppContainer = Depends(get_container),
) -> list[Relation]:
    await _ensure_active_entity(container, entity_id)
    return await container.relation_store.get_incoming(entity_id)


@router.get("/{entity_id}/subgraph", response_model=GraphSubgraph)
async def get_subgraph(
    entity_id: str,
    depth: int = Query(default=2, ge=1, le=2),
    container: AppContainer = Depends(get_container),
    graph_service: GraphService = Depends(get_graph_service),
) -> GraphSubgraph:
    await _ensure_active_entity(container, entity_id)
    return await graph_service.get_subgraph(entity_id, depth=depth)


@router.get("/{entity_id}/impact-paths", response_model=list[GlobalImpactPath])
async def get_impact_paths(
    entity_id: str,
    direction: str = Query(default="both", pattern="^(upstream|downstream|both)$"),
    limit: int = Query(default=3, ge=1, le=5),
    container: AppContainer = Depends(get_container),
    graph_service: GraphService = Depends(get_graph_service),
) -> list[GlobalImpactPath]:
    await _ensure_active_entity(container, entity_id)
    return await graph_service.get_impact_paths(
        entity_id,
        direction=direction,
        limit=limit,
    )


async def _ensure_active_entity(container: AppContainer, entity_id: str) -> None:
    entity = await container.entity_db.get(entity_id)
    if entity is None or entity.is_deleted:
        raise HTTPException(status_code=404, detail="Entity not found")


__all__ = ["router"]
