"""Stub graph service for Requirement B placeholders."""

from tailevents.models.protocols import GraphServiceProtocol


class GraphServiceStub(GraphServiceProtocol):
    """Placeholder graph service until real graph analysis is implemented."""

    async def get_subgraph(self, entity_id: str, depth: int = 2) -> dict:
        """Return an empty subgraph.

        A future implementation will traverse relation neighbors up to ``depth``
        and return a graph payload with populated nodes and edges.
        """

        return {
            "entity_id": entity_id,
            "depth": depth,
            "nodes": [],
            "edges": [],
            "implemented": False,
        }

    async def get_impact_paths(
        self,
        entity_id: str,
        direction: str = "both",
        limit: int = 3,
    ) -> list[dict]:
        _ = entity_id
        _ = direction
        _ = limit
        return []

    async def get_isolated_entities(self) -> list[str]:
        """Return no isolated entities.

        A future implementation will find entities with both in-degree and
        out-degree equal to zero.
        """

        return []

    async def get_single_dependency_entities(self) -> list[str]:
        """Return no single-dependency entities.

        A future implementation will detect entities with extremely small local
        neighborhoods for simplification analysis.
        """

        return []

    async def detect_cycles(self) -> list[list[str]]:
        """Raise until cycle detection exists.

        A future implementation will inspect the relation graph and return every
        strongly connected cycle found in the codebase.
        """

        raise NotImplementedError("Graph analysis not yet implemented")

    async def get_communities(self) -> list[list[str]]:
        """Raise until community detection exists.

        A future implementation will cluster entities into graph communities for
        higher-level architectural summaries.
        """

        raise NotImplementedError("Graph analysis not yet implemented")

    async def get_entity_importance(self, entity_id: str) -> dict:
        """Return an empty importance payload.

        A future implementation will compute graph-based centrality metrics for
        the requested entity.
        """

        return {
            "entity_id": entity_id,
            "implemented": False,
        }


__all__ = ["GraphServiceStub"]
