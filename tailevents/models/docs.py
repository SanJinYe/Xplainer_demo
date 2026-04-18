"""Typed external document payload models."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


DocSourceKind = Literal["pydoc", "workspace_doc"]


class ExternalDocSource(BaseModel):
    """Resolved documentation source metadata."""

    kind: DocSourceKind
    package: str
    symbol: str
    file_path: Optional[str] = None
    doc_uri: Optional[str] = None


class ExternalDocChunk(BaseModel):
    """Retrieved document chunk used by explanations."""

    chunk_id: str
    content: str
    content_hash: Optional[str] = None


class ExternalDocMatch(BaseModel):
    """Typed match returned by the retriever."""

    source: ExternalDocSource
    chunk: ExternalDocChunk
    usage_pattern: str
    version: Optional[str] = None
    score: float = 0.0


class AuthorizedDocSnapshot(BaseModel):
    """Single authorized workspace document snapshot."""

    file_path: str
    content: str
    content_hash: str


class DocsSyncRequest(BaseModel):
    """Replace the backend workspace-doc index with the provided snapshot."""

    documents: list[AuthorizedDocSnapshot] = Field(default_factory=list)


class DocsSyncSkippedItem(BaseModel):
    """Document omitted during sync."""

    file_path: str
    reason: str


class DocsSyncResponse(BaseModel):
    """Result of a document sync request."""

    accepted: int
    skipped: list[DocsSyncSkippedItem] = Field(default_factory=list)
    revision: int


__all__ = [
    "AuthorizedDocSnapshot",
    "DocsSyncRequest",
    "DocsSyncResponse",
    "DocsSyncSkippedItem",
    "ExternalDocChunk",
    "ExternalDocMatch",
    "ExternalDocSource",
]
