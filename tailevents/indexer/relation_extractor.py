"""Relation extraction and synchronization."""

from dataclasses import dataclass
from typing import Optional

from tailevents.models.enums import Provenance, RelationType
from tailevents.models.protocols import RelationStoreProtocol
from tailevents.models.relation import Relation
from tailevents.indexer.ast_analyzer import ASTAnalyzer


@dataclass
class RelationSyncResult:
    relation_ids: list[str]
    impacted_entity_ids: list[str]
    graph_changed: bool


class RelationExtractor:
    """Wrap AST relation extraction and RelationStore synchronization."""

    def __init__(self, analyzer: ASTAnalyzer, relation_store: RelationStoreProtocol):
        self._analyzer = analyzer
        self._relation_store = relation_store

    async def refresh(
        self,
        source: str,
        file_path: str,
        known_entities: dict[str, str],
        source_entity_ids_to_refresh: list[str],
        event_id: str,
        entity_files: Optional[dict[str, str]] = None,
    ) -> RelationSyncResult:
        entity_files = entity_files or {}
        impacted_entity_ids: list[str] = []
        previous_relation_keys: set[tuple[str, str, str]] = set()
        for entity_id in dict.fromkeys(source_entity_ids_to_refresh):
            previous_relations = await self._relation_store.get_outgoing(entity_id)
            impacted_entity_ids.append(entity_id)
            impacted_entity_ids.extend(
                relation.target for relation in previous_relations if relation.is_active
            )
            previous_relation_keys.update(
                (
                    relation.source,
                    relation.target,
                    relation.relation_type.value,
                )
                for relation in previous_relations
                if relation.is_active
            )
            await self._relation_store.deactivate_by_source(entity_id)

        extracted_relations = self._analyzer.extract_relations(
            source=source,
            file_path=file_path,
            known_entities=known_entities,
            entity_files=entity_files,
        )

        relation_ids: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        current_relation_keys: set[tuple[str, str, str]] = set()
        for relation in extracted_relations:
            source_qname = relation["source_qname"]
            target_qname = relation["target_qname"]
            source_id = known_entities.get(source_qname)
            target_id = known_entities.get(target_qname)
            if source_id is None or target_id is None:
                continue

            dedupe_key = (source_id, target_id, relation["relation_type"])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            current_relation_keys.add(dedupe_key)

            relation_record = Relation(
                source=source_id,
                target=target_id,
                relation_type=RelationType(relation["relation_type"]),
                provenance=Provenance.AST_DERIVED,
                confidence=1.0,
                from_event=event_id,
            )
            relation_ids.append(await self._relation_store.put(relation_record))
            impacted_entity_ids.extend([source_id, target_id])

        return RelationSyncResult(
            relation_ids=relation_ids,
            impacted_entity_ids=list(dict.fromkeys(impacted_entity_ids)),
            graph_changed=previous_relation_keys != current_relation_keys,
        )


__all__ = ["RelationExtractor", "RelationSyncResult"]
