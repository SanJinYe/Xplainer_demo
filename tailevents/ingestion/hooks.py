"""Hook contracts for ingestion side effects."""

from typing import Protocol, runtime_checkable

from tailevents.models.event import TailEvent
from tailevents.models.protocols import GraphServiceProtocol, IndexerResult


@runtime_checkable
class IngestionHook(Protocol):
    """Protocol for post-ingestion hooks."""

    async def on_event_ingested(self, event: TailEvent, result: IndexerResult) -> None: ...


class LoggingHook:
    """Print a short ingestion summary to stdout."""

    async def on_event_ingested(self, event: TailEvent, result: IndexerResult) -> None:
        print(
            "[ingestion]",
            event.event_id,
            event.action_type.value,
            event.file_path,
            f"pending={result.pending}",
            f"created={len(result.entities_created)}",
            f"modified={len(result.entities_modified)}",
            f"deleted={len(result.entities_deleted)}",
        )


class GraphUpdateHook:
    """Placeholder hook for future graph synchronization."""

    def __init__(self, graph_service: GraphServiceProtocol):
        self._graph_service = graph_service

    async def on_event_ingested(self, event: TailEvent, result: IndexerResult) -> None:
        """No-op for Requirement A; graph updates are deferred."""

        _ = self._graph_service
        _ = event
        _ = result


__all__ = ["GraphUpdateHook", "IngestionHook", "LoggingHook"]
