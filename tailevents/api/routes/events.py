"""Event-related API routes."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tailevents.api.dependencies import (
    AppContainer,
    get_container,
    get_ingestion_pipeline,
)
from tailevents.ingestion import IngestionPipeline
from tailevents.models.entity import CodeEntity
from tailevents.models.event import RawEvent, TailEvent


router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=TailEvent, status_code=status.HTTP_201_CREATED)
async def create_event(
    raw_event: RawEvent,
    ingestion_pipeline: IngestionPipeline = Depends(get_ingestion_pipeline),
) -> TailEvent:
    return await ingestion_pipeline.ingest(raw_event)


@router.post("/batch", response_model=list[TailEvent], status_code=status.HTTP_201_CREATED)
async def create_event_batch(
    raw_events: list[RawEvent],
    ingestion_pipeline: IngestionPipeline = Depends(get_ingestion_pipeline),
) -> list[TailEvent]:
    return await ingestion_pipeline.ingest_batch(raw_events)


@router.get("/for-entity/{entity_id}", response_model=list[TailEvent])
async def get_events_for_entity(
    entity_id: str,
    container: AppContainer = Depends(get_container),
) -> list[TailEvent]:
    entity = await _get_active_entity(container, entity_id)
    return await container.get_events_for_entity(entity)


@router.get("/{event_id}", response_model=TailEvent)
async def get_event(
    event_id: str,
    container: AppContainer = Depends(get_container),
) -> TailEvent:
    event = await container.event_store.get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("", response_model=list[TailEvent])
async def list_events(
    session: Optional[str] = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> list[TailEvent]:
    if session:
        return await container.event_store.get_by_session(session)
    return await container.event_store.get_recent()


async def _get_active_entity(container: AppContainer, entity_id: str) -> CodeEntity:
    entity = await container.entity_db.get(entity_id)
    if entity is None or entity.is_deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


__all__ = ["router"]
