"""Explanation request and response models."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from tailevents.models.enums import EntityType


HistorySource = Literal["baseline_only", "mixed", "traced_only"]
RelationContextKind = Literal["module", "class", "function", "method"]
RelationContextRole = Literal["caller", "callee", "container", "member"]


class RelationContextItem(BaseModel):
    """Structured relation item for explanation payloads."""

    entity_id: str
    qualified_name: str
    kind: RelationContextKind
    relation: RelationContextRole


class LocalRelationContext(BaseModel):
    """Local relation context shown by the explanation UI."""

    callers: list[RelationContextItem] = Field(default_factory=list)
    callees: list[RelationContextItem] = Field(default_factory=list)
    containers: list[RelationContextItem] = Field(default_factory=list)
    members: list[RelationContextItem] = Field(default_factory=list)


class GlobalRelationContext(BaseModel):
    """Reserved structure for later graph-aware relation context."""

    paths: Optional[list[dict]] = None
    subgraph: Optional[dict] = None


class RelationContext(BaseModel):
    """Structured relation context for explanation payloads."""

    local: LocalRelationContext = Field(default_factory=LocalRelationContext)
    global_: GlobalRelationContext = Field(
        default_factory=GlobalRelationContext,
        alias="global",
        serialization_alias="global",
    )

    model_config = {
        "populate_by_name": True,
    }


class ExplanationRequest(BaseModel):
    """User-facing explanation request."""

    query: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    cursor_word: Optional[str] = None
    detail_level: str = "summary"
    include_relations: bool = False


class EntityExplanation(BaseModel):
    """Explanation for a single entity."""

    entity_id: str
    entity_name: str
    qualified_name: str
    entity_type: EntityType
    signature: Optional[str] = None

    summary: str
    detailed_explanation: Optional[str] = None
    param_explanations: Optional[dict[str, str]] = None
    return_explanation: Optional[str] = None
    usage_context: Optional[str] = None

    creation_intent: Optional[str] = None
    modification_history: list[dict] = Field(default_factory=list)
    history_source: HistorySource = "traced_only"
    relation_context: RelationContext = Field(default_factory=RelationContext)
    related_entities: list[dict] = Field(default_factory=list)
    external_doc_snippets: list[dict] = Field(default_factory=list)

    generated_at: datetime = Field(default_factory=datetime.utcnow)
    from_cache: bool = False
    confidence: float = 1.0


class ExplanationResponse(BaseModel):
    """Complete response for an explanation request."""

    request: ExplanationRequest
    explanations: list[EntityExplanation]
    graph_context: Optional[dict] = None


class ExplanationStreamInit(BaseModel):
    """Initial stream payload for the explanation sidebar."""

    event: Literal["init"] = "init"
    entity_id: str
    entity_name: str
    qualified_name: str
    entity_type: EntityType
    signature: Optional[str] = None
    file_path: str
    line_range: Optional[tuple[int, int]] = None
    event_count: int = 0
    summary: Optional[str] = None
    history_source: HistorySource = "traced_only"


class ExplanationStreamDelta(BaseModel):
    """Incremental detailed explanation text."""

    event: Literal["delta"] = "delta"
    text: str


class ExplanationStreamDone(BaseModel):
    """Final completed explanation payload."""

    event: Literal["done"] = "done"
    explanation: EntityExplanation


class ExplanationStreamError(BaseModel):
    """Stream failure payload."""

    event: Literal["error"] = "error"
    message: str


ExplanationStreamEvent = (
    ExplanationStreamInit
    | ExplanationStreamDelta
    | ExplanationStreamDone
    | ExplanationStreamError
)


__all__ = [
    "EntityExplanation",
    "ExplanationRequest",
    "ExplanationResponse",
    "ExplanationStreamDelta",
    "ExplanationStreamDone",
    "ExplanationStreamError",
    "ExplanationStreamEvent",
    "ExplanationStreamInit",
    "GlobalRelationContext",
    "HistorySource",
    "LocalRelationContext",
    "RelationContext",
    "RelationContextItem",
]
