"""Typed graph payload models."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


ImpactDirection = Literal["upstream", "downstream"]
ImpactRelationType = Literal["calls", "imports", "inherits", "composed_of"]
ImpactEvidenceLevel = Literal["strong", "weak"]
ImpactTerminalReason = Literal[
    "module_root",
    "inheritance_root",
    "inheritance_leaf",
    "call_boundary",
    "frontier",
]
ImpactTruncationReason = Literal["frontier", "hop_limit", "expansion_limit"]


class GraphNode(BaseModel):
    """Compact graph node payload."""

    entity_id: str
    qualified_name: str
    entity_type: str


class GraphEdge(BaseModel):
    """Compact graph edge payload."""

    source: str
    target: str
    relation_type: str


class GraphSubgraph(BaseModel):
    """Typed subgraph API payload."""

    entity_id: str
    depth: int
    truncated: bool = False
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class GraphSubgraphSummary(BaseModel):
    """Compact subgraph summary attached to explanations."""

    depth: int
    node_count: int = 0
    edge_count: int = 0
    truncated: bool = False
    relation_types: list[str] = Field(default_factory=list)


class GlobalImpactPathStep(BaseModel):
    """Single node within a global impact path."""

    entity_id: str
    qualified_name: str
    entity_type: str


class GlobalImpactPath(BaseModel):
    """Ranked path used by the explanation sidebar."""

    direction: ImpactDirection
    steps: list[GlobalImpactPathStep] = Field(default_factory=list)
    step_relations: list[ImpactRelationType] = Field(default_factory=list)
    cost: int
    hop_count: int
    composed_hops: int = 0
    terminal_entity_id: str
    terminal_qualified_name: str
    terminal_reason: ImpactTerminalReason
    evidence_level: ImpactEvidenceLevel = "weak"
    truncated: bool = False
    truncation_reason: Optional[ImpactTruncationReason] = None


__all__ = [
    "GlobalImpactPath",
    "GlobalImpactPathStep",
    "GraphEdge",
    "GraphNode",
    "GraphSubgraph",
    "GraphSubgraphSummary",
    "ImpactEvidenceLevel",
    "ImpactDirection",
    "ImpactRelationType",
    "ImpactTerminalReason",
    "ImpactTruncationReason",
]
