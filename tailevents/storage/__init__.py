"""Public storage layer exports."""

from tailevents.storage.database import SQLiteConnectionManager, get_db, initialize_db
from tailevents.storage.entity_db import SQLiteEntityDB
from tailevents.storage.event_store import SQLiteEventStore
from tailevents.storage.exceptions import (
    EventEnrichmentError,
    RecordNotFoundError,
    StorageError,
)
from tailevents.storage.relation_store import SQLiteRelationStore

__all__ = [
    "EventEnrichmentError",
    "RecordNotFoundError",
    "SQLiteConnectionManager",
    "SQLiteEntityDB",
    "SQLiteEventStore",
    "SQLiteRelationStore",
    "StorageError",
    "get_db",
    "initialize_db",
]
