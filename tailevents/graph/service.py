"""Graph service for subgraph and impact-path queries."""

import heapq
import time
from collections import deque
from statistics import mean
from typing import Optional

from tailevents.models.entity import CodeEntity
from tailevents.models.enums import EntityType, RelationType
from tailevents.models.graph import (
    GlobalImpactPath,
    GlobalImpactPathStep,
    GraphEdge,
    GraphNode,
    GraphSubgraph,
)
from tailevents.models.protocols import EntityDBProtocol, GraphServiceProtocol, RelationStoreProtocol
from tailevents.models.relation import Relation


class GraphMetricsTracker:
    """Track runtime graph query metrics."""

    def __init__(self, sample_limit: int = 200):
        self._subgraph = _MetricBucket(sample_limit)
        self._impact_paths = _MetricBucket(sample_limit)

    def record_subgraph(self, *, total_ms: float, truncated: bool, error: bool = False) -> None:
        self._subgraph.record(total_ms=total_ms, truncated=truncated, error=error)

    def record_impact_paths(
        self,
        *,
        total_ms: float,
        truncated: bool,
        paths: Optional[list[GlobalImpactPath]] = None,
        error: bool = False,
    ) -> None:
        self._impact_paths.record(
            total_ms=total_ms,
            truncated=truncated,
            error=error,
            paths=paths,
        )

    def snapshot(self) -> dict[str, dict[str, float | int | None]]:
        return {
            "subgraph": self._subgraph.snapshot(),
            "impact_paths": self._impact_paths.snapshot(),
        }

    def reset(self) -> None:
        self._subgraph.reset()
        self._impact_paths.reset()


class GraphService(GraphServiceProtocol):
    """Serve lightweight graph queries from current relation state."""

    _RELATION_WEIGHTS = {
        RelationType.CALLS.value: 1,
        RelationType.INHERITS.value: 2,
        RelationType.COMPOSED_OF.value: 3,
        RelationType.IMPORTS.value: 4,
    }

    def __init__(
        self,
        entity_db: EntityDBProtocol,
        relation_store: RelationStoreProtocol,
        telemetry: Optional[GraphMetricsTracker] = None,
        max_subgraph_depth: int = 2,
        max_subgraph_nodes: int = 50,
        max_subgraph_edges: int = 100,
        max_impact_limit: int = 5,
        max_impact_hops: int = 8,
        max_expanded_nodes: int = 200,
    ):
        self._entity_db = entity_db
        self._relation_store = relation_store
        self._telemetry = telemetry or GraphMetricsTracker()
        self._max_subgraph_depth = max(1, max_subgraph_depth)
        self._max_subgraph_nodes = max(1, max_subgraph_nodes)
        self._max_subgraph_edges = max(1, max_subgraph_edges)
        self._max_impact_limit = max(1, max_impact_limit)
        self._max_impact_hops = max(1, max_impact_hops)
        self._max_expanded_nodes = max(1, max_expanded_nodes)

    def get_metrics(self) -> dict[str, dict[str, float | int | None]]:
        return self._telemetry.snapshot()

    def reset_metrics(self) -> None:
        self._telemetry.reset()

    async def get_subgraph(self, entity_id: str, depth: int = 2) -> GraphSubgraph:
        started_at = time.perf_counter()
        truncated = False
        try:
            entities = await self._load_entities()
            if entity_id not in entities:
                return GraphSubgraph(entity_id=entity_id, depth=min(depth, self._max_subgraph_depth))

            bounded_depth = min(max(depth, 1), self._max_subgraph_depth)
            relations = await self._relation_store.get_all_active()
            outgoing, incoming = self._build_relation_maps(relations)

            visited: set[str] = {entity_id}
            queue: deque[tuple[str, int]] = deque([(entity_id, 0)])
            edge_keys: set[tuple[str, str, str]] = set()

            while queue:
                current_id, current_depth = queue.popleft()
                if current_depth >= bounded_depth:
                    continue

                neighbors = outgoing.get(current_id, []) + incoming.get(current_id, [])
                for relation in neighbors:
                    edge_key = (
                        relation.source,
                        relation.target,
                        relation.relation_type.value,
                    )
                    if len(edge_keys) >= self._max_subgraph_edges and edge_key not in edge_keys:
                        truncated = True
                        continue
                    edge_keys.add(edge_key)

                    neighbor_id = (
                        relation.target
                        if relation.source == current_id
                        else relation.source
                    )
                    if neighbor_id in visited:
                        continue
                    if len(visited) >= self._max_subgraph_nodes:
                        truncated = True
                        continue
                    if neighbor_id not in entities:
                        continue
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, current_depth + 1))

            included_edges = [
                GraphEdge(source=source, target=target, relation_type=relation_type)
                for source, target, relation_type in sorted(edge_keys)
                if source in visited and target in visited
            ][: self._max_subgraph_edges]
            nodes = [
                self._to_graph_node(entities[node_id])
                for node_id in sorted(visited, key=lambda item: entities[item].qualified_name)
            ]
            if len(included_edges) >= self._max_subgraph_edges:
                truncated = True

            result = GraphSubgraph(
                entity_id=entity_id,
                depth=bounded_depth,
                truncated=truncated,
                nodes=nodes,
                edges=included_edges,
            )
            self._telemetry.record_subgraph(
                total_ms=(time.perf_counter() - started_at) * 1000,
                truncated=truncated,
                error=False,
            )
            return result
        except Exception:
            self._telemetry.record_subgraph(
                total_ms=(time.perf_counter() - started_at) * 1000,
                truncated=truncated,
                error=True,
            )
            raise

    async def get_impact_paths(
        self,
        entity_id: str,
        direction: str = "both",
        limit: int = 3,
    ) -> list[GlobalImpactPath]:
        started_at = time.perf_counter()
        truncated = False
        try:
            bounded_limit = min(max(limit, 1), self._max_impact_limit)
            entities = await self._load_entities()
            if entity_id not in entities:
                return []

            relations = await self._relation_store.get_all_active()
            relation_maps = self._build_impact_maps(relations)

            paths: list[GlobalImpactPath] = []
            directions = ["upstream", "downstream"] if direction == "both" else [direction]
            for requested_direction in directions:
                found_paths, hit_limit = self._search_direction(
                    start_id=entity_id,
                    direction=requested_direction,
                    limit=bounded_limit,
                    entities=entities,
                    relation_maps=relation_maps,
                )
                paths.extend(found_paths)
                truncated = truncated or hit_limit

            self._telemetry.record_impact_paths(
                total_ms=(time.perf_counter() - started_at) * 1000,
                truncated=truncated,
                paths=paths,
                error=False,
            )
            return paths
        except Exception:
            self._telemetry.record_impact_paths(
                total_ms=(time.perf_counter() - started_at) * 1000,
                truncated=truncated,
                paths=None,
                error=True,
            )
            raise

    async def get_isolated_entities(self) -> list[str]:
        return []

    async def get_single_dependency_entities(self) -> list[str]:
        return []

    async def detect_cycles(self) -> list[list[str]]:
        raise NotImplementedError("Graph analysis not yet implemented")

    async def get_communities(self) -> list[list[str]]:
        raise NotImplementedError("Graph analysis not yet implemented")

    async def get_entity_importance(self, entity_id: str) -> dict:
        return {
            "entity_id": entity_id,
            "implemented": False,
        }

    async def _load_entities(self) -> dict[str, CodeEntity]:
        return {
            entity.entity_id: entity
            for entity in await self._entity_db.get_all()
            if not entity.is_deleted
        }

    def _build_relation_maps(
        self,
        relations: list[Relation],
    ) -> tuple[dict[str, list[Relation]], dict[str, list[Relation]]]:
        outgoing: dict[str, list[Relation]] = {}
        incoming: dict[str, list[Relation]] = {}
        for relation in relations:
            if not relation.is_active:
                continue
            outgoing.setdefault(relation.source, []).append(relation)
            incoming.setdefault(relation.target, []).append(relation)
        return outgoing, incoming

    def _build_impact_maps(
        self,
        relations: list[Relation],
    ) -> dict[str, dict[str, list[tuple[str, str]]]]:
        upstream: dict[str, list[tuple[str, str]]] = {}
        downstream: dict[str, list[tuple[str, str]]] = {}
        upstream_inherits: dict[str, list[tuple[str, str]]] = {}
        downstream_inherits: dict[str, list[tuple[str, str]]] = {}
        upstream_primary: dict[str, list[tuple[str, str]]] = {}
        downstream_primary: dict[str, list[tuple[str, str]]] = {}

        for relation in relations:
            if not relation.is_active:
                continue
            relation_name = relation.relation_type.value
            if relation.relation_type == RelationType.CALLS:
                self._register_impact_edge(
                    upstream,
                    relation.target,
                    relation.source,
                    relation_name,
                )
                self._register_impact_edge(
                    downstream,
                    relation.source,
                    relation.target,
                    relation_name,
                )
                self._register_impact_edge(
                    upstream_primary,
                    relation.target,
                    relation.source,
                    relation_name,
                )
                self._register_impact_edge(
                    downstream_primary,
                    relation.source,
                    relation.target,
                    relation_name,
                )
            elif relation.relation_type == RelationType.IMPORTS:
                self._register_impact_edge(
                    upstream,
                    relation.target,
                    relation.source,
                    relation_name,
                )
                self._register_impact_edge(
                    downstream,
                    relation.source,
                    relation.target,
                    relation_name,
                )
                self._register_impact_edge(
                    upstream_primary,
                    relation.target,
                    relation.source,
                    relation_name,
                )
                self._register_impact_edge(
                    downstream_primary,
                    relation.source,
                    relation.target,
                    relation_name,
                )
            elif relation.relation_type == RelationType.COMPOSED_OF:
                self._register_impact_edge(
                    upstream,
                    relation.target,
                    relation.source,
                    relation_name,
                )
                self._register_impact_edge(
                    downstream,
                    relation.source,
                    relation.target,
                    relation_name,
                )
            elif relation.relation_type == RelationType.INHERITS:
                # Stored as child -> base. Impact traversal treats base -> child as downstream.
                self._register_impact_edge(
                    upstream,
                    relation.source,
                    relation.target,
                    relation_name,
                )
                self._register_impact_edge(
                    downstream,
                    relation.target,
                    relation.source,
                    relation_name,
                )
                self._register_impact_edge(
                    upstream_inherits,
                    relation.source,
                    relation.target,
                    relation_name,
                )
                self._register_impact_edge(
                    downstream_inherits,
                    relation.target,
                    relation.source,
                    relation_name,
                )
                self._register_impact_edge(
                    upstream_primary,
                    relation.source,
                    relation.target,
                    relation_name,
                )
                self._register_impact_edge(
                    downstream_primary,
                    relation.target,
                    relation.source,
                    relation_name,
                )

        return {
            "upstream": upstream,
            "downstream": downstream,
            "upstream_inherits": upstream_inherits,
            "downstream_inherits": downstream_inherits,
            "upstream_primary": upstream_primary,
            "downstream_primary": downstream_primary,
        }

    def _search_direction(
        self,
        *,
        start_id: str,
        direction: str,
        limit: int,
        entities: dict[str, CodeEntity],
        relation_maps: dict[str, dict[str, list[tuple[str, str]]]],
    ) -> tuple[list[GlobalImpactPath], bool]:
        initial_terminal_reason = self._strong_terminal_reason(
            entity_id=start_id,
            direction=direction,
            entities=entities,
            relation_maps=relation_maps,
            path_relations=(),
        )
        if initial_terminal_reason is not None:
            entity = entities[start_id]
            return (
                [
                    GlobalImpactPath(
                        direction=direction,
                        steps=[self._to_path_step(entity)],
                        step_relations=[],
                        cost=0,
                        hop_count=0,
                        composed_hops=0,
                        terminal_entity_id=entity.entity_id,
                        terminal_qualified_name=entity.qualified_name,
                        terminal_reason=initial_terminal_reason,
                        evidence_level="weak",
                        truncated=False,
                        truncation_reason=None,
                    )
                ],
                False,
            )

        heap: list[tuple[int, int, int, int, int, tuple[str, ...], tuple[str, ...]]] = []
        heapq.heappush(heap, (0, 0, 0, 0, 0, (start_id,), ()))
        candidates: list[GlobalImpactPath] = []
        expanded = 0
        truncated = False

        while heap and expanded < self._max_expanded_nodes:
            cost, hop_count, composed_hops, import_hops, _, path, path_relations = heapq.heappop(
                heap
            )
            current_id = path[-1]
            expanded += 1

            expandable_neighbors = [
                (neighbor_id, relation_type)
                for neighbor_id, relation_type in self._impact_neighbors(
                    entity_id=current_id,
                    direction=direction,
                    relation_maps=relation_maps,
                )
                if neighbor_id in entities and neighbor_id not in path
            ]
            strong_terminal_reason = self._strong_terminal_reason(
                entity_id=current_id,
                direction=direction,
                entities=entities,
                relation_maps=relation_maps,
                path_relations=path_relations,
            )
            reached_frontier = len(expandable_neighbors) == 0
            hit_hop_ceiling = hop_count >= self._max_impact_hops

            if strong_terminal_reason is not None or reached_frontier or hit_hop_ceiling:
                truncation_reason = None
                terminal_reason = strong_terminal_reason or "frontier"
                is_truncated_path = False
                if strong_terminal_reason is None:
                    is_truncated_path = True
                    truncation_reason = "hop_limit" if hit_hop_ceiling else "frontier"
                candidates.append(
                    self._build_impact_path(
                        direction=direction,
                        path=list(path),
                        path_relations=list(path_relations),
                        entities=entities,
                        cost=cost,
                        composed_hops=composed_hops,
                        hop_count=hop_count,
                        terminal_reason=terminal_reason,
                        truncated=is_truncated_path,
                        truncation_reason=truncation_reason,
                    )
                )
                if is_truncated_path:
                    truncated = True
                if len(candidates) >= limit:
                    break
                continue

            for neighbor_id, relation_type in sorted(
                expandable_neighbors,
                key=lambda item: (
                    self._relation_weight(item[1]),
                    entities[item[0]].qualified_name,
                ),
            ):
                next_cost = cost + self._relation_weight(relation_type)
                next_composed = composed_hops + int(
                    relation_type == RelationType.COMPOSED_OF.value
                )
                next_imports = import_hops + int(
                    relation_type == RelationType.IMPORTS.value
                )
                next_hops = hop_count + 1
                if next_hops > self._max_impact_hops:
                    truncated = True
                    continue
                heapq.heappush(
                    heap,
                    (
                        next_cost,
                        next_hops,
                        next_composed,
                        next_imports,
                        expanded,
                        (*path, neighbor_id),
                        (*path_relations, relation_type),
                    ),
                )

        if heap:
            truncated = True

        ordered = sorted(
            candidates,
            key=lambda item: (
                item.cost,
                item.evidence_level != "strong",
                item.truncated,
                item.composed_hops,
                item.hop_count,
                item.terminal_qualified_name,
                self._path_signature(item),
            ),
        )
        return ordered[:limit], truncated

    def _impact_neighbors(
        self,
        *,
        entity_id: str,
        direction: str,
        relation_maps: dict[str, dict[str, list[tuple[str, str]]]],
    ) -> list[tuple[str, str]]:
        return list(relation_maps[direction].get(entity_id, []))

    def _strong_terminal_reason(
        self,
        *,
        entity_id: str,
        direction: str,
        entities: dict[str, CodeEntity],
        relation_maps: dict[str, dict[str, list[tuple[str, str]]]],
        path_relations: tuple[str, ...],
    ) -> Optional[str]:
        entity = entities[entity_id]
        if direction == "upstream" and entity.entity_type == EntityType.MODULE:
            return "module_root"
        if RelationType.INHERITS.value in path_relations and entity.entity_type == EntityType.CLASS:
            if direction == "upstream" and not relation_maps["upstream_inherits"].get(
                entity_id
            ):
                return "inheritance_root"
            if direction == "downstream" and not relation_maps["downstream_inherits"].get(
                entity_id
            ):
                return "inheritance_leaf"
        if entity.entity_type in {EntityType.FUNCTION, EntityType.METHOD}:
            primary_key = f"{direction}_primary"
            if relation_maps[primary_key].get(entity_id):
                return None
            if direction == "upstream" and relation_maps["upstream"].get(entity_id):
                return None
            if direction == "downstream" and relation_maps["downstream"].get(entity_id):
                return None
            return "call_boundary"
        return None

    def _build_impact_path(
        self,
        *,
        direction: str,
        path: list[str],
        path_relations: list[str],
        entities: dict[str, CodeEntity],
        cost: int,
        composed_hops: int,
        hop_count: int,
        terminal_reason: str,
        truncated: bool,
        truncation_reason: Optional[str],
    ) -> GlobalImpactPath:
        display_path = list(reversed(path)) if direction == "upstream" else path
        display_relations = (
            list(reversed(path_relations)) if direction == "upstream" else path_relations
        )
        steps = [self._to_path_step(entities[entity_id]) for entity_id in display_path]
        terminal = entities[display_path[0] if direction == "upstream" else display_path[-1]]
        return GlobalImpactPath(
            direction=direction,
            steps=steps,
            step_relations=display_relations,
            cost=cost,
            hop_count=hop_count,
            composed_hops=composed_hops,
            terminal_entity_id=terminal.entity_id,
            terminal_qualified_name=terminal.qualified_name,
            terminal_reason=terminal_reason,
            evidence_level=self._classify_evidence(
                path_relations=path_relations,
                terminal_reason=terminal_reason,
                truncated=truncated,
            ),
            truncated=truncated,
            truncation_reason=truncation_reason,
        )

    def _to_graph_node(self, entity: CodeEntity) -> GraphNode:
        return GraphNode(
            entity_id=entity.entity_id,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type.value,
        )

    def _to_path_step(self, entity: CodeEntity) -> GlobalImpactPathStep:
        return GlobalImpactPathStep(
            entity_id=entity.entity_id,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type.value,
        )

    def _path_signature(self, path: GlobalImpactPath) -> str:
        return " > ".join(step.qualified_name for step in path.steps)

    def _classify_evidence(
        self,
        *,
        path_relations: list[str],
        terminal_reason: str,
        truncated: bool,
    ) -> str:
        if truncated:
            return "weak"
        if terminal_reason == "frontier":
            return "weak"
        if any(
            relation_type in {RelationType.CALLS.value, RelationType.INHERITS.value}
            for relation_type in path_relations
        ):
            return "strong"
        return "weak"

    def _relation_weight(self, relation_type: str) -> int:
        return self._RELATION_WEIGHTS.get(relation_type, 5)

    def _register_impact_edge(
        self,
        mapping: dict[str, list[tuple[str, str]]],
        source_id: str,
        target_id: str,
        relation_type: str,
    ) -> None:
        mapping.setdefault(source_id, [])
        edge = (target_id, relation_type)
        if edge not in mapping[source_id]:
            mapping[source_id].append(edge)


class _MetricBucket:
    def __init__(self, sample_limit: int):
        self._sample_limit = sample_limit
        self._total_ms: deque[float] = deque(maxlen=sample_limit)
        self._requests = 0
        self._errors = 0
        self._truncated = 0
        self._paths = 0
        self._strong_paths = 0
        self._weak_paths = 0
        self._truncated_paths = 0
        self._truncated_frontier_paths = 0
        self._truncated_hop_limit_paths = 0
        self._truncated_expansion_paths = 0
        self._edge_counts = {
            RelationType.CALLS.value: 0,
            RelationType.INHERITS.value: 0,
            RelationType.COMPOSED_OF.value: 0,
            RelationType.IMPORTS.value: 0,
        }

    def record(
        self,
        *,
        total_ms: float,
        truncated: bool,
        error: bool,
        paths: Optional[list[GlobalImpactPath]] = None,
    ) -> None:
        self._requests += 1
        if error:
            self._errors += 1
        if truncated:
            self._truncated += 1
        self._total_ms.append(total_ms)
        for path in paths or []:
            self._paths += 1
            if path.evidence_level == "strong":
                self._strong_paths += 1
            else:
                self._weak_paths += 1
            if path.truncated:
                self._truncated_paths += 1
                if path.truncation_reason == "frontier":
                    self._truncated_frontier_paths += 1
                elif path.truncation_reason == "hop_limit":
                    self._truncated_hop_limit_paths += 1
                elif path.truncation_reason == "expansion_limit":
                    self._truncated_expansion_paths += 1
            for relation_type in path.step_relations:
                if relation_type in self._edge_counts:
                    self._edge_counts[relation_type] += 1

    def snapshot(self) -> dict[str, float | int | None]:
        ordered = sorted(self._total_ms)
        p95 = None
        if len(ordered) >= 1:
            index = int(round((len(ordered) - 1) * 0.95))
            p95 = round(float(ordered[index]), 2)
        return {
            "requests": self._requests,
            "errors": self._errors,
            "truncated": self._truncated,
            "avg_ms": round(mean(self._total_ms), 2) if self._total_ms else 0.0,
            "p95_ms": p95,
            "paths": self._paths,
            "strong_paths": self._strong_paths,
            "weak_paths": self._weak_paths,
            "truncated_paths": self._truncated_paths,
            "truncated_frontier_paths": self._truncated_frontier_paths,
            "truncated_hop_limit_paths": self._truncated_hop_limit_paths,
            "truncated_expansion_paths": self._truncated_expansion_paths,
            "edge_calls": self._edge_counts[RelationType.CALLS.value],
            "edge_inherits": self._edge_counts[RelationType.INHERITS.value],
            "edge_composed_of": self._edge_counts[RelationType.COMPOSED_OF.value],
            "edge_imports": self._edge_counts[RelationType.IMPORTS.value],
        }

    def reset(self) -> None:
        self._total_ms.clear()
        self._requests = 0
        self._errors = 0
        self._truncated = 0
        self._paths = 0
        self._strong_paths = 0
        self._weak_paths = 0
        self._truncated_paths = 0
        self._truncated_frontier_paths = 0
        self._truncated_hop_limit_paths = 0
        self._truncated_expansion_paths = 0
        for relation_type in self._edge_counts:
            self._edge_counts[relation_type] = 0


__all__ = ["GraphMetricsTracker", "GraphService"]
