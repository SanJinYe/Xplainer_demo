"""SQLite-backed store for coding task history records."""

import json
from typing import Any

from tailevents.models.protocols import CodingTaskStoreProtocol
from tailevents.models.task import CodingTaskRecord
from tailevents.storage.database import SQLiteConnectionManager


class SQLiteCodingTaskStore(CodingTaskStoreProtocol):
    """Persist coding task history records in SQLite."""

    def __init__(self, database: SQLiteConnectionManager):
        self._database = database

    async def put(self, record: CodingTaskRecord) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO coding_tasks (
                    task_id,
                    target_file_path,
                    user_prompt,
                    context_files,
                    status,
                    created_at,
                    updated_at,
                    model_output_text,
                    verified_draft_content,
                    intent,
                    reasoning,
                    last_error,
                    applied_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    target_file_path = excluded.target_file_path,
                    user_prompt = excluded.user_prompt,
                    context_files = excluded.context_files,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    model_output_text = excluded.model_output_text,
                    verified_draft_content = excluded.verified_draft_content,
                    intent = excluded.intent,
                    reasoning = excluded.reasoning,
                    last_error = excluded.last_error,
                    applied_event_id = excluded.applied_event_id
                """,
                (
                    record.task_id,
                    record.target_file_path,
                    record.user_prompt,
                    json.dumps(record.context_files),
                    record.status,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.model_output_text,
                    record.verified_draft_content,
                    record.intent,
                    record.reasoning,
                    record.last_error,
                    record.applied_event_id,
                ),
            )
            await connection.commit()

    async def get(self, task_id: str) -> CodingTaskRecord | None:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM coding_tasks
                WHERE task_id = ?
                """,
                (task_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return None
        return self._row_to_record(row)

    async def list_recent(self, limit: int = 20) -> list[CodingTaskRecord]:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM coding_tasks
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: Any) -> CodingTaskRecord:
        return CodingTaskRecord.model_validate(
            {
                "task_id": row["task_id"],
                "target_file_path": row["target_file_path"],
                "user_prompt": row["user_prompt"],
                "context_files": json.loads(row["context_files"] or "[]"),
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "model_output_text": row["model_output_text"],
                "verified_draft_content": row["verified_draft_content"],
                "intent": row["intent"],
                "reasoning": row["reasoning"],
                "last_error": row["last_error"],
                "applied_event_id": row["applied_event_id"],
            }
        )


__all__ = ["SQLiteCodingTaskStore"]
