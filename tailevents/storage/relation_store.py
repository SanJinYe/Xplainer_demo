"""SQLite-backed relation store."""

from typing import Any, Optional

from tailevents.models.protocols import RelationStoreProtocol
from tailevents.models.relation import Relation
from tailevents.storage.database import SQLiteConnectionManager


class SQLiteRelationStore(RelationStoreProtocol):
    """Persist Relation records in SQLite."""

    def __init__(self, database: SQLiteConnectionManager):
        self._database = database

    async def put(self, relation: Relation) -> str:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO relations (
                    relation_id,
                    source,
                    target,
                    relation_type,
                    provenance,
                    confidence,
                    from_event,
                    context,
                    created_at,
                    is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation.relation_id,
                    relation.source,
                    relation.target,
                    relation.relation_type.value,
                    relation.provenance.value,
                    relation.confidence,
                    relation.from_event,
                    relation.context,
                    relation.created_at.isoformat(),
                    int(relation.is_active),
                ),
            )
            await connection.commit()
        return relation.relation_id

    async def get_outgoing(self, entity_id: str) -> list[Relation]:
        return await self._list_relations(
            """
            SELECT * FROM relations
            WHERE source = ? AND is_active = 1
            ORDER BY created_at ASC
            """,
            (entity_id,),
        )

    async def get_incoming(self, entity_id: str) -> list[Relation]:
        return await self._list_relations(
            """
            SELECT * FROM relations
            WHERE target = ? AND is_active = 1
            ORDER BY created_at ASC
            """,
            (entity_id,),
        )

    async def get_between(self, source: str, target: str) -> list[Relation]:
        return await self._list_relations(
            """
            SELECT * FROM relations
            WHERE source = ? AND target = ? AND is_active = 1
            ORDER BY created_at ASC
            """,
            (source, target),
        )

    async def get_by_event(self, event_id: str) -> list[Relation]:
        return await self._list_relations(
            """
            SELECT * FROM relations
            WHERE from_event = ?
            ORDER BY created_at ASC
            """,
            (event_id,),
        )

    async def deactivate_by_source(self, entity_id: str) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                UPDATE relations
                SET is_active = 0
                WHERE source = ?
                """,
                (entity_id,),
            )
            await connection.commit()

    async def get_all_active(self) -> list[Relation]:
        return await self._list_relations(
            """
            SELECT * FROM relations
            WHERE is_active = 1
            ORDER BY created_at ASC
            """
        )

    async def count(self) -> int:
        async with self._database.connection() as connection:
            row = await self._fetchone(
                connection, "SELECT COUNT(*) AS count FROM relations"
            )
        return 0 if row is None else int(row["count"])

    async def _list_relations(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> list[Relation]:
        async with self._database.connection() as connection:
            rows = await self._fetchall(connection, query, params)
        return [
            relation
            for relation in (self._row_to_relation(row) for row in rows)
            if relation
        ]

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

    def _row_to_relation(self, row: Any) -> Optional[Relation]:
        if row is None:
            return None
        payload = {
            "relation_id": row["relation_id"],
            "source": row["source"],
            "target": row["target"],
            "relation_type": row["relation_type"],
            "provenance": row["provenance"],
            "confidence": row["confidence"],
            "from_event": row["from_event"],
            "context": row["context"],
            "created_at": row["created_at"],
            "is_active": bool(row["is_active"]),
        }
        return Relation.model_validate(payload)


__all__ = ["SQLiteRelationStore"]
