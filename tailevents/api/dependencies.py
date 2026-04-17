"""FastAPI dependency wiring and application container."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from fastapi import Depends, Request

from tailevents.baseline import BaselineOnboardingService
from tailevents.cache import ExplanationCache
from tailevents.coding import CodingTaskService
from tailevents.config import Settings, get_settings
from tailevents.explanation import (
    DocRetriever,
    ExplanationEngine,
    ExplanationMetricsTracker,
    LLMClientFactory,
)
from tailevents.graph import GraphServiceStub
from tailevents.ingestion import GraphUpdateHook, IngestionPipeline
from tailevents.indexer import Indexer
from tailevents.models.entity import CodeEntity
from tailevents.models.event import RawEvent, TailEvent
from tailevents.models.protocols import (
    CodingProfileRegistryProtocol,
    DocRetrieverProtocol,
    LLMClientProtocol,
)
from tailevents.profiles import InMemoryCodingProfileRegistry
from tailevents.query import QueryRouter
from tailevents.storage import (
    SQLiteCodingTaskStore,
    SQLiteConnectionManager,
    SQLiteEntityDB,
    SQLiteEventStore,
    SQLiteRelationStore,
    SQLiteTaskStepStore,
    initialize_db,
)


@dataclass
class AppContainer:
    """Bundle long-lived application services for dependency injection."""

    settings: Settings
    db_manager: SQLiteConnectionManager
    event_store: SQLiteEventStore
    entity_db: SQLiteEntityDB
    relation_store: SQLiteRelationStore
    task_store: SQLiteCodingTaskStore
    task_step_store: SQLiteTaskStepStore
    cache: ExplanationCache
    indexer: Indexer
    baseline_service: BaselineOnboardingService
    llm_client: LLMClientProtocol
    doc_retriever: DocRetrieverProtocol
    explanation_engine: ExplanationEngine
    query_router: QueryRouter
    graph_service: GraphServiceStub
    ingestion_pipeline: IngestionPipeline
    profile_registry: CodingProfileRegistryProtocol
    coding_task_service: CodingTaskService

    async def ingest_raw_event(self, raw_event: RawEvent) -> TailEvent:
        """Compatibility wrapper around the formal ingestion pipeline."""

        return await self.ingestion_pipeline.ingest(raw_event)

    async def ingest_batch(self, raw_events: list[RawEvent]) -> list[TailEvent]:
        """Compatibility wrapper around the formal ingestion pipeline."""

        return await self.ingestion_pipeline.ingest_batch(raw_events)

    async def get_events_for_entity(self, entity: CodeEntity) -> list[TailEvent]:
        """Load events referenced by a single entity in stored order."""

        event_ids = [reference.event_id for reference in entity.event_refs]
        if not event_ids:
            return []
        return await self.event_store.get_batch(event_ids)

    async def get_admin_stats(self) -> dict[str, object]:
        """Aggregate counts for admin observability."""

        cache_stats = await self.cache.stats()
        return {
            "entity_count": len(
                [entity for entity in await self.entity_db.get_all() if not entity.is_deleted]
            ),
            "event_count": await self.event_store.count(),
            "relation_count": len(await self.relation_store.get_all_active()),
            "explanation_metrics": self.explanation_engine.get_metrics(),
            **cache_stats,
        }

    async def clear_cache(self) -> dict[str, float | int]:
        """Invalidate all cached explanations and reset runtime metrics."""

        await self.cache.clear_all()
        self.explanation_engine.reset_metrics()
        return await self.cache.stats()

    async def reset_state(self) -> dict[str, int]:
        """Clear all persisted runtime state for local manual testing."""

        entity_count = len(await self.entity_db.get_all())
        event_count = await self.event_store.count()
        relation_count = len(await self.relation_store.get_all_active())
        task_count = await self._count_rows("coding_tasks")
        task_step_count = await self._count_rows("task_step_events")
        cancelled_tasks = await self.coding_task_service.reset_all_sessions()

        async with self.db_manager.connection() as connection:
            await connection.executescript(
                """
                DELETE FROM coding_tasks;
                DELETE FROM task_step_events;
                DELETE FROM relations;
                DELETE FROM entity_search;
                DELETE FROM entities;
                DELETE FROM explanation_cache;
                DELETE FROM events;
                """
            )
            await connection.commit()

        self.indexer.pending_queue.clear()
        self.cache.reset_metrics()
        self.explanation_engine.reset_metrics()

        return {
            "events_deleted": event_count,
            "entities_deleted": entity_count,
            "relations_deleted": relation_count,
            "tasks_deleted": task_count,
            "task_steps_deleted": task_step_count,
            "cancelled_tasks": cancelled_tasks,
        }

    async def reindex_all(self) -> dict[str, int]:
        """Replay all events after clearing index-side tables."""

        event_count = await self.event_store.count()
        ordered_events = list(
            reversed(await self.event_store.get_recent(limit=event_count))
        )
        await self._clear_index_state()

        pending_events = 0
        entities_created = 0
        entities_modified = 0
        entities_deleted = 0
        relations_created = 0

        for event in ordered_events:
            replay_event = event.model_copy(update={"entity_refs": []})
            _, result = await self.ingestion_pipeline.reindex_stored_event(replay_event)
            pending_events += int(result.pending)
            entities_created += len(result.entities_created)
            entities_modified += len(result.entities_modified)
            entities_deleted += len(result.entities_deleted)
            relations_created += len(result.relations_created)

        return {
            "events_replayed": len(ordered_events),
            "pending_events": pending_events,
            "entities_created": entities_created,
            "entities_modified": entities_modified,
            "entities_deleted": entities_deleted,
            "relations_created": relations_created,
        }

    async def health(self) -> dict[str, str]:
        """Check the database connectivity."""

        async with self.db_manager.connection() as connection:
            cursor = await connection.execute("SELECT 1 AS ok")
            row = await cursor.fetchone()
            await cursor.close()

        return {
            "status": "ok" if row is not None and int(row["ok"]) == 1 else "degraded",
            "database": "ok",
        }

    async def _clear_index_state(self) -> None:
        async with self.db_manager.connection() as connection:
            await connection.executescript(
                """
                DELETE FROM relations;
                DELETE FROM entity_search;
                DELETE FROM entities;
                DELETE FROM explanation_cache;
                UPDATE events SET entity_refs = NULL;
                """
            )
            await connection.commit()
        self.cache.reset_metrics()
        self.explanation_engine.reset_metrics()

    async def _count_rows(self, table_name: str) -> int:
        async with self.db_manager.connection() as connection:
            cursor = await connection.execute(
                f"SELECT COUNT(*) AS count FROM {table_name}"
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return 0
        return int(row["count"])


def build_lifespan(
    settings: Optional[Settings] = None,
    llm_client: Optional[LLMClientProtocol] = None,
    doc_retriever: Optional[DocRetrieverProtocol] = None,
):
    """Build the application lifespan that initializes shared services."""

    @asynccontextmanager
    async def lifespan(app) -> AsyncIterator[None]:
        app_settings = settings or get_settings()
        db_manager = SQLiteConnectionManager.from_settings(app_settings)
        await initialize_db(db_manager)

        event_store = SQLiteEventStore(db_manager)
        entity_db = SQLiteEntityDB(db_manager)
        relation_store = SQLiteRelationStore(db_manager)
        task_store = SQLiteCodingTaskStore(db_manager)
        task_step_store = SQLiteTaskStepStore(db_manager)
        cache = ExplanationCache(db_manager)
        explanation_telemetry = ExplanationMetricsTracker()
        indexer = Indexer(
            entity_db=entity_db,
            relation_store=relation_store,
            cache=cache,
            rename_similarity_threshold=app_settings.rename_similarity_threshold,
        )
        active_llm_client = llm_client or LLMClientFactory.create(app_settings)
        active_doc_retriever = doc_retriever or DocRetriever()
        explanation_engine = ExplanationEngine(
            entity_db=entity_db,
            event_store=event_store,
            relation_store=relation_store,
            cache=cache,
            llm_client=active_llm_client,
            doc_retriever=active_doc_retriever,
            max_events=app_settings.explanation_max_events,
            temperature=app_settings.explanation_temperature,
            cache_ttl=app_settings.cache_default_ttl,
            cache_enabled=app_settings.cache_enabled,
            llm_backend_name=app_settings.llm_backend,
            llm_model_name=_resolve_llm_model_name(app_settings),
            detailed_concurrency=app_settings.explanation_detailed_concurrency,
            stream_flush_chars=app_settings.explanation_stream_flush_chars,
            stream_flush_ms=app_settings.explanation_stream_flush_ms,
            stream_stall_timeout_ms=app_settings.explanation_stream_stall_timeout_ms,
            telemetry=explanation_telemetry,
        )
        query_router = QueryRouter(
            entity_db=entity_db,
            explanation_engine=explanation_engine,
        )
        graph_service = GraphServiceStub()
        ingestion_pipeline = IngestionPipeline(
            event_store=event_store,
            indexer=indexer,
            hooks=[GraphUpdateHook(graph_service)],
        )
        profile_registry = InMemoryCodingProfileRegistry(app_settings)
        baseline_service = BaselineOnboardingService(
            event_store=event_store,
            ingestion_pipeline=ingestion_pipeline,
        )
        coding_task_service = CodingTaskService(
            llm_client=active_llm_client,
            task_store=task_store,
            step_store=task_step_store,
            ingestion_pipeline=ingestion_pipeline,
            profile_registry=profile_registry,
        )

        container = AppContainer(
            settings=app_settings,
            db_manager=db_manager,
            event_store=event_store,
            entity_db=entity_db,
            relation_store=relation_store,
            task_store=task_store,
            task_step_store=task_step_store,
            cache=cache,
            indexer=indexer,
            baseline_service=baseline_service,
            llm_client=active_llm_client,
            doc_retriever=active_doc_retriever,
            explanation_engine=explanation_engine,
            query_router=query_router,
            graph_service=graph_service,
            ingestion_pipeline=ingestion_pipeline,
            profile_registry=profile_registry,
            coding_task_service=coding_task_service,
        )
        app.state.db_manager = db_manager
        app.state.container = container

        try:
            yield
        finally:
            await container.coding_task_service.reset_all_sessions()
            await db_manager.close()

    return lifespan


def get_container(request: Request) -> AppContainer:
    """Return the application container from FastAPI state."""

    return request.app.state.container


def get_settings_dependency(
    container: AppContainer = Depends(get_container),
) -> Settings:
    return container.settings


def get_event_store(
    container: AppContainer = Depends(get_container),
) -> SQLiteEventStore:
    return container.event_store


def get_entity_db(
    container: AppContainer = Depends(get_container),
) -> SQLiteEntityDB:
    return container.entity_db


def get_relation_store(
    container: AppContainer = Depends(get_container),
) -> SQLiteRelationStore:
    return container.relation_store


def get_task_step_store(
    container: AppContainer = Depends(get_container),
) -> SQLiteTaskStepStore:
    return container.task_step_store


def get_coding_task_store(
    container: AppContainer = Depends(get_container),
) -> SQLiteCodingTaskStore:
    return container.task_store


def get_indexer(
    container: AppContainer = Depends(get_container),
) -> Indexer:
    return container.indexer


def get_cache(
    container: AppContainer = Depends(get_container),
) -> ExplanationCache:
    return container.cache


def get_baseline_onboarding_service(
    container: AppContainer = Depends(get_container),
) -> BaselineOnboardingService:
    return container.baseline_service


def get_explanation_engine(
    container: AppContainer = Depends(get_container),
) -> ExplanationEngine:
    return container.explanation_engine


def get_query_router(
    container: AppContainer = Depends(get_container),
) -> QueryRouter:
    return container.query_router


def get_graph_service(
    container: AppContainer = Depends(get_container),
) -> GraphServiceStub:
    return container.graph_service


def get_ingestion_pipeline(
    container: AppContainer = Depends(get_container),
) -> IngestionPipeline:
    return container.ingestion_pipeline


def get_coding_task_service(
    container: AppContainer = Depends(get_container),
) -> CodingTaskService:
    return container.coding_task_service


def get_profile_registry(
    container: AppContainer = Depends(get_container),
) -> CodingProfileRegistryProtocol:
    return container.profile_registry


def _resolve_llm_model_name(settings: Settings) -> str:
    backend = settings.llm_backend.lower()
    if backend == "ollama":
        return settings.ollama_model
    if backend == "claude":
        return settings.claude_model
    if backend == "openrouter":
        return settings.openrouter_model
    return ""


__all__ = [
    "AppContainer",
    "get_baseline_onboarding_service",
    "build_lifespan",
    "get_cache",
    "get_coding_task_store",
    "get_coding_task_service",
    "get_container",
    "get_entity_db",
    "get_event_store",
    "get_explanation_engine",
    "get_graph_service",
    "get_ingestion_pipeline",
    "get_indexer",
    "get_profile_registry",
    "get_query_router",
    "get_relation_store",
    "get_settings_dependency",
    "get_task_step_store",
]
