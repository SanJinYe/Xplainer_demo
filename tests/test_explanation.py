from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from tailevents.cache import ExplanationCache
from tailevents.config import Settings
from tailevents.explanation import (
    ContextAssembler,
    EXPLANATION_PROMPT_VERSION,
    ExplanationEngine,
    ExplanationFormatter,
    LLMClientFactory,
    OpenRouterLLMClient,
)
from tailevents.explanation.exceptions import (
    EntityExplanationNotFoundError,
    UnsupportedLLMBackendError,
)
from tailevents.models.entity import CodeEntity, EventRef, ParamInfo
from tailevents.models.enums import (
    ActionType,
    EntityRole,
    EntityType,
    Provenance,
    RelationType,
    UsagePattern,
)
from tailevents.models.event import ExternalRef, TailEvent
from tailevents.models.relation import Relation
from tailevents.storage import (
    SQLiteConnectionManager,
    SQLiteEntityDB,
    SQLiteEventStore,
    SQLiteRelationStore,
    initialize_db,
)


STRUCTURED_OUTPUT = """核心作用
统一发起外部请求并封装超时控制，对上层调用方暴露稳定入口。

关键上下文
这是 client 层默认的网络访问入口，被多个调用方复用。

关键事件
- 初始创建时集中远程访问逻辑。
- 后续修改加入超时控制。
- 最近一次修改补充请求头构建。

关联实体
- caller: ApiClient.fetch_profile
- callee: build_headers
"""

LEGACY_STRUCTURED_OUTPUT = """作用
负责执行网络请求并统一失败处理。

参数
- url: 目标地址
- timeout: 超时时间

返回值
返回解析后的响应结果。

使用场景
用于 API 客户端访问远程服务。

设计背景
最初为了集中远程访问逻辑，后续加入超时控制。
"""


class FakeLLMClient:
    def __init__(self, responses):
        if isinstance(responses, list):
            self._responses = list(responses)
        else:
            self._responses = [responses]
        self.calls = []

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class FakeDocRetriever:
    def __init__(self, mapping=None):
        self._mapping = mapping or {}
        self.calls = []

    async def retrieve(self, package: str, symbol: str):
        self.calls.append((package, symbol))
        return self._mapping.get((package, symbol))


class CountingRelationStore:
    def __init__(self, relation_store: SQLiteRelationStore):
        self._relation_store = relation_store
        self.outgoing_calls = 0
        self.incoming_calls = 0

    async def get_outgoing(self, entity_id: str):
        self.outgoing_calls += 1
        return await self._relation_store.get_outgoing(entity_id)

    async def get_incoming(self, entity_id: str):
        self.incoming_calls += 1
        return await self._relation_store.get_incoming(entity_id)


@pytest_asyncio.fixture
async def explanation_bundle():
    database = SQLiteConnectionManager(":memory:")
    await initialize_db(database)

    bundle = {
        "database": database,
        "entity_db": SQLiteEntityDB(database),
        "event_store": SQLiteEventStore(database),
        "relation_store": SQLiteRelationStore(database),
        "cache": ExplanationCache(database),
    }

    yield bundle

    await database.close()


async def seed_explanation_data(bundle):
    entity_db = bundle["entity_db"]
    event_store = bundle["event_store"]
    relation_store = bundle["relation_store"]

    created_at = datetime(2026, 4, 13, 12, 0, 0)
    modified_at = created_at + timedelta(minutes=15)
    latest_at = modified_at + timedelta(minutes=15)

    create_event = TailEvent(
        action_type=ActionType.CREATE,
        file_path="client.py",
        line_range=(1, 8),
        code_snapshot="def fetch_data(url, timeout=1.0):\n    return {}\n",
        intent="创建统一的网络请求入口",
        reasoning="把远程调用集中到一个函数，便于后续补充能力。",
        timestamp=created_at,
        external_refs=[
            ExternalRef(
                package="httpx",
                symbol="httpx.get",
                usage_pattern=UsagePattern.DIRECT_CALL,
            )
        ],
    )
    modify_event = TailEvent(
        action_type=ActionType.MODIFY,
        file_path="client.py",
        line_range=(1, 10),
        code_snapshot="def fetch_data(url, timeout=1.0):\n    return {}\n",
        intent="增加超时控制",
        reasoning="避免外部接口长时间阻塞。",
        decision_alternatives=["全局默认超时", "调用方传入超时参数"],
        timestamp=modified_at,
    )
    latest_event = TailEvent(
        action_type=ActionType.MODIFY,
        file_path="client.py",
        line_range=(1, 12),
        code_snapshot="def fetch_data(url, timeout=1.0):\n    return {}\n",
        intent="补充请求头构建",
        reasoning="统一 headers 生成逻辑。",
        timestamp=latest_at,
    )

    for event in [create_event, modify_event, latest_event]:
        await event_store.put(event)

    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
        line_range=(1, 12),
        signature="def fetch_data(url: str, timeout: float = 1.0) -> dict",
        params=[
            ParamInfo(name="url", type_hint="str"),
            ParamInfo(name="timeout", type_hint="float", default="1.0"),
        ],
        return_type="dict",
        created_at=created_at,
        created_by_event=create_event.event_id,
        last_modified_event=latest_event.event_id,
        last_modified_at=latest_at,
        modification_count=2,
        event_refs=[
            EventRef(
                event_id=create_event.event_id,
                role=EntityRole.PRIMARY,
                timestamp=create_event.timestamp,
            ),
            EventRef(
                event_id=modify_event.event_id,
                role=EntityRole.MODIFIED,
                timestamp=modify_event.timestamp,
            ),
            EventRef(
                event_id=latest_event.event_id,
                role=EntityRole.MODIFIED,
                timestamp=latest_event.timestamp,
            ),
        ],
    )
    await entity_db.upsert(entity)

    helper_entity = CodeEntity(
        name="build_headers",
        qualified_name="build_headers",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
        line_range=(14, 18),
        signature="def build_headers() -> dict",
        created_at=created_at,
    )
    caller_entity = CodeEntity(
        name="fetch_profile",
        qualified_name="ApiClient.fetch_profile",
        entity_type=EntityType.METHOD,
        file_path="api_client.py",
        line_range=(5, 9),
        signature="def fetch_profile(self, user_id: str) -> dict",
        created_at=created_at,
    )
    importer_entity = CodeEntity(
        name="HttpClient",
        qualified_name="HttpClient",
        entity_type=EntityType.CLASS,
        file_path="transport.py",
        line_range=(1, 10),
        created_at=created_at,
    )

    for related_entity in [helper_entity, caller_entity, importer_entity]:
        await entity_db.upsert(related_entity)

    await relation_store.put(
        Relation(
            source=entity.entity_id,
            target=helper_entity.entity_id,
            relation_type=RelationType.CALLS,
            provenance=Provenance.AST_DERIVED,
            from_event=latest_event.event_id,
            context="build request headers before sending",
        )
    )
    await relation_store.put(
        Relation(
            source=caller_entity.entity_id,
            target=entity.entity_id,
            relation_type=RelationType.CALLS,
            provenance=Provenance.AST_DERIVED,
            from_event=latest_event.event_id,
            context="fetch the remote profile",
        )
    )
    await relation_store.put(
        Relation(
            source=entity.entity_id,
            target=importer_entity.entity_id,
            relation_type=RelationType.IMPORTS,
            provenance=Provenance.AST_DERIVED,
            from_event=create_event.event_id,
            context="reuse transport client",
        )
    )

    return {
        "entity": entity,
        "helper_entity": helper_entity,
        "caller_entity": caller_entity,
        "importer_entity": importer_entity,
        "events": [create_event, modify_event, latest_event],
    }


async def seed_baseline_only_entity(bundle):
    entity_db = bundle["entity_db"]
    event_store = bundle["event_store"]

    baseline_event = TailEvent(
        action_type=ActionType.BASELINE,
        file_path="pkg/settings.py",
        code_snapshot="class Settings:\n    pass\n",
        intent="Bootstrap existing repository file",
        reasoning=None,
        decision_alternatives=None,
        timestamp=datetime(2026, 4, 16, 10, 0, 0),
    )
    await event_store.put(baseline_event)

    entity = CodeEntity(
        name="Settings",
        qualified_name="Settings",
        entity_type=EntityType.CLASS,
        file_path="pkg/settings.py",
        line_range=(1, 2),
        signature="class Settings",
        created_at=baseline_event.timestamp,
        created_by_event=baseline_event.event_id,
        last_modified_event=baseline_event.event_id,
        last_modified_at=baseline_event.timestamp,
        event_refs=[
            EventRef(
                event_id=baseline_event.event_id,
                role=EntityRole.PRIMARY,
                timestamp=baseline_event.timestamp,
            )
        ],
    )
    await entity_db.upsert(entity)
    return {"entity": entity, "event": baseline_event}


def test_context_assembler_handles_detail_levels():
    assembler = ContextAssembler()
    created_at = datetime(2026, 4, 13, 12, 0, 0)

    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
        line_range=(1, 12),
        signature="def fetch_data(url: str) -> dict",
    )
    events = [
        TailEvent(
            action_type=ActionType.CREATE,
            file_path="client.py",
            code_snapshot="def fetch_data(url):\n    return {}\n",
            intent="创建网络请求入口",
            reasoning="统一访问逻辑。",
            timestamp=created_at,
        ),
        TailEvent(
            action_type=ActionType.MODIFY,
            file_path="client.py",
            code_snapshot="def fetch_data(url):\n    return {}\n",
            intent="增加超时控制",
            reasoning="避免阻塞。",
            timestamp=created_at + timedelta(minutes=5),
        ),
        TailEvent(
            action_type=ActionType.MODIFY,
            file_path="client.py",
            code_snapshot="def fetch_data(url):\n    return {}\n",
            intent="重构重试策略",
            reasoning="减少重复分支。",
            timestamp=created_at + timedelta(minutes=10),
        ),
        TailEvent(
            action_type=ActionType.MODIFY,
            file_path="client.py",
            code_snapshot="def fetch_data(url):\n    return {}\n",
            intent="补充请求头构建",
            reasoning="统一 headers。",
            timestamp=created_at + timedelta(minutes=15),
        ),
    ]
    related_entities = [
        {
            "qualified_name": "Caller.one",
            "entity_type": "method",
            "direction": "incoming",
            "relation_type": "calls",
            "context": "first caller",
        },
        {
            "qualified_name": "Caller.two",
            "entity_type": "method",
            "direction": "incoming",
            "relation_type": "calls",
            "context": "second caller",
        },
        {
            "qualified_name": "Caller.three",
            "entity_type": "method",
            "direction": "incoming",
            "relation_type": "calls",
            "context": "third caller",
        },
        {
            "qualified_name": "build_headers",
            "entity_type": "function",
            "direction": "outgoing",
            "relation_type": "calls",
            "context": "prepare headers",
        },
        {
            "qualified_name": "send_request",
            "entity_type": "function",
            "direction": "outgoing",
            "relation_type": "calls",
            "context": "dispatch request",
        },
        {
            "qualified_name": "parse_response",
            "entity_type": "function",
            "direction": "outgoing",
            "relation_type": "calls",
            "context": "decode response",
        },
        {
            "qualified_name": "HttpClient",
            "entity_type": "class",
            "direction": "incoming",
            "relation_type": "imports",
            "context": "transport dependency",
        },
    ]
    doc_snippets = [
        {
            "package": "httpx",
            "symbol": "httpx.get",
            "usage_pattern": "direct_call",
            "snippet": "httpx.get sends a GET request.",
        }
    ]

    summary = assembler.assemble(entity, events, related_entities, doc_snippets, "summary")
    detailed = assembler.assemble(entity, events, related_entities, doc_snippets, "detailed")
    trace = assembler.assemble(entity, events, related_entities, doc_snippets, "trace")

    assert "# Event Context" in summary
    assert "创建网络请求入口" in summary
    assert "补充请求头构建" in summary
    assert "增加超时控制" not in summary
    assert "# Call Relations" not in summary
    assert "# External Dependencies" not in summary

    assert "# Event Context" in detailed
    assert "增加超时控制" not in detailed
    assert "重构重试策略" in detailed
    assert "补充请求头构建" in detailed
    assert "# Call Relations" in detailed
    assert "Caller.one" in detailed
    assert "Caller.two" in detailed
    assert "Caller.three" not in detailed
    assert "build_headers" in detailed
    assert "send_request" in detailed
    assert "parse_response" not in detailed
    assert "HttpClient" not in detailed
    assert "# External Dependencies" in detailed

    assert "# Creation Context" in trace
    assert "# Modification History" in trace
    assert "# Event Trace" in trace
    assert "HttpClient" in trace


def test_formatter_parses_structured_output():
    formatter = ExplanationFormatter()
    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
        signature="def fetch_data(url: str, timeout: float = 1.0) -> dict",
    )

    explanation = formatter.format(entity, STRUCTURED_OUTPUT, detail_level="detailed")

    assert explanation.summary == "统一发起外部请求并封装超时控制，对上层调用方暴露稳定入口。"
    assert explanation.detailed_explanation is not None
    assert "核心作用" in explanation.detailed_explanation
    assert "关键上下文" in explanation.detailed_explanation
    assert "关键事件" in explanation.detailed_explanation
    assert "关联实体" in explanation.detailed_explanation
    assert len(explanation.detailed_explanation) <= 1200
    assert explanation.param_explanations is None
    assert explanation.return_explanation is None


def test_formatter_summary_enforces_sentence_and_length_limits():
    formatter = ExplanationFormatter()
    entity = CodeEntity(
        name="Settings",
        qualified_name="Settings",
        entity_type=EntityType.CLASS,
        file_path="settings.py",
    )
    raw_output = (
        "第一句说明它做什么。"
        "第二句说明它对上下文的直接作用。"
        "第三句不应该被保留。"
        + ("这" * 140)
    )

    explanation = formatter.format(entity, raw_output, detail_level="summary")

    assert explanation.detailed_explanation is None
    assert "第三句不应该被保留" not in explanation.summary
    assert len(explanation.summary) <= 120


def test_formatter_supports_legacy_output():
    formatter = ExplanationFormatter()
    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
    )

    explanation = formatter.format(entity, LEGACY_STRUCTURED_OUTPUT, detail_level="detailed")

    assert explanation.summary == "负责执行网络请求并统一失败处理。"
    assert explanation.param_explanations == {
        "url": "目标地址",
        "timeout": "超时时间",
    }
    assert explanation.return_explanation == "返回解析后的响应结果。"
    assert explanation.usage_context == "用于 API 客户端访问远程服务。"
    assert explanation.creation_intent == "最初为了集中远程访问逻辑，后续加入超时控制。"
    assert explanation.detailed_explanation is not None
    assert "核心作用" in explanation.detailed_explanation


def test_formatter_falls_back_for_malformed_output():
    formatter = ExplanationFormatter()
    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
    )

    malformed = "plain text without sections " * 100
    explanation = formatter.format(entity, malformed, detail_level="detailed")

    assert len(explanation.summary) <= 120
    assert explanation.detailed_explanation is not None
    assert len(explanation.detailed_explanation) <= 1200
    assert explanation.param_explanations is None


@pytest.mark.asyncio
async def test_engine_cache_miss_then_hit(explanation_bundle):
    seeded = await seed_explanation_data(explanation_bundle)
    llm_client = FakeLLMClient(STRUCTURED_OUTPUT)
    doc_retriever = FakeDocRetriever(
        {("httpx", "httpx.get"): "httpx.get issues a GET request and returns a response."}
    )

    engine = ExplanationEngine(
        entity_db=explanation_bundle["entity_db"],
        event_store=explanation_bundle["event_store"],
        relation_store=explanation_bundle["relation_store"],
        cache=explanation_bundle["cache"],
        llm_client=llm_client,
        doc_retriever=doc_retriever,
        max_events=20,
        temperature=0.1,
    )

    first = await engine.explain_entity(
        seeded["entity"].entity_id,
        detail_level="detailed",
        include_relations=True,
    )

    assert first.from_cache is False
    assert first.summary == "统一发起外部请求并封装超时控制，对上层调用方暴露稳定入口。"
    assert any(item["qualified_name"] == "build_headers" for item in first.related_entities)
    assert any(item["qualified_name"] == "ApiClient.fetch_profile" for item in first.related_entities)
    assert first.external_doc_snippets[0]["package"] == "httpx"
    assert len(first.modification_history) == 2
    assert len(llm_client.calls) == 1
    assert llm_client.calls[0]["max_tokens"] == 1800
    assert await explanation_bundle["cache"].get(
        f"explain:{EXPLANATION_PROMPT_VERSION}:{seeded['entity'].entity_id}:detailed:1"
    ) is not None

    stored_entity = await explanation_bundle["entity_db"].get(seeded["entity"].entity_id)
    assert stored_entity is not None
    assert stored_entity.cached_description == first.summary
    assert stored_entity.description_valid is True

    second = await engine.explain_entity(
        seeded["entity"].entity_id,
        detail_level="detailed",
        include_relations=True,
    )

    assert second.from_cache is True
    assert second.summary == first.summary
    assert len(llm_client.calls) == 1


@pytest.mark.asyncio
async def test_engine_skips_relation_lookup_when_disabled(explanation_bundle):
    seeded = await seed_explanation_data(explanation_bundle)
    llm_client = FakeLLMClient(STRUCTURED_OUTPUT)
    relation_store = CountingRelationStore(explanation_bundle["relation_store"])

    engine = ExplanationEngine(
        entity_db=explanation_bundle["entity_db"],
        event_store=explanation_bundle["event_store"],
        relation_store=relation_store,
        cache=explanation_bundle["cache"],
        llm_client=llm_client,
        doc_retriever=FakeDocRetriever(),
    )

    explanation = await engine.explain_entity(
        seeded["entity"].entity_id,
        detail_level="summary",
        include_relations=False,
    )

    assert explanation.related_entities == []
    assert relation_store.outgoing_calls == 0
    assert relation_store.incoming_calls == 0
    assert llm_client.calls[0]["max_tokens"] == 250


@pytest.mark.asyncio
async def test_engine_summary_prompt_omits_external_docs(explanation_bundle):
    seeded = await seed_explanation_data(explanation_bundle)
    llm_client = FakeLLMClient("统一处理网络请求，并给调用方提供稳定入口。")
    doc_retriever = FakeDocRetriever(
        {("httpx", "httpx.get"): "httpx.get issues a GET request and returns a response."}
    )

    engine = ExplanationEngine(
        entity_db=explanation_bundle["entity_db"],
        event_store=explanation_bundle["event_store"],
        relation_store=explanation_bundle["relation_store"],
        cache=explanation_bundle["cache"],
        llm_client=llm_client,
        doc_retriever=doc_retriever,
        cache_enabled=False,
    )

    explanation = await engine.explain_entity(
        seeded["entity"].entity_id,
        detail_level="summary",
        include_relations=False,
    )

    assert explanation.external_doc_snippets[0]["package"] == "httpx"
    assert "# External Dependencies" not in llm_client.calls[0]["user_prompt"]
    assert "httpx.get issues a GET request" not in llm_client.calls[0]["user_prompt"]


@pytest.mark.asyncio
async def test_engine_baseline_only_prompt_adds_guardrail(explanation_bundle):
    seeded = await seed_baseline_only_entity(explanation_bundle)
    llm_client = FakeLLMClient(STRUCTURED_OUTPUT)

    engine = ExplanationEngine(
        entity_db=explanation_bundle["entity_db"],
        event_store=explanation_bundle["event_store"],
        relation_store=explanation_bundle["relation_store"],
        cache=explanation_bundle["cache"],
        llm_client=llm_client,
        doc_retriever=FakeDocRetriever(),
        cache_enabled=False,
    )

    explanation = await engine.explain_entity(
        seeded["entity"].entity_id,
        detail_level="detailed",
        include_relations=False,
    )

    assert "不要猜测创建动机" in llm_client.calls[0]["user_prompt"]
    assert len(explanation.summary) <= 120
    assert explanation.detailed_explanation is not None
    assert "核心作用" in explanation.detailed_explanation
    assert len(explanation.detailed_explanation) <= 1200


@pytest.mark.asyncio
async def test_engine_raises_for_missing_entity(explanation_bundle):
    engine = ExplanationEngine(
        entity_db=explanation_bundle["entity_db"],
        event_store=explanation_bundle["event_store"],
        relation_store=explanation_bundle["relation_store"],
        cache=explanation_bundle["cache"],
        llm_client=FakeLLMClient(STRUCTURED_OUTPUT),
        doc_retriever=FakeDocRetriever(),
    )

    with pytest.raises(EntityExplanationNotFoundError):
        await engine.explain_entity("ent_missing")


@pytest.mark.asyncio
async def test_explain_entities_preserves_input_order(explanation_bundle):
    seeded = await seed_explanation_data(explanation_bundle)
    second_entity = CodeEntity(
        name="parse_payload",
        qualified_name="parse_payload",
        entity_type=EntityType.FUNCTION,
        file_path="parser.py",
        signature="def parse_payload(data: dict) -> dict",
        event_refs=[],
    )
    await explanation_bundle["entity_db"].upsert(second_entity)

    llm_client = FakeLLMClient([STRUCTURED_OUTPUT, STRUCTURED_OUTPUT])
    engine = ExplanationEngine(
        entity_db=explanation_bundle["entity_db"],
        event_store=explanation_bundle["event_store"],
        relation_store=explanation_bundle["relation_store"],
        cache=explanation_bundle["cache"],
        llm_client=llm_client,
        doc_retriever=FakeDocRetriever(),
        cache_enabled=False,
    )

    explanations = await engine.explain_entities(
        [second_entity.entity_id, seeded["entity"].entity_id],
        detail_level="summary",
        include_relations=False,
    )

    assert [item.entity_id for item in explanations] == [
        second_entity.entity_id,
        seeded["entity"].entity_id,
    ]


@pytest.mark.asyncio
async def test_openrouter_client_parses_response(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "openrouter generated explanation"
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, exc_tb):
            return None

        async def post(self, url: str, headers: dict, json: dict):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = OpenRouterLLMClient(
        api_key="test-key",
        model="openai/gpt-4o-mini",
        base_url="https://openrouter.ai/api/v1",
        proxy_url="http://127.0.0.1:7897",
        site_url="https://example.com",
        app_name="TailEvents",
    )
    content = await client.generate(
        system_prompt="system prompt",
        user_prompt="user prompt",
        max_tokens=256,
        temperature=0.2,
    )

    assert content == "openrouter generated explanation"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["headers"]["HTTP-Referer"] == "https://example.com"
    assert captured["headers"]["X-Title"] == "TailEvents"
    assert captured["json"]["model"] == "openai/gpt-4o-mini"
    assert captured["client_kwargs"]["proxy"] == "http://127.0.0.1:7897"


def test_llm_client_factory_supports_openrouter():
    settings = Settings(
        _env_file=None,
        llm_backend="openrouter",
        openrouter_api_key="test-key",
        openrouter_model="openai/gpt-4o-mini",
        openrouter_base_url="https://openrouter.ai/api/v1",
    )

    client = LLMClientFactory.create(settings)

    assert isinstance(client, OpenRouterLLMClient)


def test_llm_client_factory_rejects_missing_openrouter_key():
    settings = Settings(
        _env_file=None,
        llm_backend="openrouter",
        openrouter_model="openai/gpt-4o-mini",
        openrouter_base_url="https://openrouter.ai/api/v1",
    )

    with pytest.raises(UnsupportedLLMBackendError):
        LLMClientFactory.create(settings)
