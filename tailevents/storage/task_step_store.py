"""SQLite-backed store for coding task step events."""

from typing import Any

from tailevents.models.protocols import TaskStepStoreProtocol
from tailevents.models.task import TaskStepEvent
from tailevents.storage.database import SQLiteConnectionManager


class SQLiteTaskStepStore(TaskStepStoreProtocol):
    """Persist task step events in SQLite."""

    def __init__(self, database: SQLiteConnectionManager):
        self._database = database

    async def put(self, event: TaskStepEvent) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO task_step_events (
                    task_id,
                    step_id,
                    step_kind,
                    status,
                    file_path,
                    content_hash,
                    intent,
                    reasoning_summary,
                    tool_name,
                    input_summary,
                    output_summary,
                    timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.task_id,
                    event.step_id,
                    event.step_kind,
                    event.status,
                    event.file_path,
                    event.content_hash,
                    event.intent,
                    event.reasoning_summary,
                    event.tool_name,
                    event.input_summary,
                    event.output_summary,
                    event.timestamp.isoformat(),
                ),
            )
            await connection.commit()

    async def get_by_task(self, task_id: str) -> list[TaskStepEvent]:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM task_step_events
                WHERE task_id = ?
                ORDER BY rowid ASC
                """,
                (task_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: Any) -> TaskStepEvent:
        return TaskStepEvent.model_validate(
            {
                "task_id": row["task_id"],
                "step_id": row["step_id"],
                "step_kind": row["step_kind"],
                "status": row["status"],
                "file_path": row["file_path"],
                "content_hash": row["content_hash"],
                "intent": row["intent"],
                "reasoning_summary": row["reasoning_summary"],
                "tool_name": row["tool_name"],
                "input_summary": row["input_summary"],
                "output_summary": row["output_summary"],
                "timestamp": row["timestamp"],
            }
        )


__all__ = ["SQLiteTaskStepStore"]
