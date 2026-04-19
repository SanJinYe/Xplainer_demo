"""Graph-service adapter for runtime capability registration."""

from typing import Optional

from tailevents.models.protocols import GraphServiceProtocol


class GraphCapability:
    """Wrap the existing graph service as an internal capability."""

    name = "graph"

    def __init__(
        self,
        service: Optional[GraphServiceProtocol] = None,
    ) -> None:
        self._service = service

    @property
    def available(self) -> bool:
        return self._service is not None

    async def get_subgraph(self, entity_id: str, depth: int = 2):
        if self._service is None:
            raise ValueError("Graph capability is not configured")
        return await self._service.get_subgraph(entity_id, depth=depth)

    async def get_impact_paths(
        self,
        entity_id: str,
        direction: str = "both",
        limit: int = 3,
    ):
        if self._service is None:
            raise ValueError("Graph capability is not configured")
        return await self._service.get_impact_paths(
            entity_id,
            direction=direction,
            limit=limit,
        )


__all__ = ["GraphCapability"]
