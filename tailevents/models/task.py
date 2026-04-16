"""Task-related request and result models."""

from typing import Literal, Optional

from pydantic import BaseModel


class CodingTaskRequest(BaseModel):
    """Request payload for the minimal B0 coding task."""

    file_path: str
    file_content: str
    user_prompt: str


class CodingTaskEdit(BaseModel):
    """An exact-match replacement block returned by the model."""

    old_text: str
    new_text: str


class CodingTaskResult(BaseModel):
    """Validated result returned by the coding task model."""

    updated_file_content: str
    edits: list[CodingTaskEdit]
    intent: str
    reasoning: Optional[str] = None
    action_type: Literal["create", "modify"]


__all__ = ["CodingTaskEdit", "CodingTaskRequest", "CodingTaskResult"]
