"""Main explanation engine implementation."""

import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from tailevents.explanation.context_assembler import ContextAssembler, VALID_DETAIL_LEVELS
from tailevents.explanation.exceptions import (
    EntityExplanationNotFoundError,
    InvalidDetailLevelError,
    LLMClientError,
)
from tailevents.explanation.formatter import ExplanationFormatter
from tailevents.explanation.prompts import (
    EXPLANATION_PROMPT_VERSION,
    EXTERNAL_DOC_PROMPT,
    PROMPT_TEMPLATES,
    SYSTEM_PROMPT,
)
from tailevents.explanation.telemetry import ExplanationMetricsTracker
from tailevents.models.entity import CodeEntity
from tailevents.models.enums import ActionType
from tailevents.models.event import ExternalRef, TailEvent
from tailevents.models.explanation import (
    EntityExplanation,
    ExplanationStreamDelta,
    ExplanationStreamDone,
    ExplanationStreamError,
    ExplanationStreamEvent,
    ExplanationStreamInit,
)
from tailevents.models.protocols import (
    CacheProtocol,
    DocRetrieverProtocol,
    EntityDBProtocol,
    EventStoreProtocol,
    ExplanationEngineProtocol,
    LLMClientProtocol,
    RelationStoreProtocol,
)


@dataclass
class _PreparedExplanation:
    entity: CodeEntity
    events: list[TailEvent]
    related_entities: list[dict]
    doc_snippets: list[dict]
    user_prompt: str


class _DetailedStreamSession:
    """Single-process shared stream session for one detailed cache key."""

    def __init__(self, init_event: ExplanationStreamInit):
        self._history: list[ExplanationStreamEvent] = [init_event]
        self._subscribers: set[asyncio.Queue[Optional[ExplanationStreamEvent]]] = set()
        self._result: asyncio.Future[EntityExplanation] = (
            asyncio.get_running_loop().create_future()
        )
        self._closed = False

    def subscribe(self) -> asyncio.Queue[Optional[ExplanationStreamEvent]]:
        queue: asyncio.Queue[Optional[ExplanationStreamEvent]] = asyncio.Queue()
        for event in self._history:
            queue.put_nowait(event)
        if self._closed:
            queue.put_nowait(None)
        else:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Optional[ExplanationStreamEvent]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: ExplanationStreamEvent) -> None:
        if self._closed:
            return
        self._history.append(event)
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    def finish_with_result(self, explanation: EntityExplanation) -> None:
        if not self._result.done():
            self._result.set_result(explanation)
        self._close()

    def finish_with_error(self, error: Exception) -> None:
        if not self._result.done():
            self._result.set_exception(error)
        self._close()

    async def result(self) -> EntityExplanation:
        return await self._result

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for queue in list(self._subscribers):
            queue.put_nowait(None)
        self._subscribers.clear()


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
        llm_backend_name: str = "ollama",
        llm_model_name: str = "",
        detailed_concurrency: int = 1,
        stream_flush_chars: int = 40,
        stream_flush_ms: int = 100,
        stream_stall_timeout_ms: int = 30_000,
        telemetry: Optional[ExplanationMetricsTracker] = None,
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
        self._llm_backend_name = llm_backend_name
        self._llm_model_name = llm_model_name
        self._detailed_semaphore = asyncio.Semaphore(max(1, detailed_concurrency))
        self._stream_flush_chars = max(1, stream_flush_chars)
        self._stream_flush_ms = max(1, stream_flush_ms)
        self._stream_stall_timeout_s = max(stream_stall_timeout_ms, 1) / 1000.0
        self._telemetry = telemetry or ExplanationMetricsTracker()
        self._detailed_sessions: dict[str, _DetailedStreamSession] = {}
        self._detailed_sessions_lock = asyncio.Lock()

    async def explain_entity(
        self,
        entity_id: str,
        detail_level: str = "summary",
        include_relations: bool = False,
    ) -> EntityExplanation:
        self._validate_detail_level(detail_level)
        if detail_level == "summary":
            return await self._explain_summary_fast(entity_id)
        if detail_level == "detailed":
            return await self._explain_detailed(entity_id, include_relations)
        return await self._explain_blocking(entity_id, detail_level, include_relations)

    async def stream_explain_entity(
        self,
        entity_id: str,
        include_relations: bool = True,
    ) -> AsyncIterator[ExplanationStreamEvent]:
        started_at = time.perf_counter()
        first_event_at: Optional[float] = None
        output_chars = 0
        cache_hit = False
        saw_error = False

        cache_key = self._build_cache_key(entity_id, "detailed", include_relations)
        cached = await self._get_cached_explanation(cache_key)
        if cached is not None:
            entity = await self._get_entity_or_raise(entity_id)
            events = await self._load_events(entity)
            init_summary, _ = self._build_fast_summary(entity, events)
            init_event = self._build_stream_init(entity=entity, summary=init_summary)
            first_event_at = time.perf_counter()
            output_chars = len(cached.detailed_explanation or "")
            cache_hit = True
            try:
                yield init_event
                yield ExplanationStreamDone(explanation=cached)
            finally:
                self._telemetry.record_detailed_stream(
                    total_ms=(time.perf_counter() - started_at) * 1000,
                    first_token_ms=(first_event_at - started_at) * 1000,
                    output_chars=output_chars,
                    cache_hit=cache_hit,
                    error=False,
                )
            return

        session = await self._get_or_create_detailed_session(entity_id, include_relations)
        queue = session.subscribe()
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                if first_event_at is None:
                    first_event_at = time.perf_counter()
                if isinstance(event, ExplanationStreamDelta):
                    output_chars += len(event.text)
                elif isinstance(event, ExplanationStreamDone):
                    output_chars = len(event.explanation.detailed_explanation or "")
                elif isinstance(event, ExplanationStreamError):
                    saw_error = True
                yield event
                if isinstance(event, (ExplanationStreamDone, ExplanationStreamError)):
                    break
        finally:
            session.unsubscribe(queue)
            completed_first_event_at = first_event_at or time.perf_counter()
            self._telemetry.record_detailed_stream(
                total_ms=(time.perf_counter() - started_at) * 1000,
                first_token_ms=(completed_first_event_at - started_at) * 1000,
                output_chars=output_chars,
                cache_hit=cache_hit,
                error=saw_error,
            )

    async def explain_entities(
        self,
        entity_ids: list[str],
        detail_level: str = "summary",
        include_relations: bool = False,
    ) -> list[EntityExplanation]:
        explanations: list[EntityExplanation] = []
        for entity_id in entity_ids:
            explanations.append(
                await self.explain_entity(
                    entity_id=entity_id,
                    detail_level=detail_level,
                    include_relations=include_relations,
                )
            )
        return explanations

    def get_metrics(self) -> dict[str, dict[str, float | int | None]]:
        return self._telemetry.snapshot()

    def reset_metrics(self) -> None:
        self._telemetry.reset()

    async def _explain_summary_fast(self, entity_id: str) -> EntityExplanation:
        started_at = time.perf_counter()
        entity = await self._get_entity_or_raise(entity_id)
        events = await self._load_events(entity)
        summary, from_cache = self._build_fast_summary(entity, events)

        explanation = EntityExplanation(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type,
            signature=entity.signature,
            summary=summary or "",
            detailed_explanation=None,
            creation_intent=self._creation_intent(events),
            modification_history=[],
            related_entities=[],
            external_doc_snippets=[],
            from_cache=from_cache,
        )
        self._telemetry.record_summary(
            total_ms=(time.perf_counter() - started_at) * 1000,
            output_chars=len(explanation.summary),
            cache_hit=from_cache,
        )
        return explanation

    async def _explain_detailed(
        self,
        entity_id: str,
        include_relations: bool,
    ) -> EntityExplanation:
        cache_key = self._build_cache_key(entity_id, "detailed", include_relations)
        cached = await self._get_cached_explanation(cache_key)
        if cached is not None:
            return cached

        session = await self._get_or_create_detailed_session(entity_id, include_relations)
        explanation = await session.result()
        return explanation.model_copy(update={"from_cache": False})

    async def _explain_blocking(
        self,
        entity_id: str,
        detail_level: str,
        include_relations: bool,
    ) -> EntityExplanation:
        cache_key = self._build_cache_key(entity_id, detail_level, include_relations)
        cached = await self._get_cached_explanation(cache_key)
        if cached is not None:
            return cached

        prepared = await self._prepare_explanation(
            entity_id=entity_id,
            detail_level=detail_level,
            include_relations=include_relations,
        )
        raw_output = await self._llm_client.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prepared.user_prompt,
            max_tokens=self._max_tokens(detail_level),
            temperature=self._temperature,
        )
        explanation = self._build_final_explanation(
            entity=prepared.entity,
            events=prepared.events,
            related_entities=prepared.related_entities,
            doc_snippets=prepared.doc_snippets,
            raw_output=raw_output,
            detail_level=detail_level,
        )
        if detail_level == "detailed":
            await self._entity_db.update_description(
                prepared.entity.entity_id,
                explanation.summary,
            )
        await self._put_cached_explanation(cache_key, explanation)
        return explanation

    async def _get_or_create_detailed_session(
        self,
        entity_id: str,
        include_relations: bool,
    ) -> _DetailedStreamSession:
        cache_key = self._build_cache_key(entity_id, "detailed", include_relations)

        async with self._detailed_sessions_lock:
            existing = self._detailed_sessions.get(cache_key)
            if existing is not None:
                return existing

        prepared = await self._prepare_explanation(
            entity_id=entity_id,
            detail_level="detailed",
            include_relations=include_relations,
        )
        init_summary, _ = self._build_fast_summary(prepared.entity, prepared.events)
        init_event = self._build_stream_init(entity=prepared.entity, summary=init_summary)

        async with self._detailed_sessions_lock:
            existing = self._detailed_sessions.get(cache_key)
            if existing is not None:
                return existing
            session = _DetailedStreamSession(init_event)
            self._detailed_sessions[cache_key] = session
            asyncio.create_task(
                self._run_detailed_session(
                    cache_key=cache_key,
                    session=session,
                    prepared=prepared,
                )
            )
            return session

    async def _run_detailed_session(
        self,
        *,
        cache_key: str,
        session: _DetailedStreamSession,
        prepared: _PreparedExplanation,
    ) -> None:
        raw_chunks: list[str] = []
        flush_buffer = ""
        last_flush_at = time.perf_counter()

        try:
            async with self._detailed_semaphore:
                stream = self._llm_client.stream_generate(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prepared.user_prompt,
                    max_tokens=self._max_tokens("detailed"),
                    temperature=self._temperature,
                )
                iterator = stream.__aiter__()
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            iterator.__anext__(),
                            timeout=self._stream_stall_timeout_s,
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as error:
                        raise LLMClientError(
                            "Detailed explanation stream stalled for too long."
                        ) from error

                    if not chunk:
                        continue

                    raw_chunks.append(chunk)
                    flush_buffer += chunk
                    now = time.perf_counter()
                    should_flush = (
                        len(flush_buffer) >= self._stream_flush_chars
                        or (now - last_flush_at) * 1000 >= self._stream_flush_ms
                    )
                    if should_flush:
                        session.publish(ExplanationStreamDelta(text=flush_buffer))
                        flush_buffer = ""
                        last_flush_at = now

            if flush_buffer:
                session.publish(ExplanationStreamDelta(text=flush_buffer))

            explanation = self._build_final_explanation(
                entity=prepared.entity,
                events=prepared.events,
                related_entities=prepared.related_entities,
                doc_snippets=prepared.doc_snippets,
                raw_output="".join(raw_chunks),
                detail_level="detailed",
            )
            await self._entity_db.update_description(
                prepared.entity.entity_id,
                explanation.summary,
            )
            await self._put_cached_explanation(cache_key, explanation)
            session.publish(ExplanationStreamDone(explanation=explanation))
            session.finish_with_result(explanation)
        except Exception as error:  # noqa: BLE001
            session.publish(ExplanationStreamError(message=str(error)))
            session.finish_with_error(error)
        finally:
            async with self._detailed_sessions_lock:
                current = self._detailed_sessions.get(cache_key)
                if current is session:
                    self._detailed_sessions.pop(cache_key, None)

    async def _prepare_explanation(
        self,
        *,
        entity_id: str,
        detail_level: str,
        include_relations: bool,
    ) -> _PreparedExplanation:
        entity = await self._get_entity_or_raise(entity_id)
        events = await self._load_events(entity)
        doc_snippets = await self._load_doc_snippets(events)
        related_entities = (
            await self._load_related_entities(entity.entity_id)
            if include_relations
            else []
        )
        doc_snippets_for_prompt = doc_snippets if detail_level != "summary" else []
        context = self._context_assembler.assemble(
            entity=entity,
            events=events,
            related_entities=related_entities,
            doc_snippets=doc_snippets_for_prompt,
            detail_level=detail_level,
        )
        user_prompt = self._build_user_prompt(
            detail_level=detail_level,
            context=context,
            doc_snippets=doc_snippets_for_prompt,
            baseline_only=self._is_baseline_only(events),
        )
        return _PreparedExplanation(
            entity=entity,
            events=events,
            related_entities=related_entities,
            doc_snippets=doc_snippets,
            user_prompt=user_prompt,
        )

    async def _get_entity_or_raise(self, entity_id: str) -> CodeEntity:
        entity = await self._entity_db.get(entity_id)
        if entity is None:
            raise EntityExplanationNotFoundError(f"Entity not found: {entity_id}")
        return entity

    async def _get_cached_explanation(
        self,
        cache_key: str,
    ) -> Optional[EntityExplanation]:
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
        self,
        cache_key: str,
        explanation: EntityExplanation,
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
        snippets: list[dict] = []
        seen: set[tuple[str, str]] = set()

        for event in events:
            for external_ref in event.external_refs:
                dedupe_key = (external_ref.package, external_ref.symbol)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                snippet = await self._doc_retriever.retrieve(
                    external_ref.package,
                    external_ref.symbol,
                )
                if snippet is None:
                    continue
                snippets.append(self._external_ref_to_dict(external_ref, snippet))

        return snippets

    async def _load_related_entities(self, entity_id: str) -> list[dict]:
        related_entities: list[dict] = []
        seen: set[tuple[str, str, str]] = set()

        outgoing_relations = await self._relation_store.get_outgoing(entity_id)
        incoming_relations = await self._relation_store.get_incoming(entity_id)

        for direction, relations in (
            ("outgoing", outgoing_relations),
            ("incoming", incoming_relations),
        ):
            for relation in relations:
                other_id = relation.target if direction == "outgoing" else relation.source
                dedupe_key = (direction, relation.relation_type.value, other_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

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
        self,
        detail_level: str,
        context: str,
        doc_snippets: list[dict],
        baseline_only: bool,
    ) -> str:
        prompt = PROMPT_TEMPLATES[detail_level].format(context=context)
        if doc_snippets:
            prompt = (
                f"{prompt}\n\n"
                f"{EXTERNAL_DOC_PROMPT.format(external_context=self._format_external_context(doc_snippets))}"
            )
        if baseline_only:
            prompt = (
                f"{prompt}\n\n"
                "Additional constraints: the current context only contains a baseline event "
                "and has no explicit reasoning. Do not guess the original creation intent, "
                "design rationale, or discarded alternatives. Only describe the current code "
                "structure, behavior, and the directly observed context. "
                "不要猜测创建动机、设计动机或被放弃的备选方案。"
            )
        return prompt

    def _build_modification_history(self, events: list[TailEvent]) -> list[dict]:
        if len(events) <= 1:
            return []

        history: list[dict] = []
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
        lines: list[str] = []
        for snippet in doc_snippets:
            lines.append(f"{snippet['package']}.{snippet['symbol']}")
            lines.append(snippet["snippet"])
        return "\n".join(lines)

    def _build_cache_key(
        self,
        entity_id: str,
        detail_level: str,
        include_relations: bool,
    ) -> str:
        return (
            f"explain:{entity_id}:{detail_level}:{int(include_relations)}:"
            f"{EXPLANATION_PROMPT_VERSION}:{self._model_profile(detail_level)}"
        )

    def _model_profile(self, detail_level: str) -> str:
        model_name = self._llm_model_name or "default"
        return (
            f"{self._llm_backend_name}:{model_name}:"
            f"{self._max_tokens(detail_level)}:{self._temperature}"
        )

    def _validate_detail_level(self, detail_level: str) -> None:
        if detail_level not in VALID_DETAIL_LEVELS:
            raise InvalidDetailLevelError(f"Unsupported detail level: {detail_level}")

    def _max_tokens(self, detail_level: str) -> int:
        if detail_level == "summary":
            return 250
        if detail_level == "trace":
            return 1400
        return 1800

    def _is_baseline_only(self, events: list[TailEvent]) -> bool:
        return bool(
            len(events) == 1
            and events[0].action_type == ActionType.BASELINE
            and not events[0].reasoning
            and not events[0].decision_alternatives
        )

    def _build_fast_summary(
        self,
        entity: CodeEntity,
        events: list[TailEvent],
    ) -> tuple[Optional[str], bool]:
        if entity.description_valid and entity.cached_description:
            return entity.cached_description.strip(), True
        return self._build_deterministic_summary(events), False

    def _build_deterministic_summary(self, events: list[TailEvent]) -> Optional[str]:
        intents = [
            event.intent.strip()
            for event in events
            if event.action_type != ActionType.BASELINE and event.intent.strip()
        ]
        if not intents:
            return None
        if len(intents) == 1 or intents[0] == intents[-1]:
            return self._truncate_summary_text(intents[-1])
        return self._truncate_summary_text(
            f"Initial: {intents[0]}; Latest: {intents[-1]}"
        )

    def _truncate_summary_text(self, text: str, max_chars: int = 120) -> str:
        normalized = " ".join(text.split()).strip()
        if len(normalized) <= max_chars:
            return normalized
        if max_chars <= 3:
            return normalized[:max_chars]
        return f"{normalized[: max_chars - 3].rstrip()}..."

    def _build_stream_init(
        self,
        *,
        entity: CodeEntity,
        summary: Optional[str],
    ) -> ExplanationStreamInit:
        return ExplanationStreamInit(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type,
            signature=entity.signature,
            file_path=entity.file_path,
            line_range=entity.line_range,
            event_count=len(entity.event_refs),
            summary=summary,
        )

    def _build_final_explanation(
        self,
        *,
        entity: CodeEntity,
        events: list[TailEvent],
        related_entities: list[dict],
        doc_snippets: list[dict],
        raw_output: str,
        detail_level: str,
    ) -> EntityExplanation:
        explanation = self._formatter.format(
            entity,
            raw_output,
            detail_level=detail_level,
        )
        explanation.creation_intent = explanation.creation_intent or self._creation_intent(
            events
        )
        explanation.modification_history = self._build_modification_history(events)
        explanation.related_entities = related_entities
        explanation.external_doc_snippets = doc_snippets
        explanation.from_cache = False
        return explanation


__all__ = ["ExplanationEngine"]
