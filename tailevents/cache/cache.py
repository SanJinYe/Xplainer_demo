"""SQLite-backed explanation cache."""

from datetime import datetime, timedelta
from typing import Optional

from tailevents.models.protocols import CacheProtocol
from tailevents.storage.database import SQLiteConnectionManager


class ExplanationCache(CacheProtocol):
    """Store explanation payloads in SQLite."""

    def __init__(self, database: SQLiteConnectionManager):
        self._database = database

    async def get(self, key: str) -> Optional[str]:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT value, expires_at, is_valid
                FROM explanation_cache
                WHERE cache_key = ?
                """,
                (key,),
            )
            row = await cursor.fetchone()
            await cursor.close()

            if row is None or int(row["is_valid"]) == 0:
                return None

            expires_at = row["expires_at"]
            if expires_at is not None and datetime.fromisoformat(expires_at) <= datetime.utcnow():
                await connection.execute(
                    """
                    UPDATE explanation_cache
                    SET is_valid = 0
                    WHERE cache_key = ?
                    """,
                    (key,),
                )
                await connection.commit()
                return None

            return str(row["value"])

    async def put(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        created_at = datetime.utcnow()
        expires_at = None
        if ttl is not None:
            expires_at = (created_at + timedelta(seconds=ttl)).isoformat()

        async with self._database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO explanation_cache (
                    cache_key,
                    value,
                    created_at,
                    expires_at,
                    is_valid
                ) VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(cache_key) DO UPDATE SET
                    value = excluded.value,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    is_valid = 1
                """,
                (key, value, created_at.isoformat(), expires_at),
            )
            await connection.commit()

    async def invalidate(self, key: str) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                UPDATE explanation_cache
                SET is_valid = 0
                WHERE cache_key = ?
                """,
                (key,),
            )
            await connection.commit()

    async def invalidate_prefix(self, prefix: str) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                UPDATE explanation_cache
                SET is_valid = 0
                WHERE cache_key LIKE ?
                """,
                (f"{prefix}%",),
            )
            await connection.commit()


__all__ = ["ExplanationCache"]
