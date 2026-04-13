"""Resolve user-provided symbols to entity identifiers."""

from tailevents.models.protocols import EntityDBProtocol


class SymbolResolver:
    """Resolve symbols using exact names, rename history, and FTS search."""

    def __init__(self, entity_db: EntityDBProtocol):
        self._entity_db = entity_db

    async def resolve(self, symbol: str) -> list[str]:
        normalized = symbol.strip()
        if not normalized:
            return []

        entities = [
            entity
            for entity in await self._entity_db.get_all()
            if not entity.is_deleted
        ]

        exact_qname = [
            entity.entity_id
            for entity in entities
            if entity.qualified_name == normalized
        ]
        if exact_qname:
            return exact_qname

        exact_name = sorted(
            (
                entity
                for entity in entities
                if entity.name == normalized
            ),
            key=lambda entity: entity.qualified_name,
        )
        if exact_name:
            return [entity.entity_id for entity in exact_name]

        rename_matches = sorted(
            (
                entity
                for entity in entities
                if any(
                    record.old_qualified_name == normalized
                    for record in entity.rename_history
                )
            ),
            key=lambda entity: entity.qualified_name,
        )
        if rename_matches:
            return [entity.entity_id for entity in rename_matches]

        search_matches = await self._entity_db.search(normalized)
        return [entity.entity_id for entity in search_matches]


__all__ = ["SymbolResolver"]
