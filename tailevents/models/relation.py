"""Relation models."""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from tailevents.models.enums import Provenance, RelationType


class Relation(BaseModel):
    relation_id: str = Field(default_factory=lambda: f"rel_{uuid4().hex[:12]}")
    source: str
    target: str
    relation_type: RelationType
    provenance: Provenance
    confidence: float = 1.0
    from_event: Optional[str] = None
    context: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True


__all__ = ["Relation"]
