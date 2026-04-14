import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tailevents.cache import ExplanationCache
from tailevents.explanation import ExplanationEngine
from tailevents.indexer import Indexer
from tailevents.ingestion import IngestionPipeline
from tailevents.models.event import RawEvent
from tailevents.storage import (
    SQLiteConnectionManager,
    SQLiteEntityDB,
    SQLiteEventStore,
    SQLiteRelationStore,
    initialize_db,
)


class DeterministicLLMClient:
    def __init__(self):
        self.calls = 0

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        self.calls += 1
        if "Qualified Name: fetch_api_data" in user_prompt:
            return """作用
负责从远端 API 拉取数据，并在失败时返回安全结果。
参数
- url: 请求地址
返回值
返回接口响应文本。
使用场景
被 DataProcessor.process 调用，用于获取原始数据。
设计背景
最初以 fetch_data 创建，后续补充错误处理并重命名为 fetch_api_data，以突出 API 语义。
"""
        if "Qualified Name: DataProcessor.process" in user_prompt:
            return """作用
调用 fetch_api_data 获取原始数据，记录日志后完成处理。
参数
- url: 数据来源地址
返回值
返回处理后的结果。
使用场景
作为 DataProcessor 的主处理入口。
设计背景
为了把数据拉取和处理解耦，process 会显式调用 fetch_api_data，并在后续修改中加入日志。
"""
        return """作用
提供实体说明。
参数
- value: 输入值
返回值
返回处理结果。
使用场景
用于 smoke test。
设计背景
用于验证 explanation pipeline。
"""


class NullDocRetriever:
    async def retrieve(self, package: str, symbol: str):
        return None


def build_raw_events() -> list[RawEvent]:
    return [
        RawEvent(
            action_type="create",
            file_path="data_processor.py",
            line_range=(1, 2),
            code_snapshot=(
                "def fetch_data(url):\n"
                "    return request_remote(url)\n"
            ),
            intent="create fetch_data to isolate remote API access",
            reasoning="start with a small helper before building the processing flow",
            session_id="session-smoke",
        ),
        RawEvent(
            action_type="modify",
            file_path="data_processor.py",
            line_range=(1, 5),
            code_snapshot=(
                "def fetch_data(url):\n"
                "    try:\n"
                "        return request_remote(url)\n"
                "    except RuntimeError:\n"
                "        return \"\"\n"
            ),
            intent="add error handling to fetch_data",
            reasoning="return a safe fallback when the remote API fails",
            session_id="session-smoke",
        ),
        RawEvent(
            action_type="rename",
            file_path="data_processor.py",
            line_range=(1, 5),
            code_snapshot=(
                "def fetch_api_data(url):\n"
                "    try:\n"
                "        return request_remote(url)\n"
                "    except RuntimeError:\n"
                "        return \"\"\n"
            ),
            intent="rename fetch_data to fetch_api_data",
            reasoning="make the helper name explicit before other callers depend on it",
            session_id="session-smoke",
        ),
        RawEvent(
            action_type="create",
            file_path="data_processor.py",
            line_range=(1, 11),
            code_snapshot=(
                "def fetch_api_data(url):\n"
                "    try:\n"
                "        return request_remote(url)\n"
                "    except RuntimeError:\n"
                "        return \"\"\n\n"
                "class DataProcessor:\n"
                "    def process(self, url):\n"
                "        raw = fetch_api_data(url)\n"
                "        return raw.strip().upper()\n"
            ),
            intent="create DataProcessor.process to call fetch_api_data",
            reasoning="keep processing separate while reusing the fetch helper",
            session_id="session-smoke",
        ),
        RawEvent(
            action_type="modify",
            file_path="data_processor.py",
            line_range=(1, 12),
            code_snapshot=(
                "def fetch_api_data(url):\n"
                "    try:\n"
                "        return request_remote(url)\n"
                "    except RuntimeError:\n"
                "        return \"\"\n\n"
                "class DataProcessor:\n"
                "    def process(self, url):\n"
                "        log_processing(url)\n"
                "        raw = fetch_api_data(url)\n"
                "        return raw.strip().upper()\n"
            ),
            intent="add logging to DataProcessor.process",
            reasoning="record each processing request before transforming the response",
            session_id="session-smoke",
        ),
    ]


def _summary_block(summary: dict) -> str:
    return (
        "══ E2E Smoke Test Summary ══\n"
        f"Events ingested:  {summary['events_ingested']}\n"
        f"Entities created: {summary['entities_created']}\n"
        f"Relations found:  {summary['relations_found']}\n"
        f"Explanation:      {summary['explanation_status']}\n"
        f"Cache hit:        {summary['cache_status']}\n"
        "═══════════════════════════"
    )


async def run_smoke_scenario(llm_client=None) -> dict:
    llm_client = llm_client or DeterministicLLMClient()

    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "smoke.db"
        database = SQLiteConnectionManager(str(database_path))
        await initialize_db(database)

        try:
            event_store = SQLiteEventStore(database)
            entity_db = SQLiteEntityDB(database)
            relation_store = SQLiteRelationStore(database)
            cache = ExplanationCache(database)
            indexer = Indexer(
                entity_db=entity_db,
                relation_store=relation_store,
                cache=cache,
            )
            pipeline = IngestionPipeline(
                event_store=event_store,
                indexer=indexer,
            )
            engine = ExplanationEngine(
                entity_db=entity_db,
                event_store=event_store,
                relation_store=relation_store,
                cache=cache,
                llm_client=llm_client,
                doc_retriever=NullDocRetriever(),
            )

            ingested_events = []
            for raw_event in build_raw_events():
                ingested_events.append(await pipeline.ingest(raw_event))

            assert len(ingested_events) == 5, "应该成功摄取 5 个 RawEvent。"

            created_entity_id = ingested_events[0].entity_refs[0].entity_id
            modified_entity_id = ingested_events[1].entity_refs[0].entity_id
            renamed_entity_id = ingested_events[2].entity_refs[0].entity_id
            assert (
                created_entity_id == modified_entity_id == renamed_entity_id
            ), "fetch_data -> fetch_api_data 的 entity_id 应在 create/modify/rename 过程中保持稳定。"

            fetch_entity = await entity_db.get_by_qualified_name("fetch_api_data")
            processor_entity = await entity_db.get_by_qualified_name("DataProcessor")
            process_entity = await entity_db.get_by_qualified_name("DataProcessor.process")

            assert fetch_entity is not None, "应存在 fetch_api_data 实体。"
            assert (
                fetch_entity.entity_id == created_entity_id
            ), "重命名后的 fetch_api_data 应保持原始 entity_id。"
            assert processor_entity is not None, "应存在 DataProcessor 实体。"
            assert process_entity is not None, "应存在 DataProcessor.process 实体。"

            outgoing_relations = await relation_store.get_outgoing(process_entity.entity_id)
            calls_relations = [
                relation
                for relation in outgoing_relations
                if relation.relation_type.value == "calls"
                and relation.target == fetch_entity.entity_id
            ]
            assert calls_relations, "DataProcessor.process 应至少存在一条指向 fetch_api_data 的 calls 关系。"

            first_explanation = await engine.explain_entity(
                fetch_entity.entity_id,
                detail_level="detailed",
                include_relations=True,
            )
            assert first_explanation.entity_id == fetch_entity.entity_id, "解释结果应指向 fetch_api_data 本身。"
            assert first_explanation.summary.strip(), "解释 summary 不应为空。"
            assert (
                "fetch_api_data" in first_explanation.detailed_explanation
            ), "解释内容应包含正确的实体名 fetch_api_data。"
            assert first_explanation.from_cache is False, "首次 explanation 调用应为 cache miss。"

            process_explanation = await engine.explain_entity(
                process_entity.entity_id,
                detail_level="detailed",
                include_relations=True,
            )
            assert (
                "fetch_api_data" in process_explanation.detailed_explanation
            ), "DataProcessor.process 的解释内容应体现它调用 fetch_api_data。"

            second_explanation = await engine.explain_entity(
                fetch_entity.entity_id,
                detail_level="detailed",
                include_relations=True,
            )
            assert second_explanation.from_cache is True, "第二次 explanation 调用应命中缓存。"

            summary = {
                "events_ingested": len(ingested_events),
                "entities_created": len([entity for entity in await entity_db.get_all() if not entity.is_deleted]),
                "relations_found": len(calls_relations),
                "explanation_status": "OK",
                "cache_status": "OK",
            }

            result = {
                "events": ingested_events,
                "fetch_entity": fetch_entity,
                "processor_entity": processor_entity,
                "process_entity": process_entity,
                "first_explanation": first_explanation,
                "process_explanation": process_explanation,
                "second_explanation": second_explanation,
                "summary": summary,
                "summary_block": _summary_block(summary),
            }
            print(result["summary_block"])
            return result
        finally:
            await database.close()


def test_e2e_smoke():
    result = asyncio.run(run_smoke_scenario())
    assert result["summary"]["events_ingested"] == 5, "Smoke test 应摄取全部 5 个事件。"
    assert result["summary"]["relations_found"] >= 1, "Smoke test 应找到至少一条 calls 关系。"
    assert result["summary"]["cache_status"] == "OK", "Smoke test 应验证 explanation cache 命中。"


if __name__ == "__main__":
    asyncio.run(run_smoke_scenario())
