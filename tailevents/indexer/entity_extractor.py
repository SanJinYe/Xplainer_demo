"""Entity extraction and synchronization."""

from dataclasses import dataclass
from typing import Any

from tailevents.models.entity import CodeEntity, EventRef, ParamInfo, RenameRecord
from tailevents.models.enums import EntityRole
from tailevents.models.event import TailEvent
from tailevents.models.protocols import EntityDBProtocol
from tailevents.indexer.ast_analyzer import ASTAnalyzer
from tailevents.indexer.rename_tracker import BODY_HASH_TAG_PREFIX, BODY_TEXT_TAG_PREFIX


@dataclass
class EntityInspection:
    file_path: str
    extracted_entities: list[dict[str, Any]]
    existing_entities: list[CodeEntity]
    appeared_entities: list[dict[str, Any]]
    disappeared_entities: list[CodeEntity]


@dataclass
class EntitySyncResult:
    created_entity_ids: list[str]
    modified_entity_ids: list[str]
    deleted_entity_ids: list[str]
    current_entities: dict[str, CodeEntity]
    previous_entities: list[CodeEntity]
    appeared_entities: list[dict[str, Any]]
    disappeared_entities: list[CodeEntity]


class EntityExtractor:
    """Wrap AST extraction and EntityDB synchronization."""

    def __init__(self, analyzer: ASTAnalyzer, entity_db: EntityDBProtocol):
        self._analyzer = analyzer
        self._entity_db = entity_db

    async def inspect(self, source: str, file_path: str) -> EntityInspection:
        extracted_entities = self._analyzer.extract_entities(source, file_path)
        existing_entities = await self._entity_db.get_by_file(file_path)

        existing_qnames = {entity.qualified_name for entity in existing_entities}
        extracted_qnames = {entity["qualified_name"] for entity in extracted_entities}

        appeared_entities = [
            entity for entity in extracted_entities if entity["qualified_name"] not in existing_qnames
        ]
        disappeared_entities = [
            entity for entity in existing_entities if entity.qualified_name not in extracted_qnames
        ]

        return EntityInspection(
            file_path=file_path,
            extracted_entities=extracted_entities,
            existing_entities=existing_entities,
            appeared_entities=appeared_entities,
            disappeared_entities=disappeared_entities,
        )

    async def sync(
        self,
        event: TailEvent,
        inspection: EntityInspection,
        rename_matches: list[dict[str, Any]],
    ) -> EntitySyncResult:
        existing_by_qname = {
            entity.qualified_name: entity for entity in inspection.existing_entities
        }
        disappeared_by_id = {
            entity.entity_id: entity for entity in inspection.disappeared_entities
        }
        rename_by_new_qname = {
            match["new_qualified_name"]: match for match in rename_matches
        }

        created_entity_ids: list[str] = []
        modified_entity_ids: list[str] = []
        deleted_entity_ids: list[str] = []
        current_entities: dict[str, CodeEntity] = {}
        handled_disappeared_ids: set[str] = set()

        for extracted in inspection.extracted_entities:
            rename_match = rename_by_new_qname.get(extracted["qualified_name"])
            if rename_match is not None:
                old_entity = disappeared_by_id.get(rename_match["old_entity_id"])
                if old_entity is not None:
                    updated_entity = CodeEntity.model_validate(
                        {
                            **old_entity.model_dump(mode="python"),
                            "name": extracted["name"],
                            "qualified_name": extracted["qualified_name"],
                            "entity_type": extracted["entity_type"],
                            "file_path": inspection.file_path,
                            "line_range": extracted["line_range"],
                            "signature": extracted["signature"],
                            "params": self._params_from_extracted(extracted),
                            "return_type": extracted["return_type"],
                            "docstring": extracted["docstring"],
                            "last_modified_event": event.event_id,
                            "last_modified_at": event.timestamp,
                            "modification_count": old_entity.modification_count + 1,
                            "is_deleted": False,
                            "deleted_by_event": None,
                            "event_refs": old_entity.event_refs
                            + [
                                EventRef(
                                    event_id=event.event_id,
                                    role=EntityRole.MODIFIED,
                                    timestamp=event.timestamp,
                                )
                            ],
                            "rename_history": old_entity.rename_history
                            + [
                                RenameRecord(
                                    old_qualified_name=old_entity.qualified_name,
                                    new_qualified_name=extracted["qualified_name"],
                                    event_id=event.event_id,
                                    timestamp=event.timestamp,
                                )
                            ],
                            "tags": self._merge_tags(old_entity.tags, extracted),
                        }
                    )
                    await self._entity_db.upsert(updated_entity)
                    modified_entity_ids.append(updated_entity.entity_id)
                    current_entities[updated_entity.qualified_name] = updated_entity
                    handled_disappeared_ids.add(old_entity.entity_id)
                    continue

            existing_entity = existing_by_qname.get(extracted["qualified_name"])
            if existing_entity is None:
                new_entity = CodeEntity(
                    name=extracted["name"],
                    qualified_name=extracted["qualified_name"],
                    entity_type=extracted["entity_type"],
                    file_path=inspection.file_path,
                    line_range=extracted["line_range"],
                    signature=extracted["signature"],
                    params=self._params_from_extracted(extracted),
                    return_type=extracted["return_type"],
                    docstring=extracted["docstring"],
                    created_at=event.timestamp,
                    created_by_event=event.event_id,
                    last_modified_event=event.event_id,
                    last_modified_at=event.timestamp,
                    event_refs=[
                        EventRef(
                            event_id=event.event_id,
                            role=EntityRole.PRIMARY,
                            timestamp=event.timestamp,
                        )
                    ],
                    tags=self._merge_tags([], extracted),
                )
                await self._entity_db.upsert(new_entity)
                created_entity_ids.append(new_entity.entity_id)
                current_entities[new_entity.qualified_name] = new_entity
                continue

            updated_entity = CodeEntity.model_validate(
                {
                    **existing_entity.model_dump(mode="python"),
                    "name": extracted["name"],
                    "qualified_name": extracted["qualified_name"],
                    "entity_type": extracted["entity_type"],
                    "file_path": inspection.file_path,
                    "line_range": extracted["line_range"],
                    "signature": extracted["signature"],
                    "params": self._params_from_extracted(extracted),
                    "return_type": extracted["return_type"],
                    "docstring": extracted["docstring"],
                    "last_modified_event": event.event_id,
                    "last_modified_at": event.timestamp,
                    "modification_count": existing_entity.modification_count + 1,
                    "is_deleted": False,
                    "deleted_by_event": None,
                    "event_refs": existing_entity.event_refs
                    + [
                        EventRef(
                            event_id=event.event_id,
                            role=EntityRole.MODIFIED,
                            timestamp=event.timestamp,
                        )
                    ],
                    "tags": self._merge_tags(existing_entity.tags, extracted),
                }
            )
            await self._entity_db.upsert(updated_entity)
            modified_entity_ids.append(updated_entity.entity_id)
            current_entities[updated_entity.qualified_name] = updated_entity

        for disappeared in inspection.disappeared_entities:
            if disappeared.entity_id in handled_disappeared_ids:
                continue
            await self._entity_db.mark_deleted(disappeared.entity_id, event.event_id)
            deleted_entity_ids.append(disappeared.entity_id)

        return EntitySyncResult(
            created_entity_ids=created_entity_ids,
            modified_entity_ids=modified_entity_ids,
            deleted_entity_ids=deleted_entity_ids,
            current_entities=current_entities,
            previous_entities=inspection.existing_entities,
            appeared_entities=inspection.appeared_entities,
            disappeared_entities=inspection.disappeared_entities,
        )

    def _params_from_extracted(self, extracted: dict[str, Any]) -> list[ParamInfo]:
        return [ParamInfo.model_validate(param) for param in extracted["params"]]

    def _merge_tags(
        self, existing_tags: list[str], extracted: dict[str, Any]
    ) -> list[str]:
        preserved_tags = [
            tag
            for tag in existing_tags
            if not tag.startswith(BODY_HASH_TAG_PREFIX)
            and not tag.startswith(BODY_TEXT_TAG_PREFIX)
        ]
        preserved_tags.append(f"{BODY_HASH_TAG_PREFIX}{extracted['body_hash']}")
        preserved_tags.append(f"{BODY_TEXT_TAG_PREFIX}{extracted['normalized_body']}")
        return preserved_tags


__all__ = [
    "EntityExtractor",
    "EntityInspection",
    "EntitySyncResult",
]
