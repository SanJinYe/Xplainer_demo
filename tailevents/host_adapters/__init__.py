"""Host-specific adapters for external coding agents."""

from tailevents.host_adapters.cline import (
    ClineTraceBatchRequest,
    ClineTraceIngestResponse,
    convert_cline_messages,
    normalize_cline_messages,
)
from tailevents.host_adapters.normalized import (
    NormalizedHostEvent,
    host_events_to_raw_events,
)

__all__ = [
    "ClineTraceBatchRequest",
    "ClineTraceIngestResponse",
    "NormalizedHostEvent",
    "convert_cline_messages",
    "host_events_to_raw_events",
    "normalize_cline_messages",
]
