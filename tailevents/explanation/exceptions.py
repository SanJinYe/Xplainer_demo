"""Custom exceptions for the explanation module."""


class ExplanationError(Exception):
    """Base exception for explanation failures."""


class EntityExplanationNotFoundError(ExplanationError):
    """Raised when the requested entity does not exist."""


class InvalidDetailLevelError(ExplanationError):
    """Raised when an unsupported detail level is requested."""


class LLMClientError(ExplanationError):
    """Raised when an LLM backend request fails."""


class UnsupportedLLMBackendError(ExplanationError):
    """Raised when the configured LLM backend is not supported."""


__all__ = [
    "EntityExplanationNotFoundError",
    "ExplanationError",
    "InvalidDetailLevelError",
    "LLMClientError",
    "UnsupportedLLMBackendError",
]
