"""Public explanation module exports."""

from tailevents.explanation.context_assembler import ContextAssembler
from tailevents.explanation.doc_retriever import DocRetriever
from tailevents.explanation.engine import ExplanationEngine
from tailevents.explanation.exceptions import (
    EntityExplanationNotFoundError,
    ExplanationError,
    InvalidDetailLevelError,
    LLMClientError,
    UnsupportedLLMBackendError,
)
from tailevents.explanation.formatter import ExplanationFormatter
from tailevents.explanation.llm_client import (
    ClaudeLLMClient,
    LLMClientFactory,
    OllamaLLMClient,
    OpenRouterLLMClient,
)

__all__ = [
    "ClaudeLLMClient",
    "ContextAssembler",
    "DocRetriever",
    "EntityExplanationNotFoundError",
    "ExplanationEngine",
    "ExplanationError",
    "ExplanationFormatter",
    "InvalidDetailLevelError",
    "LLMClientError",
    "LLMClientFactory",
    "OllamaLLMClient",
    "OpenRouterLLMClient",
    "UnsupportedLLMBackendError",
]
