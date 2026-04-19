"""Shared internal capability contracts."""

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from tailevents.models.task import CodingTaskEdit, VerifiedFileDraft


class EditPlan(BaseModel):
    """Validated model output for a single coding attempt."""

    edits: list[CodingTaskEdit]
    intent: str
    reasoning: Optional[str] = None


@dataclass(frozen=True)
class CodeAttemptOutcome:
    """Completed single-attempt code result."""

    plan: EditPlan
    draft_contents: dict[str, str]
    verified_files: list[VerifiedFileDraft]
    edit_step_id: str
    verify_step_id: str


@runtime_checkable
class RuntimeCapabilityProtocol(Protocol):
    """Minimal internal runtime capability contract."""

    name: str


__all__ = ["CodeAttemptOutcome", "EditPlan", "RuntimeCapabilityProtocol"]
