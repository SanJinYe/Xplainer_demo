"""Task-related request and result models."""

from typing import Literal, Optional

from pydantic import BaseModel


class CodingTaskRequest(BaseModel):
    """Request payload for the minimal B0 coding task."""

    file_path: str
    file_content: str
    user_prompt: str


class CodingTaskResult(BaseModel):
    """Validated result returned by the coding task model."""

    updated_file_content: str
    intent: str
    reasoning: Optional[str] = None
    action_type: Literal["create", "modify"]


__all__ = ["CodingTaskRequest", "CodingTaskResult"]
