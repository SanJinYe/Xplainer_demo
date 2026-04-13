from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from tailevents.api import create_app
from tailevents.config import Settings
from tailevents.models.entity import CodeEntity, RenameRecord
from tailevents.models.enums import EntityType
from tailevents.models.explanation import EntityExplanation, ExplanationRequest
from tailevents.query import LocationResolver, QueryRouter, SymbolResolver
from tailevents.storage import SQLiteConnectionManager, SQLiteEntityDB, initialize_db


STRUCTURED_OUTPUT = """作用
负责提供结构化说明。
参数
- value: 输入值
返回值
返回转换后的结果。
使用场景
用于测试 explanation API。
设计背景
为了验证 query 和 API 链路。
"""


class FakeLLMClient:
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        return STRUCTURED_OUTPUT


class FakeDocRetriever:
    async def retrieve(self, package: str, symbol: str):
        return None


class FakeExplanationEngine:
    def __init__(self):
        self.calls = []

    async def explain_entity(
        self,
        entity_id: str,
        detail_level: str = "summary",
        include_relations: bool = False,
    ) -> EntityExplanation:
        self.calls.append(
            {
                "entity_ids": [entity_id],
                "detail_level": detail_level,
                "include_relations": include_relations,
            }
        )
        return self._build_explanation(entity_id)

    async def explain_entities(
        self,
        entity_ids: list[str],
        detail_level: str = "summary",
        include_relations: bool = False,
    ) -> list[EntityExplanation]:
        self.calls.append(
            {
                "entity_ids": entity_ids,
                "detail_level": detail_level,
                "include_relations": include_relations,
            }
        )
        return [self._build_explanation(entity_id) for entity_id in entity_ids]

    def _build_explanation(self, entity_id: str) -> EntityExplanation:
        return EntityExplanation(
            entity_id=entity_id,
            entity_name=entity_id,
            qualified_name=entity_id,
            entity_type=EntityType.FUNCTION,
            summary=f"summary for {entity_id}",
        )


@pytest_asyncio.fixture
async def query_bundle():
    database = SQLiteConnectionManager(":memory:")
    await initialize_db(database)
    entity_db = SQLiteEntityDB(database)

    class_entity = CodeEntity(
        entity_id="ent_service",
        name="Service",
        qualified_name="Service",
        entity_type=EntityType.CLASS,
        file_path="service.py",
        line_range=(4, 8),
    )
    method_entity = CodeEntity(
        entity_id="ent_run",
        name="run",
        qualified_name="Service.run",
        entity_type=EntityType.METHOD,
        file_path="service.py",
        line_range=(5, 6),
    )
    helper_entity = CodeEntity(
        entity_id="ent_helper_primary",
        name="helper",
        qualified_name="pkg.helper",
        entity_type=EntityType.FUNCTION,
        file_path="helpers.py",
        line_range=(1, 2),
        cached_description="retry helper for network access",
        description_valid=True,
    )
    helper_entity_secondary = CodeEntity(
        entity_id="ent_helper_secondary",
        name="helper",
        qualified_name="utils.helper",
        entity_type=EntityType.FUNCTION,
        file_path="utils.py",
        line_range=(10, 12),
    )
    renamed_entity = CodeEntity(
        entity_id="ent_renamed",
        name="new_name",
        qualified_name="new_name",
        entity_type=EntityType.FUNCTION,
        file_path="renamed.py",
        line_range=(1, 2),
        rename_history=[
            RenameRecord(
                old_qualified_name="legacy.helper",
                new_qualified_name="new_name",
                event_id="te_rename",
                timestamp=datetime.utcnow(),
            )
        ],
    )

    for entity in [
        class_entity,
        method_entity,
        helper_entity,
        helper_entity_secondary,
        renamed_entity,
    ]:
        await entity_db.upsert(entity)

    yield {
        "database": database,
        "entity_db": entity_db,
        "class_entity": class_entity,
        "method_entity": method_entity,
        "helper_entity": helper_entity,
        "helper_entity_secondary": helper_entity_secondary,
        "renamed_entity": renamed_entity,
    }

    await database.close()


@pytest.fixture
def api_client():
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "tailevents.db"
        settings = Settings(db_path=str(db_path))
        app = create_app(
            settings=settings,
            llm_client=FakeLLMClient(),
            doc_retriever=FakeDocRetriever(),
        )
        with TestClient(app) as client:
            yield client


def seed_api_data(client: TestClient) -> dict:
    primary_event = client.post(
        "/api/v1/events",
        json={
            "action_type": "create",
            "file_path": "service.py",
            "code_snapshot": (
                "def helper():\n"
                "    return 1\n\n"
                "class Service:\n"
                "    def run(self):\n"
                "        return helper()\n"
            ),
            "intent": "create helper and service orchestration",
            "reasoning": "need a small call graph for API tests",
            "session_id": "session-1",
        },
    )
    assert primary_event.status_code == 201

    batch_response = client.post(
        "/api/v1/events/batch",
        json=[
            {
                "action_type": "create",
                "file_path": "parser.py",
                "code_snapshot": "def parse_payload(data):\n    return data\n",
                "intent": "create parser",
                "session_id": "session-2",
            },
            {
                "action_type": "create",
                "file_path": "normalizer.py",
                "code_snapshot": "def normalize(value):\n    return value.strip()\n",
                "intent": "create normalizer",
                "session_id": "session-2",
            },
        ],
    )
    assert batch_response.status_code == 201

    entities_response = client.get("/api/v1/entities", params={"skip": 0, "limit": 50})
    assert entities_response.status_code == 200
    entities = entities_response.json()

    by_qname = {entity["qualified_name"]: entity for entity in entities}
    return {
        "primary_event": primary_event.json(),
        "batch_events": batch_response.json(),
        "entities": entities,
        "helper": by_qname["helper"],
        "service_run": by_qname["Service.run"],
        "service_class": by_qname["Service"],
        "parser": by_qname["parse_payload"],
        "normalizer": by_qname["normalize"],
    }


@pytest.mark.asyncio
async def test_location_and_symbol_resolvers(query_bundle):
    location_resolver = LocationResolver(query_bundle["entity_db"])
    symbol_resolver = SymbolResolver(query_bundle["entity_db"])

    assert (
        await location_resolver.resolve("service.py", 5)
        == query_bundle["method_entity"].entity_id
    )
    assert await location_resolver.resolve("service.py", 100) is None

    assert await symbol_resolver.resolve("pkg.helper") == [
        query_bundle["helper_entity"].entity_id
    ]
    assert await symbol_resolver.resolve("helper") == [
        query_bundle["helper_entity"].entity_id,
        query_bundle["helper_entity_secondary"].entity_id,
    ]
    assert await symbol_resolver.resolve("legacy.helper") == [
        query_bundle["renamed_entity"].entity_id
    ]
    assert await symbol_resolver.resolve("network") == [
        query_bundle["helper_entity"].entity_id
    ]


@pytest.mark.asyncio
async def test_query_router_priority_and_empty_results(query_bundle):
    engine = FakeExplanationEngine()
    router = QueryRouter(query_bundle["entity_db"], engine)

    response = await router.route(
        ExplanationRequest(
            query="network",
            file_path="service.py",
            line_number=5,
            cursor_word="helper",
            detail_level="trace",
            include_relations=True,
        )
    )

    assert [item.entity_id for item in response.explanations] == [
        query_bundle["method_entity"].entity_id
    ]
    assert engine.calls[0]["detail_level"] == "trace"
    assert engine.calls[0]["include_relations"] is True

    empty_response = await router.route(
        ExplanationRequest(
            query="network",
            file_path="service.py",
            line_number=100,
            cursor_word="helper",
        )
    )
    assert empty_response.explanations == []


def test_events_endpoints(api_client):
    seeded = seed_api_data(api_client)

    assert len(seeded["primary_event"]["entity_refs"]) >= 3
    assert len(seeded["batch_events"]) == 2

    event_id = seeded["primary_event"]["event_id"]
    single_event = api_client.get(f"/api/v1/events/{event_id}")
    assert single_event.status_code == 200
    assert single_event.json()["event_id"] == event_id

    session_events = api_client.get("/api/v1/events", params={"session": "session-2"})
    assert session_events.status_code == 200
    assert len(session_events.json()) == 2

    recent_events = api_client.get("/api/v1/events")
    assert recent_events.status_code == 200
    assert len(recent_events.json()) == 3

    helper_events = api_client.get(
        f"/api/v1/events/for-entity/{seeded['helper']['entity_id']}"
    )
    assert helper_events.status_code == 200
    assert helper_events.json()[0]["event_id"] == event_id


def test_entities_and_relations_endpoints(api_client):
    seeded = seed_api_data(api_client)

    listed = api_client.get("/api/v1/entities", params={"skip": 0, "limit": 10})
    assert listed.status_code == 200
    assert len(listed.json()) >= 5

    helper_detail = api_client.get(
        f"/api/v1/entities/{seeded['helper']['entity_id']}"
    )
    assert helper_detail.status_code == 200
    assert helper_detail.json()["qualified_name"] == "helper"

    search = api_client.get("/api/v1/entities/search", params={"q": "helper"})
    assert search.status_code == 200
    assert any(item["qualified_name"] == "helper" for item in search.json())

    by_location = api_client.get(
        "/api/v1/entities/by-location",
        params={"file": "service.py", "line": 5},
    )
    assert by_location.status_code == 200
    assert by_location.json()["qualified_name"] == "Service.run"

    outgoing = api_client.get(
        f"/api/v1/relations/{seeded['service_run']['entity_id']}/outgoing"
    )
    assert outgoing.status_code == 200
    assert outgoing.json()[0]["target"] == seeded["helper"]["entity_id"]

    incoming = api_client.get(
        f"/api/v1/relations/{seeded['helper']['entity_id']}/incoming"
    )
    assert incoming.status_code == 200
    assert incoming.json()[0]["source"] == seeded["service_run"]["entity_id"]

    subgraph = api_client.get(
        f"/api/v1/relations/{seeded['service_run']['entity_id']}/subgraph",
        params={"depth": 2},
    )
    assert subgraph.status_code == 200
    assert subgraph.json()["implemented"] is False


def test_explanations_and_admin_endpoints(api_client):
    seeded = seed_api_data(api_client)
    helper_id = seeded["helper"]["entity_id"]

    routed = api_client.post(
        "/api/v1/explain",
        json={
            "query": "helper",
            "detail_level": "summary",
            "include_relations": False,
        },
    )
    assert routed.status_code == 200
    assert len(routed.json()["explanations"]) >= 1

    first_detailed = api_client.get(f"/api/v1/explain/{helper_id}")
    assert first_detailed.status_code == 200
    assert first_detailed.json()["entity_id"] == helper_id

    second_detailed = api_client.get(f"/api/v1/explain/{helper_id}")
    assert second_detailed.status_code == 200
    assert second_detailed.json()["from_cache"] is True

    summary = api_client.get(f"/api/v1/explain/{helper_id}/summary")
    assert summary.status_code == 200
    assert "summary" in summary.json()

    stats = api_client.get("/api/v1/admin/stats")
    assert stats.status_code == 200
    stats_payload = stats.json()
    assert stats_payload["entity_count"] >= 5
    assert stats_payload["event_count"] == 3
    assert stats_payload["relation_count"] >= 1
    assert stats_payload["cache_hits"] >= 1
    assert stats_payload["cache_misses"] >= 1

    health = api_client.get("/api/v1/admin/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    cleared = api_client.post("/api/v1/admin/cache/clear")
    assert cleared.status_code == 200
    assert cleared.json()["cache_hits"] == 0
    assert cleared.json()["cache_misses"] == 0

    reindex = api_client.post("/api/v1/admin/reindex")
    assert reindex.status_code == 200
    assert reindex.json()["events_replayed"] == 3

    entities_after_reindex = api_client.get("/api/v1/entities")
    assert entities_after_reindex.status_code == 200
    assert len(entities_after_reindex.json()) >= 5


def test_api_error_responses(api_client):
    seed_api_data(api_client)

    missing_entity = api_client.get("/api/v1/entities/ent_missing")
    assert missing_entity.status_code == 404

    missing_event = api_client.get("/api/v1/events/te_missing")
    assert missing_event.status_code == 404

    missing_explanation = api_client.get("/api/v1/explain/ent_missing")
    assert missing_explanation.status_code == 404

    bad_location = api_client.get(
        "/api/v1/entities/by-location",
        params={"file": "service.py"},
    )
    assert bad_location.status_code == 422

    bad_event = api_client.post(
        "/api/v1/events",
        json={
            "action_type": "create",
            "intent": "missing file path",
            "code_snapshot": "pass\n",
        },
    )
    assert bad_event.status_code == 422
