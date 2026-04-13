"""Entity-related API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query

from tailevents.api.dependencies import AppContainer, get_container
from tailevents.models.entity import CodeEntity
from tailevents.query import LocationResolver


router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("", response_model=list[CodeEntity])
async def list_entities(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    container: AppContainer = Depends(get_container),
) -> list[CodeEntity]:
    entities = [
        entity
        for entity in await container.entity_db.get_all()
        if not entity.is_deleted
    ]
    return entities[skip : skip + limit]


@router.get("/search", response_model=list[CodeEntity])
async def search_entities(
    q: str = Query(default=""),
    container: AppContainer = Depends(get_container),
) -> list[CodeEntity]:
    if not q.strip():
        return []
    return await container.entity_db.search(q)


@router.get("/by-location", response_model=CodeEntity)
async def get_entity_by_location(
    file: str = Query(...),
    line: int = Query(..., ge=1),
    container: AppContainer = Depends(get_container),
) -> CodeEntity:
    resolver = LocationResolver(container.entity_db)
    entity_id = await resolver.resolve(file, line)
    if entity_id is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    entity = await container.entity_db.get(entity_id)
    if entity is None or entity.is_deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.get("/{entity_id}", response_model=CodeEntity)
async def get_entity(
    entity_id: str,
    container: AppContainer = Depends(get_container),
) -> CodeEntity:
    entity = await container.entity_db.get(entity_id)
    if entity is None or entity.is_deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


__all__ = ["router"]
