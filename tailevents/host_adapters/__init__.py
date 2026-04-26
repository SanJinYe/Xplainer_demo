"""Host-specific adapters for external coding agents."""

from tailevents.host_adapters.cline import (
    ClineTraceBatchRequest,
    ClineTraceIngestResponse,
    convert_cline_messages,
)

__all__ = [
    "ClineTraceBatchRequest",
    "ClineTraceIngestResponse",
    "convert_cline_messages",
]
