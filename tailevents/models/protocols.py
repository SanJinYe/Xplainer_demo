"""Protocol interfaces shared across modules."""

from typing import AsyncIterator, Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from tailevents.models.entity import CodeEntity
    from tailevents.models.event import EntityRef, TailEvent
    from tailevents.models.explanation import EntityExplanation
    from tailevents.models.relation import Relation
    from tailevents.models.task import (
        CodingTaskCreateRequest,
        CodingTaskCreateResponse,
        CodingTaskDraftResult,
        CodingTaskToolResultRequest,
        TaskStepEvent,
    )


@runtime_checkable
class EventStoreProtocol(Protocol):
    async def put(self, event: "TailEvent") -> str: ...

    async def enrich(self, event_id: str, entity_refs: list["EntityRef"]) -> None: ...

    async def get(self, event_id: str) -> Optional["TailEvent"]: ...

    async def get_batch(self, event_ids: list[str]) -> list["TailEvent"]: ...

    async def get_by_session(self, session_id: str) -> list["TailEvent"]: ...

    async def get_by_file(self, file_path: str) -> list["TailEvent"]: ...

    async def get_recent(self, limit: int = 50) -> list["TailEvent"]: ...

    async def count(self) -> int: ...


@runtime_checkable
class EntityDBProtocol(Protocol):
    async def upsert(self, entity: "CodeEntity") -> str: ...

    async def get(self, entity_id: str) -> Optional["CodeEntity"]: ...

    async def get_by_qualified_name(self, qname: str) -> Optional["CodeEntity"]: ...

    async def get_by_name(self, name: str) -> list["CodeEntity"]: ...

    async def get_by_file(self, file_path: str) -> list["CodeEntity"]: ...

    async def search(self, query: str) -> list["CodeEntity"]: ...

    async def get_all(self) -> list["CodeEntity"]: ...

    async def mark_deleted(self, entity_id: str, event_id: str) -> None: ...

    async def update_description(self, entity_id: str, desc: str) -> None: ...

    async def invalidate_description(self, entity_id: str) -> None: ...

    async def count(self) -> int: ...


@runtime_checkable
class RelationStoreProtocol(Protocol):
    async def put(self, relation: "Relation") -> str: ...

    async def get_outgoing(self, entity_id: str) -> list["Relation"]: ...

    async def get_incoming(self, entity_id: str) -> list["Relation"]: ...

    async def get_between(self, source: str, target: str) -> list["Relation"]: ...

    async def get_by_event(self, event_id: str) -> list["Relation"]: ...

    async def deactivate_by_source(self, entity_id: str) -> None: ...

    async def get_all_active(self) -> list["Relation"]: ...

    async def count(self) -> int: ...


@runtime_checkable
class IndexerResult(Protocol):
    entities_created: list[str]
    entities_modified: list[str]
    entities_deleted: list[str]
    relations_created: list[str]
    pending: bool


@runtime_checkable
class IndexerProtocol(Protocol):
    async def process_event(self, event: "TailEvent") -> IndexerResult: ...


@runtime_checkable
class ExplanationEngineProtocol(Protocol):
    async def explain_entity(
        self,
        entity_id: str,
        detail_level: str = "summary",
        include_relations: bool = False,
    ) -> "EntityExplanation": ...

    async def explain_entities(
        self,
        entity_ids: list[str],
        detail_level: str = "summary",
        include_relations: bool = False,
    ) -> list["EntityExplanation"]: ...


@runtime_checkable
class CacheProtocol(Protocol):
    async def get(self, key: str) -> Optional[str]: ...

    async def put(self, key: str, value: str, ttl: Optional[int] = None) -> None: ...

    async def invalidate(self, key: str) -> None: ...

    async def invalidate_prefix(self, prefix: str) -> None: ...


@runtime_checkable
class GraphServiceProtocol(Protocol):
    async def get_subgraph(self, entity_id: str, depth: int = 2) -> dict: ...

    async def get_isolated_entities(self) -> list[str]: ...

    async def get_single_dependency_entities(self) -> list[str]: ...

    async def detect_cycles(self) -> list[list[str]]: ...

    async def get_communities(self) -> list[list[str]]: ...

    async def get_entity_importance(self, entity_id: str) -> dict: ...


@runtime_checkable
class LLMClientProtocol(Protocol):
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str: ...

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]: ...


@runtime_checkable
class DocRetrieverProtocol(Protocol):
    async def retrieve(self, package: str, symbol: str) -> Optional[str]: ...


@runtime_checkable
class TaskStepStoreProtocol(Protocol):
    async def put(self, event: "TaskStepEvent") -> None: ...

    async def get_by_task(self, task_id: str) -> list["TaskStepEvent"]: ...


@runtime_checkable
class CodingTaskServiceProtocol(Protocol):
    async def create_task(
        self,
        request: "CodingTaskCreateRequest",
    ) -> "CodingTaskCreateResponse": ...

    async def stream_events(self, task_id: str): ...

    async def submit_tool_result(
        self,
        task_id: str,
        result: "CodingTaskToolResultRequest",
    ) -> None: ...

    async def cancel_task(self, task_id: str) -> None: ...

    async def get_result(self, task_id: str) -> Optional["CodingTaskDraftResult"]: ...


__all__ = [
    "CacheProtocol",
    "CodingTaskServiceProtocol",
    "DocRetrieverProtocol",
    "EntityDBProtocol",
    "EventStoreProtocol",
    "ExplanationEngineProtocol",
    "GraphServiceProtocol",
    "IndexerProtocol",
    "IndexerResult",
    "LLMClientProtocol",
    "RelationStoreProtocol",
    "TaskStepStoreProtocol",
]
