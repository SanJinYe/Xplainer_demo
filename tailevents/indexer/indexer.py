"""Main indexer orchestration."""

import ast
from dataclasses import dataclass
from typing import Optional

from tailevents.models.enums import EntityRole
from tailevents.models.event import TailEvent
from tailevents.models.protocols import CacheProtocol, EntityDBProtocol, IndexerProtocol, RelationStoreProtocol
from tailevents.indexer.ast_analyzer import ASTAnalyzer
from tailevents.indexer.diff_parser import DiffParser
from tailevents.indexer.entity_extractor import EntityExtractor
from tailevents.indexer.pending_queue import PendingQueue
from tailevents.indexer.relation_extractor import RelationExtractor
from tailevents.indexer.rename_tracker import RenameTracker


@dataclass
class IndexerResultData:
    entities_created: list[str]
    entities_modified: list[str]
    entities_deleted: list[str]
    relations_created: list[str]
    pending: bool


class Indexer(IndexerProtocol):
    """Coordinate diff parsing, AST analysis, entity sync, and relation sync."""

    def __init__(
        self,
        entity_db: EntityDBProtocol,
        relation_store: RelationStoreProtocol,
        cache: Optional[CacheProtocol] = None,
        rename_similarity_threshold: float = 0.8,
    ):
        self._entity_db = entity_db
        self._relation_store = relation_store
        self._cache = cache
        self._ast_analyzer = ASTAnalyzer()
        self._diff_parser = DiffParser()
        self._rename_tracker = RenameTracker(
            similarity_threshold=rename_similarity_threshold
        )
        self._entity_extractor = EntityExtractor(self._ast_analyzer, self._entity_db)
        self._relation_extractor = RelationExtractor(
            self._ast_analyzer, self._relation_store
        )
        self._pending_queue = PendingQueue()

    @property
    def pending_queue(self) -> PendingQueue:
        return self._pending_queue

    async def process_event(self, event: TailEvent) -> IndexerResultData:
        return await self._process_event(
            event,
            retry_pending=True,
            enqueue_on_failure=True,
        )

    async def _process_event(
        self,
        event: TailEvent,
        retry_pending: bool,
        enqueue_on_failure: bool,
    ) -> IndexerResultData:
        parsed_changes = self._diff_parser.parse(event.code_snapshot, event.file_path)
        selected_change = self._select_change(parsed_changes, event.file_path)
        source = selected_change["source"]
        file_path = selected_change["file_path"] or event.file_path

        try:
            ast.parse(source)
        except SyntaxError:
            if enqueue_on_failure:
                self._pending_queue.add(event)
            return IndexerResultData([], [], [], [], True)

        inspection = await self._entity_extractor.inspect(source, file_path)
        rename_matches = self._rename_tracker.detect_renames(
            disappeared=inspection.disappeared_entities,
            appeared=inspection.appeared_entities,
        )
        entity_result = await self._entity_extractor.sync(
            event=event,
            inspection=inspection,
            rename_matches=rename_matches,
        )

        all_entities = [entity for entity in await self._entity_db.get_all() if not entity.is_deleted]
        known_entities = {
            entity.qualified_name: entity.entity_id for entity in all_entities
        }
        relation_result = await self._relation_extractor.refresh(
            source=source,
            file_path=file_path,
            known_entities=known_entities,
            source_entity_ids_to_refresh=[
                entity.entity_id for entity in inspection.existing_entities
            ],
            event_id=event.event_id,
        )

        await self._invalidate_cache(
            entity_result.created_entity_ids
            + entity_result.modified_entity_ids
            + entity_result.deleted_entity_ids
        )

        if retry_pending:
            await self._pending_queue.retry_all(self)

        return IndexerResultData(
            entities_created=entity_result.created_entity_ids,
            entities_modified=entity_result.modified_entity_ids,
            entities_deleted=entity_result.deleted_entity_ids,
            relations_created=relation_result.relation_ids,
            pending=False,
        )

    async def _invalidate_cache(self, entity_ids: list[str]) -> None:
        if self._cache is None:
            return
        for entity_id in dict.fromkeys(entity_ids):
            await self._cache.invalidate_prefix(f"explanation:{entity_id}:")

    def _select_change(
        self, parsed_changes: list[dict], event_file_path: str
    ) -> dict:
        for change in parsed_changes:
            if change.get("file_path") == event_file_path:
                return change
        if parsed_changes:
            return parsed_changes[0]
        return {
            "file_path": event_file_path,
            "source": "",
            "added_lines": [],
            "removed_lines": [],
            "modified_lines": [],
            "is_diff": False,
        }


__all__ = ["Indexer", "IndexerResultData"]
