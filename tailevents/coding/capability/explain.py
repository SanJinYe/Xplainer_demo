"""Explanation-engine adapter for runtime capability registration."""

from typing import Optional

from tailevents.models.protocols import ExplanationEngineProtocol


class ExplanationCapability:
    """Wrap the existing explanation engine as an internal capability."""

    name = "explain"

    def __init__(
        self,
        engine: Optional[ExplanationEngineProtocol] = None,
    ) -> None:
        self._engine = engine

    @property
    def available(self) -> bool:
        return self._engine is not None

    async def explain_entity(
        self,
        entity_id: str,
        detail_level: str = "summary",
        include_relations: bool = False,
        profile_id: Optional[str] = None,
    ):
        if self._engine is None:
            raise ValueError("Explanation capability is not configured")
        return await self._engine.explain_entity(
            entity_id,
            detail_level=detail_level,
            include_relations=include_relations,
            profile_id=profile_id,
        )


__all__ = ["ExplanationCapability"]
