"""Explanation request and response models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from tailevents.models.enums import EntityType


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


__all__ = [
    "EntityExplanation",
    "ExplanationRequest",
    "ExplanationResponse",
]
