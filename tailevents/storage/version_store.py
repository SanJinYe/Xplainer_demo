"""Persist coarse-grained runtime versions in SQLite."""

from typing import Optional

from tailevents.storage.database import SQLiteConnectionManager


class SQLiteVersionStore:
    """Store monotonic versions for graph/docs invalidation."""

    def __init__(self, database: SQLiteConnectionManager):
        self._database = database

    async def get(self, key: str) -> int:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT value
                FROM system_state
                WHERE key = ?
                """,
                (key,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return 0
        return int(row["value"])

    async def set(self, key: str, value: int) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO system_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )
            await connection.commit()

    async def bump(self, key: str) -> int:
        next_value = await self.get(key) + 1
        await self.set(key, next_value)
        return next_value

    async def ensure_defaults(self, keys: list[str]) -> None:
        async with self._database.connection() as connection:
            for key in keys:
                await connection.execute(
                    """
                    INSERT INTO system_state (key, value)
                    VALUES (?, '0')
                    ON CONFLICT(key) DO NOTHING
                    """,
                    (key,),
                )
            await connection.commit()


__all__ = ["SQLiteVersionStore"]
