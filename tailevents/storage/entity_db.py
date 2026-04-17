"""SQLite-backed entity database."""

import json
from typing import Any, Optional

from tailevents.models.entity import CodeEntity, EventRef, ParamInfo, RenameRecord
from tailevents.models.protocols import EntityDBProtocol
from tailevents.storage.database import SQLiteConnectionManager
from tailevents.storage.exceptions import RecordNotFoundError


class SQLiteEntityDB(EntityDBProtocol):
    """Persist CodeEntity records in SQLite."""

    def __init__(self, database: SQLiteConnectionManager):
        self._database = database

    async def upsert(self, entity: CodeEntity) -> str:
        async with self._database.connection() as connection:
            existing_rowid = await self._get_entity_rowid(connection, entity.entity_id)
            if existing_rowid is not None:
                await connection.execute(
                    "DELETE FROM entity_search WHERE rowid = ?", (existing_rowid,)
                )

            await connection.execute(
                """
                INSERT INTO entities (
                    entity_id,
                    name,
                    qualified_name,
                    entity_type,
                    file_path,
                    line_range_start,
                    line_range_end,
                    signature,
                    params,
                    return_type,
                    docstring,
                    created_at,
                    created_by_event,
                    last_modified_event,
                    last_modified_at,
                    modification_count,
                    is_deleted,
                    deleted_by_event,
                    event_refs,
                    rename_history,
                    is_external,
                    package,
                    cached_description,
                    description_valid,
                    in_degree,
                    out_degree,
                    tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    name = excluded.name,
                    qualified_name = excluded.qualified_name,
                    entity_type = excluded.entity_type,
                    file_path = excluded.file_path,
                    line_range_start = excluded.line_range_start,
                    line_range_end = excluded.line_range_end,
                    signature = excluded.signature,
                    params = excluded.params,
                    return_type = excluded.return_type,
                    docstring = excluded.docstring,
                    created_at = excluded.created_at,
                    created_by_event = excluded.created_by_event,
                    last_modified_event = excluded.last_modified_event,
                    last_modified_at = excluded.last_modified_at,
                    modification_count = excluded.modification_count,
                    is_deleted = excluded.is_deleted,
                    deleted_by_event = excluded.deleted_by_event,
                    event_refs = excluded.event_refs,
                    rename_history = excluded.rename_history,
                    is_external = excluded.is_external,
                    package = excluded.package,
                    cached_description = excluded.cached_description,
                    description_valid = excluded.description_valid,
                    in_degree = excluded.in_degree,
                    out_degree = excluded.out_degree,
                    tags = excluded.tags
                """,
                (
                    entity.entity_id,
                    entity.name,
                    entity.qualified_name,
                    entity.entity_type.value,
                    entity.file_path,
                    self._line_start(entity.line_range),
                    self._line_end(entity.line_range),
                    entity.signature,
                    self._serialize_models(entity.params),
                    entity.return_type,
                    entity.docstring,
                    entity.created_at.isoformat(),
                    entity.created_by_event,
                    entity.last_modified_event,
                    self._serialize_datetime(entity.last_modified_at),
                    entity.modification_count,
                    int(entity.is_deleted),
                    entity.deleted_by_event,
                    self._serialize_models(entity.event_refs),
                    self._serialize_models(entity.rename_history),
                    int(entity.is_external),
                    entity.package,
                    entity.cached_description,
                    int(entity.description_valid),
                    entity.in_degree,
                    entity.out_degree,
                    self._serialize_value(entity.tags),
                ),
            )
            await self._sync_search_index(connection, entity.entity_id)
            await connection.commit()
        return entity.entity_id

    async def get(self, entity_id: str) -> Optional[CodeEntity]:
        async with self._database.connection() as connection:
            row = await self._fetchone(
                connection, "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)
            )
        return self._row_to_entity(row)

    async def get_by_qualified_name(self, qname: str) -> Optional[CodeEntity]:
        async with self._database.connection() as connection:
            row = await self._fetchone(
                connection,
                """
                SELECT * FROM entities
                WHERE qualified_name = ? AND is_deleted = 0
                LIMIT 1
                """,
                (qname,),
            )
            if row is not None:
                return self._row_to_entity(row)
            rows = await self._fetchall(
                connection, "SELECT * FROM entities WHERE is_deleted = 0"
            )

        for row in rows:
            entity = self._row_to_entity(row)
            if entity is None:
                continue
            for record in entity.rename_history:
                if record.old_qualified_name == qname or record.new_qualified_name == qname:
                    return entity
        return None

    async def get_by_name(self, name: str) -> list[CodeEntity]:
        async with self._database.connection() as connection:
            rows = await self._fetchall(
                connection,
                """
                SELECT * FROM entities
                WHERE name = ? AND is_deleted = 0
                ORDER BY qualified_name ASC
                """,
                (name,),
            )
        return self._rows_to_entities(rows)

    async def get_by_file(self, file_path: str) -> list[CodeEntity]:
        async with self._database.connection() as connection:
            rows = await self._fetchall(
                connection,
                """
                SELECT * FROM entities
                WHERE file_path = ? AND is_deleted = 0
                ORDER BY qualified_name ASC
                """,
                (file_path,),
            )
        return self._rows_to_entities(rows)

    async def search(self, query: str) -> list[CodeEntity]:
        if not query.strip():
            return []
        async with self._database.connection() as connection:
            rows = await self._fetchall(
                connection,
                """
                SELECT e.*
                FROM entity_search s
                JOIN entities e ON e.rowid = s.rowid
                WHERE entity_search MATCH ? AND e.is_deleted = 0
                ORDER BY bm25(entity_search), e.qualified_name ASC
                """,
                (query,),
            )
        return self._rows_to_entities(rows)

    async def get_all(self) -> list[CodeEntity]:
        async with self._database.connection() as connection:
            rows = await self._fetchall(
                connection,
                """
                SELECT * FROM entities
                ORDER BY qualified_name ASC
                """,
            )
        return self._rows_to_entities(rows)

    async def mark_deleted(self, entity_id: str, event_id: str) -> None:
        async with self._database.connection() as connection:
            rowid = await self._get_entity_rowid(connection, entity_id)
            cursor = await connection.execute(
                """
                UPDATE entities
                SET is_deleted = 1, deleted_by_event = ?
                WHERE entity_id = ?
                """,
                (event_id, entity_id),
            )
            if cursor.rowcount == 0:
                raise RecordNotFoundError(f"Entity not found: {entity_id}")
            if rowid is not None:
                await connection.execute(
                    "DELETE FROM entity_search WHERE rowid = ?", (rowid,)
                )
            await connection.commit()

    async def update_description(self, entity_id: str, desc: str) -> None:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE entities
                SET cached_description = ?, description_valid = 1
                WHERE entity_id = ?
                """,
                (desc, entity_id),
            )
            if cursor.rowcount == 0:
                raise RecordNotFoundError(f"Entity not found: {entity_id}")
            await self._sync_search_index(connection, entity_id)
            await connection.commit()

    async def invalidate_description(self, entity_id: str) -> None:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE entities
                SET description_valid = 0
                WHERE entity_id = ?
                """,
                (entity_id,),
            )
            if cursor.rowcount == 0:
                raise RecordNotFoundError(f"Entity not found: {entity_id}")
            await connection.commit()

    async def invalidate_description_and_cache_prefix(
        self,
        entity_id: str,
        cache_prefix: str,
    ) -> None:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE entities
                SET description_valid = 0
                WHERE entity_id = ?
                """,
                (entity_id,),
            )
            if cursor.rowcount == 0:
                raise RecordNotFoundError(f"Entity not found: {entity_id}")
            await connection.execute(
                """
                UPDATE explanation_cache
                SET is_valid = 0
                WHERE cache_key LIKE ?
                """,
                (f"{cache_prefix}%",),
            )
            await connection.commit()

    async def count(self) -> int:
        async with self._database.connection() as connection:
            row = await self._fetchone(
                connection, "SELECT COUNT(*) AS count FROM entities"
            )
        return 0 if row is None else int(row["count"])

    async def _sync_search_index(self, connection, entity_id: str) -> None:
        row = await self._fetchone(
            connection,
            """
            SELECT rowid, name, qualified_name, cached_description, is_deleted
            FROM entities
            WHERE entity_id = ?
            """,
            (entity_id,),
        )
        if row is None:
            return
        await connection.execute("DELETE FROM entity_search WHERE rowid = ?", (row["rowid"],))
        if int(row["is_deleted"]) == 0:
            await connection.execute(
                """
                INSERT INTO entity_search (rowid, name, qualified_name, cached_description)
                VALUES (?, ?, ?, ?)
                """,
                (
                    row["rowid"],
                    row["name"],
                    row["qualified_name"],
                    row["cached_description"] or "",
                ),
            )

    async def _get_entity_rowid(self, connection, entity_id: str) -> Optional[int]:
        row = await self._fetchone(
            connection, "SELECT rowid FROM entities WHERE entity_id = ?", (entity_id,)
        )
        return None if row is None else int(row["rowid"])

    async def _fetchone(self, connection, query: str, params: tuple[Any, ...] = ()):
        cursor = await connection.execute(query, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def _fetchall(self, connection, query: str, params: tuple[Any, ...] = ()):
        cursor = await connection.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

    def _row_to_entity(self, row: Any) -> Optional[CodeEntity]:
        if row is None:
            return None
        payload = {
            "entity_id": row["entity_id"],
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "entity_type": row["entity_type"],
            "file_path": row["file_path"],
            "line_range": self._build_line_range(
                row["line_range_start"], row["line_range_end"]
            ),
            "signature": row["signature"],
            "params": self._deserialize_models(row["params"], ParamInfo),
            "return_type": row["return_type"],
            "docstring": row["docstring"],
            "created_at": row["created_at"],
            "created_by_event": row["created_by_event"],
            "last_modified_event": row["last_modified_event"],
            "last_modified_at": row["last_modified_at"],
            "modification_count": row["modification_count"],
            "is_deleted": bool(row["is_deleted"]),
            "deleted_by_event": row["deleted_by_event"],
            "event_refs": self._deserialize_models(row["event_refs"], EventRef),
            "rename_history": self._deserialize_models(
                row["rename_history"], RenameRecord
            ),
            "is_external": bool(row["is_external"]),
            "package": row["package"],
            "cached_description": row["cached_description"],
            "description_valid": bool(row["description_valid"]),
            "in_degree": row["in_degree"],
            "out_degree": row["out_degree"],
            "tags": self._deserialize_value(row["tags"]) or [],
        }
        return CodeEntity.model_validate(payload)

    def _rows_to_entities(self, rows: list[Any]) -> list[CodeEntity]:
        return [entity for entity in (self._row_to_entity(row) for row in rows) if entity]

    def _serialize_models(self, values: list[Any]) -> str:
        return json.dumps([value.model_dump(mode="json") for value in values])

    def _serialize_value(self, value: Optional[Any]) -> Optional[str]:
        if value is None:
            return None
        return json.dumps(value)

    def _serialize_datetime(self, value: Optional[Any]) -> Optional[str]:
        if value is None:
            return None
        return value.isoformat()

    def _deserialize_value(self, value: Optional[str]) -> Optional[Any]:
        if value is None:
            return None
        return json.loads(value)

    def _deserialize_models(self, value: Optional[str], model_type) -> list[Any]:
        if value is None:
            return []
        return [model_type.model_validate(item) for item in json.loads(value)]

    def _build_line_range(
        self, line_start: Optional[int], line_end: Optional[int]
    ) -> Optional[tuple[int, int]]:
        if line_start is None or line_end is None:
            return None
        return (int(line_start), int(line_end))

    def _line_start(self, line_range: Optional[tuple[int, int]]) -> Optional[int]:
        return None if line_range is None else line_range[0]

    def _line_end(self, line_range: Optional[tuple[int, int]]) -> Optional[int]:
        return None if line_range is None else line_range[1]


__all__ = ["SQLiteEntityDB"]
