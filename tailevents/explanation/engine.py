"""Main explanation engine implementation."""

from typing import Optional

from tailevents.explanation.context_assembler import ContextAssembler, VALID_DETAIL_LEVELS
from tailevents.explanation.exceptions import (
    EntityExplanationNotFoundError,
    InvalidDetailLevelError,
)
from tailevents.explanation.formatter import ExplanationFormatter
from tailevents.explanation.prompts import (
    EXPLANATION_PROMPT_VERSION,
    EXTERNAL_DOC_PROMPT,
    PROMPT_TEMPLATES,
    SYSTEM_PROMPT,
)
from tailevents.models.entity import CodeEntity
from tailevents.models.event import ExternalRef, TailEvent
from tailevents.models.explanation import EntityExplanation
from tailevents.models.protocols import (
    CacheProtocol,
    DocRetrieverProtocol,
    EntityDBProtocol,
    EventStoreProtocol,
    ExplanationEngineProtocol,
    LLMClientProtocol,
    RelationStoreProtocol,
)


class ExplanationEngine(ExplanationEngineProtocol):
    """Generate explanations for indexed entities."""

    def __init__(
        self,
        entity_db: EntityDBProtocol,
        event_store: EventStoreProtocol,
        relation_store: RelationStoreProtocol,
        cache: Optional[CacheProtocol],
        llm_client: LLMClientProtocol,
        doc_retriever: DocRetrieverProtocol,
        context_assembler: Optional[ContextAssembler] = None,
        formatter: Optional[ExplanationFormatter] = None,
        max_events: int = 20,
        temperature: float = 0.3,
        cache_ttl: Optional[int] = None,
        cache_enabled: bool = True,
    ):
        self._entity_db = entity_db
        self._event_store = event_store
        self._relation_store = relation_store
        self._cache = cache
        self._llm_client = llm_client
        self._doc_retriever = doc_retriever
        self._context_assembler = context_assembler or ContextAssembler()
        self._formatter = formatter or ExplanationFormatter()
        self._max_events = max_events
        self._temperature = temperature
        self._cache_ttl = cache_ttl
        self._cache_enabled = cache_enabled

    async def explain_entity(
        self,
        entity_id: str,
        detail_level: str = "summary",
        include_relations: bool = False,
    ) -> EntityExplanation:
        self._validate_detail_level(detail_level)
        cache_key = self._build_cache_key(entity_id, detail_level, include_relations)

        cached = await self._get_cached_explanation(cache_key)
        if cached is not None:
            return cached

        entity = await self._entity_db.get(entity_id)
        if entity is None:
            raise EntityExplanationNotFoundError(f"Entity not found: {entity_id}")

        events = await self._load_events(entity)
        doc_snippets = await self._load_doc_snippets(events)
        related_entities = (
            await self._load_related_entities(entity.entity_id)
            if include_relations
            else []
        )

        context = self._context_assembler.assemble(
            entity=entity,
            events=events,
            related_entities=related_entities,
            doc_snippets=doc_snippets,
            detail_level=detail_level,
        )
        user_prompt = self._build_user_prompt(detail_level, context, doc_snippets)
        raw_output = await self._llm_client.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=self._max_tokens(detail_level),
            temperature=self._temperature,
        )

        explanation = self._formatter.format(entity, raw_output)
        explanation.creation_intent = explanation.creation_intent or self._creation_intent(
            events
        )
        explanation.modification_history = self._build_modification_history(events)
        explanation.related_entities = related_entities
        explanation.external_doc_snippets = doc_snippets
        explanation.from_cache = False

        await self._entity_db.update_description(entity.entity_id, explanation.summary)
        await self._put_cached_explanation(cache_key, explanation)
        return explanation

    async def explain_entities(
        self,
        entity_ids: list[str],
        detail_level: str = "summary",
        include_relations: bool = False,
    ) -> list[EntityExplanation]:
        explanations = []
        for entity_id in entity_ids:
            explanation = await self.explain_entity(
                entity_id=entity_id,
                detail_level=detail_level,
                include_relations=include_relations,
            )
            explanations.append(explanation)
        return explanations

    async def _get_cached_explanation(self, cache_key: str) -> Optional[EntityExplanation]:
        if not self._cache_enabled or self._cache is None:
            return None

        cached_value = await self._cache.get(cache_key)
        if cached_value is None:
            return None

        try:
            explanation = EntityExplanation.model_validate_json(cached_value)
        except Exception:
            await self._cache.invalidate(cache_key)
            return None
        return explanation.model_copy(update={"from_cache": True})

    async def _put_cached_explanation(
        self, cache_key: str, explanation: EntityExplanation
    ) -> None:
        if not self._cache_enabled or self._cache is None:
            return
        payload = explanation.model_copy(update={"from_cache": False}).model_dump_json()
        await self._cache.put(cache_key, payload, ttl=self._cache_ttl)

    async def _load_events(self, entity: CodeEntity) -> list[TailEvent]:
        event_ids = [reference.event_id for reference in entity.event_refs]
        if not event_ids:
            return []

        events = await self._event_store.get_batch(event_ids)
        ordered_events = sorted(events, key=lambda item: item.timestamp)
        if self._max_events <= 0 or len(ordered_events) <= self._max_events:
            return ordered_events
        if self._max_events == 1:
            return [ordered_events[0]]
        return [ordered_events[0]] + ordered_events[-(self._max_events - 1) :]

    async def _load_doc_snippets(self, events: list[TailEvent]) -> list[dict]:
        snippets = []
        seen: set[tuple[str, str]] = set()

        for event in events:
            for external_ref in event.external_refs:
                cache_key = (external_ref.package, external_ref.symbol)
                if cache_key in seen:
                    continue
                seen.add(cache_key)

                snippet = await self._doc_retriever.retrieve(
                    external_ref.package,
                    external_ref.symbol,
                )
                if snippet is None:
                    continue
                snippets.append(
                    self._external_ref_to_dict(
                        external_ref,
                        snippet,
                    )
                )

        return snippets

    async def _load_related_entities(self, entity_id: str) -> list[dict]:
        related_entities = []
        seen: set[tuple[str, str, str]] = set()

        outgoing_relations = await self._relation_store.get_outgoing(entity_id)
        incoming_relations = await self._relation_store.get_incoming(entity_id)

        for direction, relations in (
            ("outgoing", outgoing_relations),
            ("incoming", incoming_relations),
        ):
            for relation in relations:
                other_id = relation.target if direction == "outgoing" else relation.source
                key = (direction, relation.relation_type.value, other_id)
                if key in seen:
                    continue
                seen.add(key)

                related_entity = await self._entity_db.get(other_id)
                if related_entity is None or related_entity.is_deleted:
                    continue
                related_entities.append(
                    {
                        "entity_id": related_entity.entity_id,
                        "entity_name": related_entity.name,
                        "qualified_name": related_entity.qualified_name,
                        "entity_type": related_entity.entity_type.value,
                        "direction": direction,
                        "relation_type": relation.relation_type.value,
                        "confidence": relation.confidence,
                        "context": relation.context,
                    }
                )

        return related_entities

    def _build_user_prompt(
        self, detail_level: str, context: str, doc_snippets: list[dict]
    ) -> str:
        template = PROMPT_TEMPLATES[detail_level]
        prompt = template.format(context=context)
        if doc_snippets:
            prompt = (
                f"{prompt}\n\n"
                f"{EXTERNAL_DOC_PROMPT.format(external_context=self._format_external_context(doc_snippets))}"
            )
        return prompt

    def _build_modification_history(self, events: list[TailEvent]) -> list[dict]:
        if len(events) <= 1:
            return []

        history = []
        for event in events[1:]:
            history.append(
                {
                    "event_id": event.event_id,
                    "timestamp": event.timestamp,
                    "action_type": event.action_type.value,
                    "intent": event.intent,
                    "reasoning": event.reasoning,
                    "decision_alternatives": event.decision_alternatives or [],
                }
            )
        return history

    def _creation_intent(self, events: list[TailEvent]) -> Optional[str]:
        if not events:
            return None
        return events[0].intent

    def _external_ref_to_dict(self, external_ref: ExternalRef, snippet: str) -> dict:
        return {
            "package": external_ref.package,
            "symbol": external_ref.symbol,
            "version": external_ref.version,
            "doc_uri": external_ref.doc_uri,
            "usage_pattern": external_ref.usage_pattern.value,
            "snippet": snippet,
        }

    def _format_external_context(self, doc_snippets: list[dict]) -> str:
        lines = []
        for snippet in doc_snippets:
            lines.append(f"{snippet['package']}.{snippet['symbol']}")
            lines.append(snippet["snippet"])
        return "\n".join(lines)

    def _build_cache_key(
        self, entity_id: str, detail_level: str, include_relations: bool
    ) -> str:
        return (
            f"explain:{EXPLANATION_PROMPT_VERSION}:"
            f"{entity_id}:{detail_level}:{int(include_relations)}"
        )

    def _validate_detail_level(self, detail_level: str) -> None:
        if detail_level not in VALID_DETAIL_LEVELS:
            raise InvalidDetailLevelError(f"Unsupported detail level: {detail_level}")

    def _max_tokens(self, detail_level: str) -> int:
        if detail_level == "summary":
            return 600
        if detail_level == "trace":
            return 1400
        return 1000


__all__ = ["ExplanationEngine"]
