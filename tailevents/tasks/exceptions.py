"""Task module exceptions."""


class CodingTaskError(Exception):
    """Base error for coding task failures."""


class CodingTaskParseError(CodingTaskError):
    """Raised when the model output cannot be parsed into a task result."""


__all__ = ["CodingTaskError", "CodingTaskParseError"]
