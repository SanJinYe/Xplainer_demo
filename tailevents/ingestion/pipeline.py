"""Main ingestion pipeline."""

import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence

from tailevents.ingestion.hooks import IngestionHook
from tailevents.ingestion.validator import RawEventValidator
from tailevents.models.enums import EntityRole
from tailevents.models.event import EntityRef, ExternalRef, RawEvent, TailEvent
from tailevents.models.protocols import EventStoreProtocol, IndexerProtocol


logger = logging.getLogger(__name__)


@dataclass
class PipelineIndexerResult:
    """Concrete result used when the indexer fails or is replayed."""

    entities_created: list[str] = field(default_factory=list)
    entities_modified: list[str] = field(default_factory=list)
    entities_deleted: list[str] = field(default_factory=list)
    relations_created: list[str] = field(default_factory=list)
    external_refs: list[ExternalRef] = field(default_factory=list)
    graph_changed: bool = False
    pending: bool = False


class IngestionPipeline:
    """Validate, persist, index, and enrich TailEvents."""

    def __init__(
        self,
        event_store: EventStoreProtocol,
        indexer: IndexerProtocol,
        validator: Optional[RawEventValidator] = None,
        hooks: Optional[Sequence[IngestionHook]] = None,
    ):
        self._event_store = event_store
        self._indexer = indexer
        self._validator = validator or RawEventValidator()
        self._hooks = list(hooks or [])

    async def ingest(self, raw_event: RawEvent) -> TailEvent:
        """Ingest a new raw event end-to-end."""

        normalized = self._validator.normalize(raw_event)
        event = TailEvent(**normalized.model_dump(mode="python"))
        enriched_event, _ = await self._persist_and_index(event)
        return enriched_event

    async def ingest_batch(self, raw_events: list[RawEvent]) -> list[TailEvent]:
        """Ingest raw events sequentially while preserving order."""

        events: list[TailEvent] = []
        for raw_event in raw_events:
            events.append(await self.ingest(raw_event))
        return events

    async def reindex_stored_event(
        self, event: TailEvent
    ) -> tuple[TailEvent, PipelineIndexerResult]:
        """Re-run indexing and enrichment for an already stored event."""

        return await self._index_existing_event(event)

    async def _persist_and_index(
        self, event: TailEvent
    ) -> tuple[TailEvent, PipelineIndexerResult]:
        await self._event_store.put(event)
        return await self._index_existing_event(event)

    async def _index_existing_event(
        self, event: TailEvent
    ) -> tuple[TailEvent, PipelineIndexerResult]:
        result = await self._run_indexer(event)
        enriched_event = event

        if not result.pending:
            entity_refs = self._build_entity_refs(result)
            await self._event_store.enrich(
                event.event_id,
                entity_refs,
                result.external_refs,
            )
            enriched_event = event.model_copy(
                update={
                    "entity_refs": entity_refs,
                    "external_refs": result.external_refs or event.external_refs,
                }
            )

        await self._run_hooks(enriched_event, result)
        return enriched_event, result

    async def _run_indexer(self, event: TailEvent) -> PipelineIndexerResult:
        try:
            result = await self._indexer.process_event(event)
        except Exception:
            logger.exception("Indexer failed for event %s", event.event_id)
            return PipelineIndexerResult(pending=True)

        return PipelineIndexerResult(
            entities_created=list(result.entities_created),
            entities_modified=list(result.entities_modified),
            entities_deleted=list(result.entities_deleted),
            relations_created=list(result.relations_created),
            external_refs=list(result.external_refs),
            graph_changed=bool(result.graph_changed),
            pending=bool(result.pending),
        )

    async def _run_hooks(self, event: TailEvent, result: PipelineIndexerResult) -> None:
        for hook in self._hooks:
            await hook.on_event_ingested(event, result)

    def _build_entity_refs(self, result: PipelineIndexerResult) -> list[EntityRef]:
        entity_refs: list[EntityRef] = []
        seen: set[str] = set()

        for entity_id in result.entities_created:
            if entity_id in seen:
                continue
            seen.add(entity_id)
            entity_refs.append(EntityRef(entity_id=entity_id, role=EntityRole.PRIMARY))

        for entity_id in result.entities_modified + result.entities_deleted:
            if entity_id in seen:
                continue
            seen.add(entity_id)
            entity_refs.append(EntityRef(entity_id=entity_id, role=EntityRole.MODIFIED))

        return entity_refs


__all__ = ["IngestionPipeline", "PipelineIndexerResult"]
