"""Exceptions for the coding task orchestration layer."""


class CodingTaskError(Exception):
    """Base error for coding-task failures."""


class CodingTaskNotFoundError(CodingTaskError):
    """Raised when a task session does not exist."""


class CodingTaskValidationError(CodingTaskError):
    """Raised when a task request or tool result is invalid."""


class CodingTaskConflictError(CodingTaskError):
    """Raised when a tool result does not match the current task state."""


class CodingTaskCancelledError(CodingTaskError):
    """Raised when a task is cancelled during execution."""


__all__ = [
    "CodingTaskCancelledError",
    "CodingTaskConflictError",
    "CodingTaskError",
    "CodingTaskNotFoundError",
    "CodingTaskValidationError",
]
