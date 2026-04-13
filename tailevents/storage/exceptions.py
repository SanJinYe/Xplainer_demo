"""Storage layer exceptions."""


class StorageError(Exception):
    """Base exception for storage-related failures."""


class RecordNotFoundError(StorageError):
    """Raised when a requested record does not exist."""


class EventEnrichmentError(StorageError):
    """Raised when an event enrichment operation is invalid."""


__all__ = ["EventEnrichmentError", "RecordNotFoundError", "StorageError"]
