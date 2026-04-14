from datetime import datetime, timedelta

import pytest
import pytest_asyncio
import httpx

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


STRUCTURED_OUTPUT = """作用
负责执行网络请求并封装重试逻辑。
参数
- url: 目标地址
- timeout: 超时时间
返回值
返回解析后的响应对象。
使用场景
用于 API 客户端访问远程服务。
设计背景
最初为了统一失败重试策略而创建，后续加入超时控制。
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

    create_event = TailEvent(
        action_type=ActionType.CREATE,
        file_path="client.py",
        line_range=(1, 8),
        code_snapshot="def fetch_data(url, timeout=1.0):\n    return {}\n",
        intent="创建统一的网络请求入口",
        reasoning="把远程调用集中到一个函数，方便后续加重试。",
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
        intent="为调用增加超时控制",
        reasoning="避免外部接口阻塞太久。",
        decision_alternatives=["全局默认超时", "逐调用超时参数"],
        timestamp=modified_at,
    )

    await event_store.put(create_event)
    await event_store.put(modify_event)

    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
        line_range=(1, 10),
        signature="def fetch_data(url: str, timeout: float = 1.0) -> dict",
        params=[
            ParamInfo(name="url", type_hint="str"),
            ParamInfo(name="timeout", type_hint="float", default="1.0"),
        ],
        return_type="dict",
        created_at=created_at,
        created_by_event=create_event.event_id,
        last_modified_event=modify_event.event_id,
        last_modified_at=modified_at,
        modification_count=1,
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
        ],
    )
    await entity_db.upsert(entity)

    helper_entity = CodeEntity(
        name="build_headers",
        qualified_name="build_headers",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
        line_range=(12, 16),
        signature="def build_headers() -> dict",
        created_at=created_at,
    )
    await entity_db.upsert(helper_entity)

    await relation_store.put(
        Relation(
            source=entity.entity_id,
            target=helper_entity.entity_id,
            relation_type=RelationType.CALLS,
            provenance=Provenance.AST_DERIVED,
            from_event=modify_event.event_id,
            context="build request headers before sending",
        )
    )

    return {
        "entity": entity,
        "helper_entity": helper_entity,
        "events": [create_event, modify_event],
    }


def test_context_assembler_handles_detail_levels():
    assembler = ContextAssembler()
    created_at = datetime(2026, 4, 13, 12, 0, 0)
    modified_at = created_at + timedelta(minutes=5)

    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
        line_range=(1, 10),
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
            decision_alternatives=["默认超时", "调用方传参"],
            timestamp=modified_at,
        ),
    ]
    related_entities = [
        {
            "qualified_name": "build_headers",
            "entity_type": "function",
            "direction": "outgoing",
            "relation_type": "calls",
            "context": "prepare headers",
        }
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
    detailed = assembler.assemble(
        entity, events, related_entities, doc_snippets, "detailed"
    )
    trace = assembler.assemble(entity, events, related_entities, doc_snippets, "trace")

    assert "# Creation Context" in summary
    assert "# Modification History" not in summary
    assert "# Modification History" in detailed
    assert "# Related Entities" in detailed
    assert "This entity calls:" in detailed
    assert "# External Dependencies" in detailed
    assert "# Event Trace" in trace
    assert "Alternatives: 默认超时, 调用方传参" in trace


def test_formatter_parses_structured_output():
    formatter = ExplanationFormatter()
    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
        signature="def fetch_data(url: str, timeout: float = 1.0) -> dict",
    )

    explanation = formatter.format(entity, STRUCTURED_OUTPUT)

    assert explanation.summary == "负责执行网络请求并封装重试逻辑。"
    assert explanation.param_explanations == {
        "url": "目标地址",
        "timeout": "超时时间",
    }
    assert explanation.return_explanation == "返回解析后的响应对象。"
    assert explanation.usage_context == "用于 API 客户端访问远程服务。"
    assert explanation.creation_intent == "最初为了统一失败重试策略而创建，后续加入超时控制。"


def test_formatter_falls_back_for_malformed_output():
    formatter = ExplanationFormatter()
    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
    )

    explanation = formatter.format(entity, "plain text without any expected sections")

    assert explanation.summary == "plain text without any expected sections"
    assert explanation.detailed_explanation == "plain text without any expected sections"
    assert explanation.param_explanations is None


def test_formatter_strips_backticks_from_param_names():
    formatter = ExplanationFormatter()
    entity = CodeEntity(
        name="fetch_data",
        qualified_name="fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
    )

    explanation = formatter.format(
        entity,
        """作用
读取远程数据
参数
- `url`: 目标地址
- ``timeout``: 超时时间
返回值
返回解析后的结果
使用场景
API 请求
设计背景
保持网络访问统一
""",
    )

    assert explanation.param_explanations == {
        "url": "目标地址",
        "timeout": "超时时间",
    }


def test_formatter_parses_multiline_param_blocks():
    formatter = ExplanationFormatter()
    entity = CodeEntity(
        name="fetch_api_data",
        qualified_name="fetch_api_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
    )

    explanation = formatter.format(
        entity,
        """作用
访问远端 API。
参数
`url`
- 类型：字符串 URL
- 作用：指定要请求的远端地址
返回值
返回响应结果。
使用场景
在处理流程前获取原始数据。
设计背景
为了统一远端访问入口。
""",
    )

    assert explanation.param_explanations == {
        "url": "类型：字符串 URL 作用：指定要请求的远端地址",
    }


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
    assert first.summary == "负责执行网络请求并封装重试逻辑。"
    assert first.related_entities[0]["qualified_name"] == "build_headers"
    assert first.external_doc_snippets[0]["package"] == "httpx"
    assert len(first.modification_history) == 1
    assert len(llm_client.calls) == 1
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
async def test_engine_handles_missing_doc_snippets(explanation_bundle):
    seeded = await seed_explanation_data(explanation_bundle)
    llm_client = FakeLLMClient(STRUCTURED_OUTPUT)

    engine = ExplanationEngine(
        entity_db=explanation_bundle["entity_db"],
        event_store=explanation_bundle["event_store"],
        relation_store=explanation_bundle["relation_store"],
        cache=explanation_bundle["cache"],
        llm_client=llm_client,
        doc_retriever=FakeDocRetriever(),
    )

    explanation = await engine.explain_entity(
        seeded["entity"].entity_id,
        detail_level="summary",
        include_relations=False,
    )

    assert explanation.external_doc_snippets == []
    assert len(llm_client.calls) == 1


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
