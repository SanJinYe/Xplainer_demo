"""Context models shared by coding runtime components."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ObservedFileView:
    """A single observed file snapshot returned by the extension."""

    file_path: str
    content: str
    content_hash: str
    document_version: Optional[int]


@dataclass
class CodingContextBundle:
    """All context assembled for a single coding attempt."""

    editable_views: dict[str, ObservedFileView]
    readonly_views: list[ObservedFileView]
    entity_refs: list[dict[str, object]] = field(default_factory=list)
    relation_context: list[dict[str, object]] = field(default_factory=list)
    external_docs: list[dict[str, object]] = field(default_factory=list)
    impact_paths: list[dict[str, object]] = field(default_factory=list)
    subgraph_summary: Optional[str] = None
    explanation_evidence: list[dict[str, object]] = field(default_factory=list)


__all__ = ["CodingContextBundle", "ObservedFileView"]
