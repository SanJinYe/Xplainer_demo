"""SQLite-backed store for coding task history records."""

import json
from typing import Any, Optional

from tailevents.models.protocols import CodingTaskStoreProtocol
from tailevents.models.task import AppliedEventRecord, CodingTaskRecord
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
                    editable_files,
                    status,
                    created_at,
                    updated_at,
                    model_output_text,
                    verified_draft_content,
                    verified_files,
                    intent,
                    reasoning,
                    last_error,
                    applied_event_id,
                    applied_events,
                    launch_mode,
                    source_task_id,
                    selected_profile_id,
                    requested_capabilities,
                    applied_event_retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    target_file_path = excluded.target_file_path,
                    user_prompt = excluded.user_prompt,
                    context_files = excluded.context_files,
                    editable_files = excluded.editable_files,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    model_output_text = excluded.model_output_text,
                    verified_draft_content = excluded.verified_draft_content,
                    verified_files = excluded.verified_files,
                    intent = excluded.intent,
                    reasoning = excluded.reasoning,
                    last_error = excluded.last_error,
                    applied_event_id = excluded.applied_event_id,
                    applied_events = excluded.applied_events,
                    launch_mode = excluded.launch_mode,
                    source_task_id = excluded.source_task_id,
                    selected_profile_id = excluded.selected_profile_id,
                    requested_capabilities = excluded.requested_capabilities,
                    applied_event_retry_count = excluded.applied_event_retry_count
                """,
                (
                    record.task_id,
                    record.target_file_path,
                    record.user_prompt,
                    self._dump_json(record.context_files),
                    self._dump_json(record.editable_files),
                    record.status,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.model_output_text,
                    None,
                    self._dump_model_list(record.verified_files),
                    record.intent,
                    record.reasoning,
                    record.last_error,
                    None,
                    self._dump_model_list(record.applied_events),
                    record.launch_mode,
                    record.source_task_id,
                    record.selected_profile_id,
                    self._dump_json(record.requested_capabilities),
                    record.applied_event_retry_count,
                ),
            )
            await connection.commit()

    async def get(self, task_id: str) -> Optional[CodingTaskRecord]:
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

    async def list_recent(
        self,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        target_file_path: Optional[str] = None,
    ) -> tuple[list[CodingTaskRecord], int]:
        where_clauses: list[str] = []
        params: list[Any] = []

        if status:
            where_clauses.append("status = ?")
            params.append(status)
        if target_file_path:
            where_clauses.append("target_file_path = ?")
            params.append(target_file_path)

        where_sql = ""
        if where_clauses:
            where_sql = f"WHERE {' AND '.join(where_clauses)}"

        async with self._database.connection() as connection:
            count_cursor = await connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM coding_tasks
                {where_sql}
                """,
                tuple(params),
            )
            count_row = await count_cursor.fetchone()
            await count_cursor.close()

            list_cursor = await connection.execute(
                f"""
                SELECT * FROM coding_tasks
                {where_sql}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            )
            rows = await list_cursor.fetchall()
            await list_cursor.close()

        total = 0 if count_row is None else int(count_row["count"])
        return ([self._row_to_record(row) for row in rows], total)

    async def list_recent_target_paths(
        self,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> list[str]:
        params: list[Any] = []
        where_sql = ""
        if query and query.strip():
            where_sql = "WHERE target_file_path LIKE ? COLLATE NOCASE"
            params.append(f"%{query.strip()}%")

        async with self._database.connection() as connection:
            cursor = await connection.execute(
                f"""
                SELECT target_file_path, MAX(updated_at) AS latest_updated_at
                FROM coding_tasks
                {where_sql}
                GROUP BY target_file_path
                ORDER BY latest_updated_at DESC, target_file_path ASC
                LIMIT ?
                """,
                (*params, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        return [str(row["target_file_path"]) for row in rows]

    def _row_to_record(self, row: Any) -> CodingTaskRecord:
        verified_files = self._load_json(row["verified_files"], [])
        applied_events = self._load_json(row["applied_events"], [])

        legacy_applied_event_id = row["applied_event_id"]
        if not applied_events and legacy_applied_event_id:
            applied_events = [
                AppliedEventRecord(
                    file_path=row["target_file_path"],
                    event_id=legacy_applied_event_id,
                    status="written",
                ).model_dump(mode="json")
            ]

        return CodingTaskRecord.model_validate(
            {
                "task_id": row["task_id"],
                "target_file_path": row["target_file_path"],
                "user_prompt": row["user_prompt"],
                "context_files": self._load_json(row["context_files"], []),
                "editable_files": self._load_json(row["editable_files"], []),
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "model_output_text": row["model_output_text"],
                "verified_draft_content": row["verified_draft_content"],
                "verified_files": verified_files,
                "intent": row["intent"],
                "reasoning": row["reasoning"],
                "last_error": row["last_error"],
                "applied_events": applied_events,
                "launch_mode": row["launch_mode"] or "new",
                "source_task_id": row["source_task_id"],
                "selected_profile_id": row["selected_profile_id"],
                "requested_capabilities": self._load_json(
                    row["requested_capabilities"], []
                ),
                "applied_event_retry_count": row["applied_event_retry_count"] or 0,
            }
        )

    def _dump_json(self, value: Any) -> str:
        return json.dumps(value)

    def _dump_model_list(self, values: list[Any]) -> str:
        return json.dumps([value.model_dump(mode="json") for value in values])

    def _load_json(self, value: Optional[str], default: Any) -> Any:
        if value is None or value == "":
            return default
        return json.loads(value)


__all__ = ["SQLiteCodingTaskStore"]
