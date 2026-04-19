"""Runtime event buffering utilities."""

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(frozen=True)
class RuntimeStreamEvent:
    """A single SSE-ready runtime event."""

    event: str
    data: dict[str, object]


class RuntimeEventSink:
    """Store runtime events and stream them to subscribers."""

    def __init__(self) -> None:
        self._events: list[RuntimeStreamEvent] = []
        self._condition = asyncio.Condition()
        self._done = False

    @property
    def events(self) -> list[RuntimeStreamEvent]:
        return self._events

    @property
    def done(self) -> bool:
        return self._done

    async def emit(self, event: str, data: dict[str, object]) -> None:
        async with self._condition:
            self._events.append(RuntimeStreamEvent(event=event, data=data))
            self._condition.notify_all()

    async def mark_done(self) -> None:
        async with self._condition:
            self._done = True
            self._condition.notify_all()

    async def stream(self) -> AsyncIterator[tuple[str, dict[str, object]]]:
        index = 0
        while True:
            while index < len(self._events):
                item = self._events[index]
                index += 1
                yield (item.event, item.data)

            if self._done:
                break

            async with self._condition:
                if index >= len(self._events) and not self._done:
                    await self._condition.wait()


__all__ = ["RuntimeEventSink", "RuntimeStreamEvent"]
