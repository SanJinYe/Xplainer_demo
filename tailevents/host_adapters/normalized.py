"""Host-agnostic normalized trace events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from tailevents.models.enums import ActionType
from tailevents.models.event import RawEvent


HostEventKind = Literal[
    "file_change",
    "read_observation",
    "completion",
    "error",
]


@dataclass(frozen=True)
class NormalizedHostEvent:
    """Stable internal event shape emitted by host-specific adapters."""

    host: str
    task_id: str
    session_id: str
    agent_step_id: str
    kind: HostEventKind
    tool_name: Optional[str] = None
    action_type: Optional[ActionType] = None
    file_path: Optional[str] = None
    code_snapshot: Optional[str] = None
    intent: Optional[str] = None
    reasoning: Optional[str] = None
    line_range: Optional[tuple[int, int]] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_raw_event(self) -> Optional[RawEvent]:
        """Convert file-change host events into TailEvents raw events."""

        if self.kind != "file_change":
            return None
        if (
            self.action_type is None
            or self.file_path is None
            or self.code_snapshot is None
            or self.intent is None
        ):
            return None
        return RawEvent(
            action_type=self.action_type,
            file_path=self.file_path,
            code_snapshot=self.code_snapshot,
            intent=self.intent,
            reasoning=self.reasoning,
            agent_step_id=self.agent_step_id,
            session_id=self.session_id,
            line_range=self.line_range,
        )


def host_events_to_raw_events(events: list[NormalizedHostEvent]) -> list[RawEvent]:
    """Extract TailEvents raw events from normalized host events."""

    raw_events: list[RawEvent] = []
    for event in events:
        raw_event = event.to_raw_event()
        if raw_event is not None:
            raw_events.append(raw_event)
    return raw_events


__all__ = [
    "HostEventKind",
    "NormalizedHostEvent",
    "host_events_to_raw_events",
]
