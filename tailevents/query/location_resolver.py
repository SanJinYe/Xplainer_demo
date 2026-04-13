"""Resolve source code locations to entity identifiers."""

from typing import Optional

from tailevents.models.protocols import EntityDBProtocol


class LocationResolver:
    """Resolve a file path and line number to the innermost entity."""

    def __init__(self, entity_db: EntityDBProtocol):
        self._entity_db = entity_db

    async def resolve(self, file_path: str, line_number: int) -> Optional[str]:
        entities = await self._entity_db.get_by_file(file_path)
        matches = []
        for entity in entities:
            if entity.line_range is None:
                continue
            start, end = entity.line_range
            if start <= line_number <= end:
                matches.append(entity)

        if not matches:
            return None

        innermost = min(
            matches,
            key=lambda entity: (
                (entity.line_range[1] - entity.line_range[0]),
                -entity.line_range[0],
            ),
        )
        return innermost.entity_id


__all__ = ["LocationResolver"]
