"""SQLite-backed explanation cache."""

from datetime import datetime, timedelta
from typing import Optional

from tailevents.models.protocols import CacheProtocol
from tailevents.storage.database import SQLiteConnectionManager


class ExplanationCache(CacheProtocol):
    """Store explanation payloads in SQLite."""

    def __init__(self, database: SQLiteConnectionManager):
        self._database = database
        self._hits = 0
        self._misses = 0

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
                self._misses += 1
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
                self._misses += 1
                return None

            self._hits += 1
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

    async def clear_all(self) -> None:
        """Invalidate all cache entries and reset runtime metrics."""

        async with self._database.connection() as connection:
            await connection.execute(
                """
                UPDATE explanation_cache
                SET is_valid = 0
                """
            )
            await connection.commit()
        self.reset_metrics()

    def reset_metrics(self) -> None:
        """Reset in-memory hit/miss counters."""

        self._hits = 0
        self._misses = 0

    async def stats(self) -> dict[str, float | int]:
        """Return SQLite-backed cache counts and runtime hit/miss metrics."""

        now = datetime.utcnow()
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT expires_at, is_valid
                FROM explanation_cache
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()

        total = len(rows)
        valid = 0
        for row in rows:
            if int(row["is_valid"]) == 0:
                continue
            expires_at = row["expires_at"]
            if expires_at is not None and datetime.fromisoformat(expires_at) <= now:
                continue
            valid += 1

        invalid = total - valid
        requests = self._hits + self._misses
        hit_rate = (self._hits / requests) if requests else 0.0

        return {
            "cache_total": total,
            "cache_valid": valid,
            "cache_invalid": invalid,
            "cache_hits": self._hits,
            "cache_misses": self._misses,
            "cache_hit_rate": hit_rate,
        }


__all__ = ["ExplanationCache"]
