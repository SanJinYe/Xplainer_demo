"""Baseline onboarding request and response models."""

from typing import Literal, Optional

from pydantic import BaseModel


class BaselineOnboardFileRequest(BaseModel):
    file_path: str
    code_snapshot: str


class BaselineOnboardFileResponse(BaseModel):
    status: Literal["created", "skipped"]
    file_path: str
    event_id: Optional[str] = None
    reason: Optional[Literal["duplicate_baseline", "existing_traced_history"]] = None


__all__ = ["BaselineOnboardFileRequest", "BaselineOnboardFileResponse"]
