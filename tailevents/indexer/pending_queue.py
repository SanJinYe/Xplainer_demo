"""In-memory queue for events with pending AST parsing."""

from tailevents.models.event import TailEvent


class PendingQueue:
    """Store events that could not be parsed yet."""

    def __init__(self):
        self._events: dict[str, TailEvent] = {}

    def add(self, event: TailEvent) -> None:
        self._events[event.event_id] = event

    def get_pending(self) -> list[TailEvent]:
        return list(self._events.values())

    def remove(self, event_id: str) -> None:
        self._events.pop(event_id, None)

    async def retry_all(self, indexer) -> None:
        for event in list(self._events.values()):
            result = await indexer._process_event(  # noqa: SLF001
                event,
                retry_pending=False,
                enqueue_on_failure=False,
            )
            if not result.pending:
                self.remove(event.event_id)


__all__ = ["PendingQueue"]
