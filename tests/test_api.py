from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time

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


STRUCTURED_OUTPUT = """核心作用
提供结构化说明，并输出稳定的默认 explanation。

关键上下文
这是 explanation API 的测试样例，用于验证返回形状和缓存。

关键事件
- 初始实现了基础 explanation 路径。
- 当前样例改成四段式输出。

关联实体
- caller: api tests
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

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ):
        yield STRUCTURED_OUTPUT


class SlowFakeLLMClient(FakeLLMClient):
    def __init__(self, delay_seconds: float):
        self._delay_seconds = delay_seconds

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        time.sleep(self._delay_seconds)
        return STRUCTURED_OUTPUT

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ):
        yield await self.generate(system_prompt, user_prompt, max_tokens, temperature)


class FakeDocRetriever:
    async def retrieve(self, package: str, symbol: str):
        return None


class FakeCodingLLMClient:
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
        ) -> str:
        return json.dumps(
            {
                "edits": [
                    {
                        "old_text": "    return 0\n",
                        "new_text": "    return 1\n",
                    }
                ],
                "intent": "Change the return value to 1",
                "reasoning": "A minimal local edit is enough for this task.",
            }
        )

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ):
        yield await self.generate(system_prompt, user_prompt, max_tokens, temperature)


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


def test_admin_reset_state_clears_runtime_data(api_client):
    seeded = seed_api_data(api_client)
    helper_id = seeded["helper"]["entity_id"]

    explained = api_client.get(f"/api/v1/explain/{helper_id}/summary")
    assert explained.status_code == 200

    created_task = api_client.post(
        "/api/v1/coding/tasks",
        json={
            "target_file_path": "pkg/demo.py",
            "target_file_version": 1,
            "user_prompt": "Change the return value to 1",
            "context_files": [],
        },
    )
    assert created_task.status_code == 201

    reset = api_client.post("/api/v1/admin/reset-state")
    assert reset.status_code == 200
    assert reset.json()["events_deleted"] == 3
    assert reset.json()["entities_deleted"] >= 5
    assert reset.json()["relations_deleted"] >= 1
    assert reset.json()["cancelled_tasks"] == 1

    stats = api_client.get("/api/v1/admin/stats")
    assert stats.status_code == 200
    stats_payload = stats.json()
    assert stats_payload["entity_count"] == 0
    assert stats_payload["event_count"] == 0
    assert stats_payload["relation_count"] == 0
    assert stats_payload["cache_hits"] == 0
    assert stats_payload["cache_misses"] == 0

    listed_events = api_client.get("/api/v1/events")
    assert listed_events.status_code == 200
    assert listed_events.json() == []


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


def test_baseline_onboard_file_endpoint(api_client):
    created = api_client.post(
        "/api/v1/baseline/onboard-file",
        json={
            "file_path": "pkg/onboarded.py",
            "code_snapshot": "def onboarded():\n    return 1\n",
        },
    )
    assert created.status_code == 200
    created_payload = created.json()
    assert created_payload["status"] == "created"
    assert created_payload["file_path"] == "pkg/onboarded.py"
    assert created_payload["event_id"]

    entities = api_client.get("/api/v1/entities/search", params={"q": "onboarded"})
    assert entities.status_code == 200
    assert any(item["qualified_name"] == "onboarded" for item in entities.json())

    duplicate = api_client.post(
        "/api/v1/baseline/onboard-file",
        json={
            "file_path": "pkg/onboarded.py",
            "code_snapshot": "def onboarded():\n    return 1\n",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "status": "skipped",
        "file_path": "pkg/onboarded.py",
        "event_id": None,
        "reason": "duplicate_baseline",
    }


def test_baseline_onboard_file_allows_new_baseline_content(api_client):
    first = api_client.post(
        "/api/v1/baseline/onboard-file",
        json={
            "file_path": "pkg/history.py",
            "code_snapshot": "def history():\n    return 1\n",
        },
    )
    assert first.status_code == 200
    assert first.json()["status"] == "created"

    second = api_client.post(
        "/api/v1/baseline/onboard-file",
        json={
            "file_path": "pkg/history.py",
            "code_snapshot": "def history():\n    return 2\n",
        },
    )
    assert second.status_code == 200
    assert second.json()["status"] == "created"

    recent_events = api_client.get("/api/v1/events")
    assert recent_events.status_code == 200
    payload = recent_events.json()
    assert len(payload) == 2
    assert {item["action_type"] for item in payload} == {"baseline"}


def test_baseline_onboard_file_skips_when_real_history_exists(api_client):
    seed_api_data(api_client)

    skipped = api_client.post(
        "/api/v1/baseline/onboard-file",
        json={
            "file_path": "service.py",
            "code_snapshot": "def helper():\n    return 1\n",
        },
    )
    assert skipped.status_code == 200
    assert skipped.json() == {
        "status": "skipped",
        "file_path": "service.py",
        "event_id": None,
        "reason": "existing_traced_history",
    }


def test_baseline_onboard_file_rejects_large_payload(api_client):
    too_large = api_client.post(
        "/api/v1/baseline/onboard-file",
        json={
            "file_path": "pkg/huge.py",
            "code_snapshot": "a" * ((512 * 1024) + 1),
        },
    )
    assert too_large.status_code == 422
    assert "512 KB" in too_large.json()["detail"]


def test_baseline_event_precedes_later_modify_history(api_client):
    onboarded = api_client.post(
        "/api/v1/baseline/onboard-file",
        json={
            "file_path": "pkg/timeline.py",
            "code_snapshot": "def timeline():\n    return 1\n",
        },
    )
    assert onboarded.status_code == 200
    event_id = onboarded.json()["event_id"]

    modified = api_client.post(
        "/api/v1/events",
        json={
            "action_type": "modify",
            "file_path": "pkg/timeline.py",
            "code_snapshot": "def timeline():\n    return 2\n",
            "intent": "update timeline helper",
        },
    )
    assert modified.status_code == 201

    entity = api_client.get("/api/v1/entities/search", params={"q": "timeline"})
    assert entity.status_code == 200
    timeline_entity = next(
        item for item in entity.json() if item["qualified_name"] == "timeline"
    )
    entity_events = api_client.get(
        f"/api/v1/events/for-entity/{timeline_entity['entity_id']}"
    )
    assert entity_events.status_code == 200
    event_ids = [item["event_id"] for item in entity_events.json()]
    assert event_ids == [event_id, modified.json()["event_id"]]


def test_baseline_onboard_docstring_only_init_creates_no_entities(api_client):
    onboarded = api_client.post(
        "/api/v1/baseline/onboard-file",
        json={
            "file_path": "pkg/__init__.py",
            "code_snapshot": '"""package docs"""\n',
        },
    )
    assert onboarded.status_code == 200
    assert onboarded.json()["status"] == "created"

    entities = api_client.get("/api/v1/entities/search", params={"q": "__init__"})
    assert entities.status_code == 200
    assert entities.json() == []


def test_baseline_onboard_empty_file_creates_no_entities(api_client):
    onboarded = api_client.post(
        "/api/v1/baseline/onboard-file",
        json={
            "file_path": "pkg/empty.py",
            "code_snapshot": "",
        },
    )
    assert onboarded.status_code == 200
    assert onboarded.json()["status"] == "created"

    listed = api_client.get("/api/v1/entities")
    assert listed.status_code == 200
    assert all(item["file_path"] != "pkg/empty.py" for item in listed.json())


def test_baseline_onboarding_does_not_significantly_block_other_requests():
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "tailevents.db"
        settings = Settings(db_path=str(db_path))
        app = create_app(
            settings=settings,
            llm_client=SlowFakeLLMClient(delay_seconds=0.01),
            doc_retriever=FakeDocRetriever(),
        )

        with TestClient(app) as client:
            seeded = seed_api_data(client)
            helper_id = seeded["helper"]["entity_id"]

            def measure_event_latency(suffix: str) -> float:
                started = time.perf_counter()
                response = client.post(
                    "/api/v1/events",
                    json={
                        "action_type": "create",
                        "file_path": f"bench_{suffix}.py",
                        "code_snapshot": f"def bench_{suffix}():\n    return 1\n",
                        "intent": f"create bench {suffix}",
                    },
                )
                assert response.status_code == 201
                return time.perf_counter() - started

            def measure_explain_latency() -> float:
                cleared = client.post("/api/v1/admin/cache/clear")
                assert cleared.status_code == 200
                started = time.perf_counter()
                response = client.get(f"/api/v1/explain/{helper_id}/summary")
                assert response.status_code == 200
                return time.perf_counter() - started

            baseline_event = measure_event_latency("baseline")
            baseline_explain = measure_explain_latency()

            started_event = threading.Event()

            def run_onboarding() -> None:
                started_event.set()
                for index in range(50):
                    response = client.post(
                        "/api/v1/baseline/onboard-file",
                        json={
                            "file_path": f"pkg/onboard_{index}.py",
                            "code_snapshot": (
                                f"def onboard_{index}():\n"
                                f"    return {index}\n"
                            ),
                        },
                    )
                    assert response.status_code == 200

            worker = threading.Thread(target=run_onboarding, daemon=True)
            worker.start()
            assert started_event.wait(timeout=1.0)
            time.sleep(0.01)

            concurrent_event = measure_event_latency("concurrent")
            concurrent_explain = measure_explain_latency()

            worker.join(timeout=5.0)
            assert not worker.is_alive()

            assert concurrent_event < max(baseline_event * 2, 0.2)
            assert concurrent_explain < max(baseline_explain * 2, 0.2)


def test_coding_task_api_smoke():
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "tailevents.db"
        settings = Settings(db_path=str(db_path))
        app = create_app(
            settings=settings,
            llm_client=FakeCodingLLMClient(),
            doc_retriever=FakeDocRetriever(),
        )

        with TestClient(app) as client:
            created = client.post(
                "/api/v1/coding/tasks",
                json={
                    "target_file_path": "pkg/demo.py",
                    "target_file_version": 1,
                    "user_prompt": "Change the return value to 1",
                    "context_files": [],
                },
            )
            assert created.status_code == 201
            task_id = created.json()["task_id"]
            assert task_id.startswith("task_")

            with client.stream("GET", "/api/v1/coding/tasks/task_missing/stream") as stream:
                assert stream.status_code == 200
                payload = stream.read().decode("utf-8")
            assert "event: error" in payload
            assert "Task not found: task_missing" in payload
            assert "event: done" in payload


def test_coding_task_tool_result_api_errors():
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "tailevents.db"
        settings = Settings(db_path=str(db_path))
        app = create_app(
            settings=settings,
            llm_client=FakeCodingLLMClient(),
            doc_retriever=FakeDocRetriever(),
        )

        with TestClient(app) as client:
            missing = client.post(
                "/api/v1/coding/tasks/task_missing/tool-result",
                json={
                    "call_id": "call_1",
                    "tool_name": "view_file",
                    "file_path": "pkg/demo.py",
                    "content": "pass\n",
                    "content_hash": "hash",
                },
            )
            assert missing.status_code == 404

            cancel_missing = client.post("/api/v1/coding/tasks/task_missing/cancel")
            assert cancel_missing.status_code == 404
