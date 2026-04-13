"""SQLite-backed event store."""

import json
from typing import Any, Optional

from tailevents.models.event import EntityRef, ExternalRef, TailEvent
from tailevents.models.protocols import EventStoreProtocol
from tailevents.storage.database import SQLiteConnectionManager
from tailevents.storage.exceptions import EventEnrichmentError, RecordNotFoundError


class SQLiteEventStore(EventStoreProtocol):
    """Persist TailEvents in SQLite."""

    def __init__(self, database: SQLiteConnectionManager):
        self._database = database

    async def put(self, event: TailEvent) -> str:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO events (
                    event_id,
                    timestamp,
                    session_id,
                    agent_step_id,
                    action_type,
                    file_path,
                    line_range_start,
                    line_range_end,
                    code_snapshot,
                    intent,
                    reasoning,
                    decision_alternatives,
                    entity_refs,
                    external_refs
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.session_id,
                    event.agent_step_id,
                    event.action_type.value,
                    event.file_path,
                    self._line_start(event.line_range),
                    self._line_end(event.line_range),
                    event.code_snapshot,
                    event.intent,
                    event.reasoning,
                    self._serialize_value(event.decision_alternatives),
                    None
                    if not event.entity_refs
                    else self._serialize_models(event.entity_refs),
                    self._serialize_models(event.external_refs),
                ),
            )
            await connection.commit()
        return event.event_id

    async def enrich(self, event_id: str, entity_refs: list[EntityRef]) -> None:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE events
                SET entity_refs = ?
                WHERE event_id = ? AND entity_refs IS NULL
                """,
                (self._serialize_models(entity_refs), event_id),
            )
            await connection.commit()
            if cursor.rowcount == 0:
                row = await self._fetchone(
                    connection,
                    "SELECT event_id, entity_refs FROM events WHERE event_id = ?",
                    (event_id,),
                )
                if row is None:
                    raise RecordNotFoundError(f"Event not found: {event_id}")
                raise EventEnrichmentError(
                    f"Event entity_refs already enriched: {event_id}"
                )

    async def get(self, event_id: str) -> Optional[TailEvent]:
        async with self._database.connection() as connection:
            row = await self._fetchone(
                connection, "SELECT * FROM events WHERE event_id = ?", (event_id,)
            )
        return self._row_to_event(row)

    async def get_batch(self, event_ids: list[str]) -> list[TailEvent]:
        if not event_ids:
            return []
        placeholders = ", ".join("?" for _ in event_ids)
        async with self._database.connection() as connection:
            rows = await self._fetchall(
                connection,
                f"SELECT * FROM events WHERE event_id IN ({placeholders})",
                tuple(event_ids),
            )
        events_by_id = {}
        for row in rows:
            event = self._row_to_event(row)
            if event is not None:
                events_by_id[event.event_id] = event
        return [events_by_id[event_id] for event_id in event_ids if event_id in events_by_id]

    async def get_by_session(self, session_id: str) -> list[TailEvent]:
        async with self._database.connection() as connection:
            rows = await self._fetchall(
                connection,
                """
                SELECT * FROM events
                WHERE session_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id,),
            )
        return self._rows_to_events(rows)

    async def get_by_file(self, file_path: str) -> list[TailEvent]:
        async with self._database.connection() as connection:
            rows = await self._fetchall(
                connection,
                """
                SELECT * FROM events
                WHERE file_path = ?
                ORDER BY timestamp ASC
                """,
                (file_path,),
            )
        return self._rows_to_events(rows)

    async def get_recent(self, limit: int = 50) -> list[TailEvent]:
        async with self._database.connection() as connection:
            rows = await self._fetchall(
                connection,
                """
                SELECT * FROM events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
        return self._rows_to_events(rows)

    async def count(self) -> int:
        async with self._database.connection() as connection:
            row = await self._fetchone(connection, "SELECT COUNT(*) AS count FROM events")
        return 0 if row is None else int(row["count"])

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

    def _row_to_event(self, row: Any) -> Optional[TailEvent]:
        if row is None:
            return None
        payload = {
            "event_id": row["event_id"],
            "timestamp": row["timestamp"],
            "session_id": row["session_id"],
            "agent_step_id": row["agent_step_id"],
            "action_type": row["action_type"],
            "file_path": row["file_path"],
            "line_range": self._build_line_range(
                row["line_range_start"], row["line_range_end"]
            ),
            "code_snapshot": row["code_snapshot"],
            "intent": row["intent"],
            "reasoning": row["reasoning"],
            "decision_alternatives": self._deserialize_value(
                row["decision_alternatives"]
            ),
            "entity_refs": self._deserialize_models(row["entity_refs"], EntityRef),
            "external_refs": self._deserialize_models(row["external_refs"], ExternalRef),
        }
        return TailEvent.model_validate(payload)

    def _rows_to_events(self, rows: list[Any]) -> list[TailEvent]:
        return [event for event in (self._row_to_event(row) for row in rows) if event]

    def _serialize_models(self, values: list[Any]) -> str:
        return json.dumps([value.model_dump(mode="json") for value in values])

    def _serialize_value(self, value: Optional[Any]) -> Optional[str]:
        if value is None:
            return None
        return json.dumps(value)

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


__all__ = ["SQLiteEventStore"]
