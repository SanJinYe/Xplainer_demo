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
from tailevents.graph.stub import GraphServiceStub
from tailevents.models.docs import ExternalDocChunk, ExternalDocMatch, ExternalDocSource
from tailevents.models.entity import CodeEntity
from tailevents.models.enums import ActionType, EntityType, RelationType
from tailevents.models.event import TailEvent
from tailevents.models.explanation import (
    EntityExplanation,
    ExplanationStreamDelta,
    ExplanationStreamDone,
    ExplanationStreamError,
    ExplanationStreamEvent,
    ExplanationStreamInit,
    HistorySource,
    LocalRelationContext,
    RelationContext,
    RelationContextItem,
)
from tailevents.models.graph import GraphSubgraphSummary
from tailevents.models.profile import ResolvedCodingProfile
from tailevents.models.protocols import (
    CacheProtocol,
    CodingProfileRegistryProtocol,
    DocRetrieverProtocol,
    EntityDBProtocol,
    EventStoreProtocol,
    ExplanationEngineProtocol,
    GraphServiceProtocol,
    LLMClientProtocol,
    RelationStoreProtocol,
)
from tailevents.storage.version_store import SQLiteVersionStore


@dataclass
class _PreparedExplanation:
    entity: CodeEntity
    all_events: list[TailEvent]
    prompt_events: list[TailEvent]
    history_source: HistorySource
    relation_context: RelationContext
    related_entities: list[dict]
    doc_snippets: list[ExternalDocMatch]
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
        doc_retriever: DocRetrieverProtocol,
        graph_service: Optional[GraphServiceProtocol] = None,
        version_store: Optional[SQLiteVersionStore] = None,
        profile_registry: Optional[CodingProfileRegistryProtocol] = None,
        llm_client: Optional[LLMClientProtocol] = None,
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
        self._graph_service = graph_service or GraphServiceStub()
        self._cache = cache
        self._profile_registry = profile_registry
        self._default_llm_client = llm_client
        self._doc_retriever = doc_retriever
        self._version_store = version_store
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
        if self._profile_registry is None and self._default_llm_client is None:
            raise ValueError("ExplanationEngine requires a profile registry or a default llm_client")

    async def explain_entity(
        self,
        entity_id: str,
        detail_level: str = "summary",
        include_relations: bool = False,
        profile_id: Optional[str] = None,
    ) -> EntityExplanation:
        self._validate_detail_level(detail_level)
        resolved_profile = self._resolve_profile(profile_id)
        if detail_level == "summary":
            return await self._explain_summary_fast(entity_id, resolved_profile)
        if detail_level == "detailed":
            return await self._explain_detailed(
                entity_id,
                include_relations,
                resolved_profile,
            )
        return await self._explain_blocking(
            entity_id,
            detail_level,
            include_relations,
            resolved_profile,
        )

    async def stream_explain_entity(
        self,
        entity_id: str,
        include_relations: bool = True,
        profile_id: Optional[str] = None,
    ) -> AsyncIterator[ExplanationStreamEvent]:
        started_at = time.perf_counter()
        first_event_at: Optional[float] = None
        output_chars = 0
        cache_hit = False
        saw_error = False
        resolved_profile = self._resolve_profile(profile_id)

        cache_key = await self._build_cache_key(
            entity_id,
            "detailed",
            include_relations,
            resolved_profile,
        )
        cached = await self._get_cached_explanation(cache_key)
        if cached is not None:
            entity = await self._get_entity_or_raise(entity_id)
            all_events = await self._load_all_events(entity)
            history_source = self._classify_history_source(all_events)
            init_summary, _ = self._build_fast_summary(entity, all_events)
            init_event = self._build_stream_init(
                entity=entity,
                summary=init_summary,
                history_source=history_source,
                resolved_profile=resolved_profile,
            )
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

        session = await self._get_or_create_detailed_session(
            entity_id,
            include_relations,
            resolved_profile,
        )
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
        profile_id: Optional[str] = None,
    ) -> list[EntityExplanation]:
        explanations: list[EntityExplanation] = []
        for entity_id in entity_ids:
            explanations.append(
                await self.explain_entity(
                    entity_id=entity_id,
                    detail_level=detail_level,
                    include_relations=include_relations,
                    profile_id=profile_id,
                )
            )
        return explanations

    def get_metrics(self) -> dict[str, dict[str, float | int | None]]:
        return self._telemetry.snapshot()

    def reset_metrics(self) -> None:
        self._telemetry.reset()

    async def _explain_summary_fast(
        self,
        entity_id: str,
        resolved_profile: ResolvedCodingProfile,
    ) -> EntityExplanation:
        started_at = time.perf_counter()
        entity = await self._get_entity_or_raise(entity_id)
        all_events = await self._load_all_events(entity)
        history_source = self._classify_history_source(all_events)
        summary, from_cache = self._build_fast_summary(entity, all_events)

        explanation = EntityExplanation(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type,
            signature=entity.signature,
            resolved_profile_id=resolved_profile.resolved_profile_id,
            summary=summary or "",
            detailed_explanation=None,
            creation_intent=self._creation_intent(all_events, history_source),
            modification_history=[],
            history_source=history_source,
            relation_context=self._empty_relation_context(),
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
        resolved_profile: ResolvedCodingProfile,
    ) -> EntityExplanation:
        cache_key = await self._build_cache_key(
            entity_id,
            "detailed",
            include_relations,
            resolved_profile,
        )
        cached = await self._get_cached_explanation(cache_key)
        if cached is not None:
            return cached

        session = await self._get_or_create_detailed_session(
            entity_id,
            include_relations,
            resolved_profile,
        )
        explanation = await session.result()
        return explanation.model_copy(update={"from_cache": False})

    async def _explain_blocking(
        self,
        entity_id: str,
        detail_level: str,
        include_relations: bool,
        resolved_profile: ResolvedCodingProfile,
    ) -> EntityExplanation:
        cache_key = await self._build_cache_key(
            entity_id,
            detail_level,
            include_relations,
            resolved_profile,
        )
        cached = await self._get_cached_explanation(cache_key)
        if cached is not None:
            return cached

        prepared = await self._prepare_explanation(
            entity_id=entity_id,
            detail_level=detail_level,
            include_relations=include_relations,
            resolved_profile=resolved_profile,
        )
        raw_output = await resolved_profile.llm_client.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prepared.user_prompt,
            max_tokens=self._max_tokens(detail_level),
            temperature=self._temperature,
        )
        explanation = self._build_final_explanation(
            entity=prepared.entity,
            all_events=prepared.all_events,
            history_source=prepared.history_source,
            relation_context=prepared.relation_context,
            related_entities=prepared.related_entities,
            doc_snippets=prepared.doc_snippets,
            raw_output=raw_output,
            detail_level=detail_level,
            resolved_profile=resolved_profile,
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
        resolved_profile: ResolvedCodingProfile,
    ) -> _DetailedStreamSession:
        cache_key = await self._build_cache_key(
            entity_id,
            "detailed",
            include_relations,
            resolved_profile,
        )

        async with self._detailed_sessions_lock:
            existing = self._detailed_sessions.get(cache_key)
            if existing is not None:
                return existing

        prepared = await self._prepare_explanation(
            entity_id=entity_id,
            detail_level="detailed",
            include_relations=include_relations,
            resolved_profile=resolved_profile,
        )
        init_summary, _ = self._build_fast_summary(prepared.entity, prepared.all_events)
        init_event = self._build_stream_init(
            entity=prepared.entity,
            summary=init_summary,
            history_source=prepared.history_source,
            resolved_profile=resolved_profile,
        )

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
                    resolved_profile=resolved_profile,
                )
            )
            return session

    async def _run_detailed_session(
        self,
        *,
        cache_key: str,
        session: _DetailedStreamSession,
        prepared: _PreparedExplanation,
        resolved_profile: ResolvedCodingProfile,
    ) -> None:
        raw_chunks: list[str] = []
        flush_buffer = ""
        last_flush_at = time.perf_counter()

        try:
            async with self._detailed_semaphore:
                stream = resolved_profile.llm_client.stream_generate(
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
                all_events=prepared.all_events,
                history_source=prepared.history_source,
                relation_context=prepared.relation_context,
                related_entities=prepared.related_entities,
                doc_snippets=prepared.doc_snippets,
                raw_output="".join(raw_chunks),
                detail_level="detailed",
                resolved_profile=resolved_profile,
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
        resolved_profile: ResolvedCodingProfile,
    ) -> _PreparedExplanation:
        entity = await self._get_entity_or_raise(entity_id)
        all_events = await self._load_all_events(entity)
        history_source = self._classify_history_source(all_events)
        prompt_events = self._select_prompt_events(
            all_events,
            detail_level=detail_level,
            history_source=history_source,
        )
        doc_snippets = await self._load_doc_snippets(prompt_events)
        relation_context = (
            await self._load_relation_context(entity.entity_id)
            if include_relations
            else self._empty_relation_context()
        )
        related_entities = self._derive_related_entities(relation_context)
        doc_snippets_for_prompt = doc_snippets if detail_level != "summary" else []
        context = self._context_assembler.assemble(
            entity=entity,
            events=prompt_events,
            related_entities=related_entities,
            doc_snippets=doc_snippets_for_prompt,
            detail_level=detail_level,
        )
        user_prompt = self._build_user_prompt(
            detail_level=detail_level,
            context=context,
            doc_snippets=doc_snippets_for_prompt,
            baseline_only=history_source == "baseline_only",
        )
        return _PreparedExplanation(
            entity=entity,
            all_events=all_events,
            prompt_events=prompt_events,
            history_source=history_source,
            relation_context=relation_context,
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
        payload = explanation.model_copy(update={"from_cache": False}).model_dump_json(
            by_alias=True
        )
        await self._cache.put(cache_key, payload, ttl=self._cache_ttl)

    async def _load_all_events(self, entity: CodeEntity) -> list[TailEvent]:
        event_ids = [reference.event_id for reference in entity.event_refs]
        if not event_ids:
            return []

        events = await self._event_store.get_batch(event_ids)
        return sorted(events, key=lambda item: item.timestamp)

    def _select_prompt_events(
        self,
        events: list[TailEvent],
        *,
        detail_level: str,
        history_source: HistorySource,
    ) -> list[TailEvent]:
        filtered = events
        if history_source == "mixed":
            filtered = [
                event for event in events if event.action_type != ActionType.BASELINE
            ]
        if detail_level != "trace":
            return filtered
        if self._max_events <= 0 or len(filtered) <= self._max_events:
            return filtered
        if self._max_events == 1:
            return [filtered[0]]
        return [filtered[0]] + filtered[-(self._max_events - 1) :]

    def _classify_history_source(self, events: list[TailEvent]) -> HistorySource:
        has_baseline = any(event.action_type == ActionType.BASELINE for event in events)
        has_traced = any(event.action_type != ActionType.BASELINE for event in events)
        if has_baseline and not has_traced:
            return "baseline_only"
        if has_baseline and has_traced:
            return "mixed"
        return "traced_only"

    async def _load_doc_snippets(self, events: list[TailEvent]) -> list[ExternalDocMatch]:
        snippets: list[ExternalDocMatch] = []
        seen: set[tuple[str, str]] = set()

        for event in events:
            for external_ref in event.external_refs:
                dedupe_key = (external_ref.package, external_ref.symbol)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                matches = await self._doc_retriever.retrieve(
                    external_ref.package,
                    external_ref.symbol,
                )
                if matches is None:
                    continue
                if isinstance(matches, str):
                    matches = [
                        ExternalDocMatch(
                            source=ExternalDocSource(
                                kind="pydoc",
                                package=external_ref.package,
                                symbol=external_ref.symbol,
                                doc_uri=external_ref.doc_uri,
                            ),
                            chunk=ExternalDocChunk(
                                chunk_id=f"legacy:{external_ref.package}:{external_ref.symbol}",
                                content=matches,
                            ),
                            usage_pattern=external_ref.usage_pattern.value,
                            version=external_ref.version,
                        )
                    ]
                for match in matches:
                    snippets.append(
                        match.model_copy(
                            update={
                                "usage_pattern": external_ref.usage_pattern.value,
                                "version": external_ref.version,
                                "source": match.source.model_copy(
                                    update={"doc_uri": external_ref.doc_uri}
                                ),
                            }
                        )
                    )
                    if len(snippets) >= 2:
                        return snippets

        return snippets

    async def _load_relation_context(self, entity_id: str) -> RelationContext:
        outgoing_relations = await self._relation_store.get_outgoing(entity_id)
        incoming_relations = await self._relation_store.get_incoming(entity_id)

        callers = await self._load_relation_items(
            relations=incoming_relations,
            role="caller",
            other_id_getter=lambda relation: relation.source,
            allowed_type=RelationType.CALLS,
            limit=5,
        )
        callees = await self._load_relation_items(
            relations=outgoing_relations,
            role="callee",
            other_id_getter=lambda relation: relation.target,
            allowed_type=RelationType.CALLS,
            limit=5,
        )
        containers = await self._load_relation_items(
            relations=incoming_relations,
            role="container",
            other_id_getter=lambda relation: relation.source,
            allowed_type=RelationType.COMPOSED_OF,
            limit=None,
        )
        members = await self._load_relation_items(
            relations=outgoing_relations,
            role="member",
            other_id_getter=lambda relation: relation.target,
            allowed_type=RelationType.COMPOSED_OF,
            limit=None,
        )
        global_paths = await self._graph_service.get_impact_paths(
            entity_id,
            direction="both",
            limit=3,
        )
        subgraph = await self._graph_service.get_subgraph(entity_id, depth=2)
        global_subgraph = None
        if hasattr(subgraph, "depth") and hasattr(subgraph, "nodes") and hasattr(subgraph, "edges"):
            global_subgraph = GraphSubgraphSummary(
                depth=subgraph.depth,
                node_count=len(subgraph.nodes),
                edge_count=len(subgraph.edges),
                truncated=bool(getattr(subgraph, "truncated", False)),
                relation_types=sorted(
                    {
                        edge.relation_type
                        for edge in subgraph.edges
                        if hasattr(edge, "relation_type")
                    }
                ),
            )

        return RelationContext(
            local=LocalRelationContext(
                callers=callers,
                callees=callees,
                containers=containers,
                members=members,
            ),
            global_={
                "paths": global_paths or None,
                "subgraph": global_subgraph,
            },
        )

    async def _load_relation_items(
        self,
        *,
        relations: list,
        role: str,
        other_id_getter,
        allowed_type: RelationType,
        limit: Optional[int],
    ) -> list[RelationContextItem]:
        rows: list[tuple[object, CodeEntity]] = []
        for relation in relations:
            if relation.relation_type != allowed_type:
                continue
            other_id = other_id_getter(relation)
            other_entity = await self._entity_db.get(other_id)
            if other_entity is None or other_entity.is_deleted:
                continue
            rows.append((relation, other_entity))

        rows.sort(
            key=lambda item: (
                -item[0].created_at.timestamp(),
                item[1].qualified_name,
            )
        )

        items: list[RelationContextItem] = []
        seen: set[str] = set()
        for _, entity in rows:
            if entity.entity_id in seen:
                continue
            seen.add(entity.entity_id)
            items.append(self._relation_item(entity=entity, role=role))
            if limit is not None and len(items) >= limit:
                break
        return items

    def _relation_item(
        self,
        *,
        entity: CodeEntity,
        role: str,
    ) -> RelationContextItem:
        return RelationContextItem(
            entity_id=entity.entity_id,
            qualified_name=entity.qualified_name,
            kind=self._relation_kind(entity.entity_type),
            relation=role,
        )

    def _relation_kind(self, entity_type: EntityType) -> str:
        if entity_type == EntityType.CLASS:
            return "class"
        if entity_type == EntityType.METHOD:
            return "method"
        if entity_type == EntityType.MODULE:
            return "module"
        return "function"

    def _empty_relation_context(self) -> RelationContext:
        return RelationContext()

    def _derive_related_entities(self, relation_context: RelationContext) -> list[dict]:
        derived: list[dict] = []
        seen: set[tuple[str, str, str]] = set()

        groups = (
            ("incoming", "calls", relation_context.local.callers),
            ("outgoing", "calls", relation_context.local.callees),
            ("incoming", "composed_of", relation_context.local.containers),
            ("outgoing", "composed_of", relation_context.local.members),
        )
        for direction, relation_type, items in groups:
            for item in items:
                dedupe_key = (direction, relation_type, item.entity_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                derived.append(
                    {
                        "entity_id": item.entity_id,
                        "entity_name": item.qualified_name.rsplit(".", 1)[-1],
                        "qualified_name": item.qualified_name,
                        "entity_type": item.kind,
                        "direction": direction,
                        "relation_type": relation_type,
                        "confidence": 1.0,
                        "context": None,
                    }
                )
        return derived

    def _build_user_prompt(
        self,
        detail_level: str,
        context: str,
        doc_snippets: list[ExternalDocMatch],
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
                "Additional constraints: the current context only contains baseline history "
                "and has no explicit reasoning. Do not guess the original creation intent, "
                "design rationale, or discarded alternatives. Only describe the current code "
                "structure, behavior, and directly observed context."
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

    def _creation_intent(
        self,
        events: list[TailEvent],
        history_source: HistorySource,
    ) -> Optional[str]:
        if not events:
            return None
        if history_source == "baseline_only":
            return None
        if history_source == "mixed":
            for event in events:
                if event.action_type != ActionType.BASELINE and event.intent.strip():
                    return event.intent
            return None
        return events[0].intent

    def _format_external_context(self, doc_snippets: list[ExternalDocMatch]) -> str:
        lines: list[str] = []
        for snippet in doc_snippets:
            lines.append(f"{snippet.source.package}.{snippet.source.symbol}")
            lines.append(snippet.chunk.content)
        return "\n".join(lines)

    async def _build_cache_key(
        self,
        entity_id: str,
        detail_level: str,
        include_relations: bool,
        resolved_profile: ResolvedCodingProfile,
    ) -> str:
        graph_version = 0
        docs_version = 0
        if self._version_store is not None:
            graph_version = await self._version_store.get("graph_version")
            docs_version = await self._version_store.get("docs_version")
        return (
            f"explain:{entity_id}:{detail_level}:{int(include_relations)}:"
            f"{EXPLANATION_PROMPT_VERSION}:{resolved_profile.resolved_profile_id}:"
            f"{self._model_profile(detail_level, resolved_profile)}:"
            f"{graph_version}:{docs_version}"
        )

    def _model_profile(
        self,
        detail_level: str,
        resolved_profile: ResolvedCodingProfile,
    ) -> str:
        model_name = resolved_profile.model or self._llm_model_name or "default"
        return (
            f"{resolved_profile.backend or self._llm_backend_name}:{model_name}:"
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
        history_source: HistorySource,
        resolved_profile: ResolvedCodingProfile,
    ) -> ExplanationStreamInit:
        return ExplanationStreamInit(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type,
            signature=entity.signature,
            resolved_profile_id=resolved_profile.resolved_profile_id,
            file_path=entity.file_path,
            line_range=entity.line_range,
            event_count=len(entity.event_refs),
            summary=summary,
            history_source=history_source,
        )

    def _build_final_explanation(
        self,
        *,
        entity: CodeEntity,
        all_events: list[TailEvent],
        history_source: HistorySource,
        relation_context: RelationContext,
        related_entities: list[dict],
        doc_snippets: list[ExternalDocMatch],
        raw_output: str,
        detail_level: str,
        resolved_profile: ResolvedCodingProfile,
    ) -> EntityExplanation:
        explanation = self._formatter.format(
            entity,
            raw_output,
            detail_level=detail_level,
        )
        explanation.resolved_profile_id = resolved_profile.resolved_profile_id
        explanation.creation_intent = explanation.creation_intent or self._creation_intent(
            all_events,
            history_source,
        )
        explanation.modification_history = self._build_modification_history(all_events)
        explanation.history_source = history_source
        explanation.relation_context = relation_context
        explanation.related_entities = related_entities
        explanation.external_doc_snippets = doc_snippets
        explanation.from_cache = False
        return explanation

    def _resolve_profile(self, profile_id: Optional[str]) -> ResolvedCodingProfile:
        if self._profile_registry is not None:
            return self._profile_registry.resolve_profile(profile_id)
        if profile_id:
            raise ValueError("Explanation profile selection is not configured on the backend")
        if self._default_llm_client is None:
            raise ValueError("Explanation backend is not configured")
        return ResolvedCodingProfile(
            resolved_profile_id="default",
            backend=self._llm_backend_name,
            model=self._llm_model_name,
            source="env_fallback",
            llm_client=self._default_llm_client,
        )


__all__ = ["ExplanationEngine"]
