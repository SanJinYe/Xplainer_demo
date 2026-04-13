"""Event models."""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from tailevents.models.enums import ActionType, EntityRole, UsagePattern


class ExternalRef(BaseModel):
    package: str
    symbol: str
    version: Optional[str] = None
    doc_uri: Optional[str] = None
    usage_pattern: UsagePattern


class EntityRef(BaseModel):
    entity_id: str
    role: EntityRole


class TailEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"te_{uuid4().hex[:12]}")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent_step_id: Optional[str] = None
    session_id: Optional[str] = None

    action_type: ActionType
    file_path: str
    line_range: Optional[tuple[int, int]] = None
    code_snapshot: str

    intent: str
    reasoning: Optional[str] = None
    decision_alternatives: Optional[list[str]] = None

    entity_refs: list[EntityRef] = Field(default_factory=list)
    external_refs: list[ExternalRef] = Field(default_factory=list)


class RawEvent(BaseModel):
    """Minimal event emitted by the coding agent."""

    action_type: ActionType
    file_path: str
    code_snapshot: str
    intent: str
    reasoning: Optional[str] = None
    decision_alternatives: Optional[list[str]] = None
    agent_step_id: Optional[str] = None
    session_id: Optional[str] = None
    line_range: Optional[tuple[int, int]] = None
    external_refs: list[ExternalRef] = Field(default_factory=list)


__all__ = ["EntityRef", "ExternalRef", "RawEvent", "TailEvent"]
