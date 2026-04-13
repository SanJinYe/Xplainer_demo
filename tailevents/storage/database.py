"""SQLite connection manager."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional, cast

import aiosqlite
from fastapi import Request

from tailevents.config.settings import Settings
from tailevents.storage.migrations import run_migrations


class SQLiteConnectionManager:
    """Manage a shared async SQLite connection."""

    def __init__(self, db_path: str, uri: bool = False):
        self._db_path = db_path
        self._uri = uri
        self._connection: Optional[aiosqlite.Connection] = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "SQLiteConnectionManager":
        """Build a connection manager from application settings."""

        return cls(db_path=settings.db_path)

    @property
    def db_path(self) -> str:
        """Return the configured database path."""

        return self._db_path

    async def connect(self) -> aiosqlite.Connection:
        """Open and configure the SQLite connection."""

        if self._connection is None:
            self._ensure_parent_directory()
            self._connection = await aiosqlite.connect(self._db_path, uri=self._uri)
            self._connection.row_factory = aiosqlite.Row
            await self._connection.execute("PRAGMA foreign_keys = ON;")
            if not self._is_in_memory():
                await self._connection.execute("PRAGMA journal_mode = WAL;")
            await self._connection.commit()
        return self._connection

    async def close(self) -> None:
        """Close the managed SQLite connection."""

        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def __aenter__(self) -> "SQLiteConnectionManager":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, exc_tb) -> None:
        await self.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield the configured SQLite connection."""

        connection = await self.connect()
        yield connection

    def _ensure_parent_directory(self) -> None:
        if self._is_in_memory() or self._db_path.startswith("file:"):
            return
        Path(self._db_path).expanduser().resolve().parent.mkdir(
            parents=True, exist_ok=True
        )

    def _is_in_memory(self) -> bool:
        return self._db_path == ":memory:" or "mode=memory" in self._db_path


async def initialize_db(database: SQLiteConnectionManager) -> None:
    """Initialize the SQLite schema."""

    async with database.connection() as connection:
        await run_migrations(connection)


async def get_db(request: Request) -> AsyncIterator[aiosqlite.Connection]:
    """FastAPI dependency that yields the shared SQLite connection."""

    database = cast(SQLiteConnectionManager, request.app.state.db_manager)
    async with database.connection() as connection:
        yield connection


__all__ = ["SQLiteConnectionManager", "get_db", "initialize_db"]
