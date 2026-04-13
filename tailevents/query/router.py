"""Route explanation requests to the appropriate resolver path."""

from tailevents.models.explanation import (
    EntityExplanation,
    ExplanationRequest,
    ExplanationResponse,
)
from tailevents.models.protocols import EntityDBProtocol, ExplanationEngineProtocol
from tailevents.query.location_resolver import LocationResolver
from tailevents.query.symbol_resolver import SymbolResolver


class QueryRouter:
    """Resolve request inputs to entity ids, then delegate to the explanation engine."""

    def __init__(
        self,
        entity_db: EntityDBProtocol,
        explanation_engine: ExplanationEngineProtocol,
    ):
        self._entity_db = entity_db
        self._explanation_engine = explanation_engine
        self._location_resolver = LocationResolver(entity_db)
        self._symbol_resolver = SymbolResolver(entity_db)

    async def route(self, request: ExplanationRequest) -> ExplanationResponse:
        entity_ids = await self._resolve_entity_ids(request)
        explanations: list[EntityExplanation] = []

        if entity_ids:
            explanations = await self._explanation_engine.explain_entities(
                entity_ids=entity_ids,
                detail_level=request.detail_level,
                include_relations=request.include_relations,
            )

        return ExplanationResponse(
            request=request,
            explanations=explanations,
        )

    async def _resolve_entity_ids(self, request: ExplanationRequest) -> list[str]:
        if request.file_path and request.line_number is not None:
            entity_id = await self._location_resolver.resolve(
                request.file_path,
                request.line_number,
            )
            return [] if entity_id is None else [entity_id]

        if request.cursor_word:
            return await self._dedupe(self._symbol_resolver.resolve(request.cursor_word))

        if request.query:
            entities = await self._entity_db.search(request.query)
            return self._dedupe_ids([entity.entity_id for entity in entities])

        return []

    async def _dedupe(self, resolver_call) -> list[str]:
        entity_ids = await resolver_call
        return self._dedupe_ids(entity_ids)

    def _dedupe_ids(self, entity_ids: list[str]) -> list[str]:
        return list(dict.fromkeys(entity_ids))


__all__ = ["QueryRouter"]
