"""Code entity models."""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from tailevents.models.enums import EntityRole, EntityType


class ParamInfo(BaseModel):
    name: str
    type_hint: Optional[str] = None
    default: Optional[str] = None
    description: Optional[str] = None


class EventRef(BaseModel):
    event_id: str
    role: EntityRole
    timestamp: datetime


class RenameRecord(BaseModel):
    old_qualified_name: str
    new_qualified_name: str
    event_id: str
    timestamp: datetime


class CodeEntity(BaseModel):
    entity_id: str = Field(default_factory=lambda: f"ent_{uuid4().hex[:12]}")

    name: str
    qualified_name: str
    entity_type: EntityType
    file_path: str
    line_range: Optional[tuple[int, int]] = None

    signature: Optional[str] = None
    params: list[ParamInfo] = Field(default_factory=list)
    return_type: Optional[str] = None
    docstring: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_event: Optional[str] = None
    last_modified_event: Optional[str] = None
    last_modified_at: Optional[datetime] = None
    modification_count: int = 0
    is_deleted: bool = False
    deleted_by_event: Optional[str] = None

    event_refs: list[EventRef] = Field(default_factory=list)
    rename_history: list[RenameRecord] = Field(default_factory=list)

    is_external: bool = False
    package: Optional[str] = None

    cached_description: Optional[str] = None
    description_valid: bool = False

    in_degree: int = 0
    out_degree: int = 0
    tags: list[str] = Field(default_factory=list)


__all__ = ["CodeEntity", "EventRef", "ParamInfo", "RenameRecord"]
