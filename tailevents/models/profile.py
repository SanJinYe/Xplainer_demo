"""Coding profile and capability models."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from tailevents.models.protocols import LLMClientProtocol


ProfileSource = Literal["sync", "env_fallback"]


class CodingProfileSyncItem(BaseModel):
    """A single profile definition pushed from the extension."""

    profile_id: str
    label: str
    backend: str
    model: str
    is_default: bool = False
    api_key: Optional[str] = None


class CodingProfilesSyncRequest(BaseModel):
    """Bulk profile sync payload."""

    profiles: list[CodingProfileSyncItem] = Field(default_factory=list)


class CodingProfileStatusItem(BaseModel):
    """Sanitized merged profile view."""

    profile_id: str
    label: str
    backend: str
    model: str
    source: ProfileSource
    has_key: bool
    is_default: bool
    selectable: bool
    reason: Optional[str] = None


class CodingProfilesStatusResponse(BaseModel):
    """All selectable and fallback profiles visible to the extension."""

    profiles: list[CodingProfileStatusItem] = Field(default_factory=list)


@dataclass(frozen=True)
class ResolvedCodingProfile:
    """Resolved runtime profile used for profile-aware backend requests."""

    resolved_profile_id: str
    backend: str
    model: str
    source: ProfileSource
    llm_client: "LLMClientProtocol"


class CodingCapabilityState(BaseModel):
    """One capability flag exposed by the backend."""

    available: bool
    reason: Optional[str] = None


class CodingCapabilitiesResponse(BaseModel):
    """Global coding capabilities surfaced to the extension."""

    repo_observe: CodingCapabilityState
    multi_file: CodingCapabilityState
    mcp: CodingCapabilityState
    skills: CodingCapabilityState


__all__ = [
    "CodingCapabilitiesResponse",
    "CodingCapabilityState",
    "CodingProfileStatusItem",
    "CodingProfilesStatusResponse",
    "CodingProfilesSyncRequest",
    "CodingProfileSyncItem",
    "ProfileSource",
    "ResolvedCodingProfile",
]
