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
        error: bool = False,
    ) -> None:
        self._impact_paths.record(total_ms=total_ms, truncated=truncated, error=error)

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
                error=False,
            )
            return paths
        except Exception:
            self._telemetry.record_impact_paths(
                total_ms=(time.perf_counter() - started_at) * 1000,
                truncated=truncated,
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

    def _build_impact_maps(self, relations: list[Relation]) -> dict[str, dict[str, list[tuple[str, str]]]]:
        forward_calls: dict[str, list[tuple[str, str]]] = {}
        reverse_calls: dict[str, list[tuple[str, str]]] = {}
        composed_any: dict[str, list[tuple[str, str]]] = {}

        for relation in relations:
            if not relation.is_active:
                continue
            relation_name = relation.relation_type.value
            if relation.relation_type == RelationType.CALLS:
                forward_calls.setdefault(relation.source, []).append((relation.target, relation_name))
                reverse_calls.setdefault(relation.target, []).append((relation.source, relation_name))
            elif relation.relation_type == RelationType.COMPOSED_OF:
                composed_any.setdefault(relation.source, []).append((relation.target, relation_name))
                composed_any.setdefault(relation.target, []).append((relation.source, relation_name))

        return {
            "forward_calls": forward_calls,
            "reverse_calls": reverse_calls,
            "composed_any": composed_any,
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
        if self._is_boundary(
            entity_id=start_id,
            direction=direction,
            entities=entities,
            relation_maps=relation_maps,
        ):
            entity = entities[start_id]
            return (
                [
                    GlobalImpactPath(
                        direction=direction,
                        steps=[self._to_path_step(entity)],
                        cost=0,
                        hop_count=0,
                        composed_hops=0,
                        terminal_entity_id=entity.entity_id,
                        terminal_qualified_name=entity.qualified_name,
                        truncated=False,
                    )
                ],
                False,
            )

        heap: list[tuple[int, int, int, int, tuple[str, ...]]] = []
        heapq.heappush(heap, (0, 0, 0, 0, (start_id,)))
        candidates: list[GlobalImpactPath] = []
        expanded = 0
        truncated = False

        while heap and expanded < self._max_expanded_nodes:
            cost, composed_hops, hop_count, _, path = heapq.heappop(heap)
            current_id = path[-1]
            expanded += 1

            if hop_count > self._max_impact_hops:
                truncated = True
                continue

            if self._is_boundary(
                entity_id=current_id,
                direction=direction,
                entities=entities,
                relation_maps=relation_maps,
            ):
                candidates.append(
                    self._build_impact_path(
                        direction=direction,
                        path=list(path),
                        entities=entities,
                        cost=cost,
                        composed_hops=composed_hops,
                        hop_count=hop_count,
                    )
                )
                if len(candidates) >= limit:
                    break
                continue

            for neighbor_id, relation_type in self._impact_neighbors(
                entity_id=current_id,
                direction=direction,
                relation_maps=relation_maps,
            ):
                if neighbor_id not in entities or neighbor_id in path:
                    continue

                next_cost = cost + (2 if relation_type == RelationType.COMPOSED_OF.value else 1)
                next_composed = composed_hops + int(
                    relation_type == RelationType.COMPOSED_OF.value
                )
                next_hops = hop_count + 1
                if next_hops > self._max_impact_hops:
                    truncated = True
                    continue
                heapq.heappush(
                    heap,
                    (
                        next_cost,
                        next_composed,
                        next_hops,
                        expanded,
                        (*path, neighbor_id),
                    ),
                )

        if heap:
            truncated = True

        ordered = sorted(
            candidates,
            key=lambda item: (
                item.cost,
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
        neighbors: list[tuple[str, str]] = []
        if direction == "upstream":
            neighbors.extend(relation_maps["reverse_calls"].get(entity_id, []))
        else:
            neighbors.extend(relation_maps["forward_calls"].get(entity_id, []))
        neighbors.extend(relation_maps["composed_any"].get(entity_id, []))
        return neighbors

    def _is_boundary(
        self,
        *,
        entity_id: str,
        direction: str,
        entities: dict[str, CodeEntity],
        relation_maps: dict[str, dict[str, list[tuple[str, str]]]],
    ) -> bool:
        entity = entities[entity_id]
        if entity.entity_type not in {EntityType.FUNCTION, EntityType.METHOD}:
            return False
        if direction == "upstream":
            return len(relation_maps["reverse_calls"].get(entity_id, [])) == 0
        return len(relation_maps["forward_calls"].get(entity_id, [])) == 0

    def _build_impact_path(
        self,
        *,
        direction: str,
        path: list[str],
        entities: dict[str, CodeEntity],
        cost: int,
        composed_hops: int,
        hop_count: int,
    ) -> GlobalImpactPath:
        display_path = list(reversed(path)) if direction == "upstream" else path
        steps = [self._to_path_step(entities[entity_id]) for entity_id in display_path]
        terminal = entities[display_path[0] if direction == "upstream" else display_path[-1]]
        return GlobalImpactPath(
            direction=direction,
            steps=steps,
            cost=cost,
            hop_count=hop_count,
            composed_hops=composed_hops,
            terminal_entity_id=terminal.entity_id,
            terminal_qualified_name=terminal.qualified_name,
            truncated=False,
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


class _MetricBucket:
    def __init__(self, sample_limit: int):
        self._sample_limit = sample_limit
        self._total_ms: deque[float] = deque(maxlen=sample_limit)
        self._requests = 0
        self._errors = 0
        self._truncated = 0

    def record(self, *, total_ms: float, truncated: bool, error: bool) -> None:
        self._requests += 1
        if error:
            self._errors += 1
        if truncated:
            self._truncated += 1
        self._total_ms.append(total_ms)

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
        }

    def reset(self) -> None:
        self._total_ms.clear()
        self._requests = 0
        self._errors = 0
        self._truncated = 0


__all__ = ["GraphMetricsTracker", "GraphService"]
