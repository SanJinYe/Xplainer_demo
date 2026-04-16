import pytest
import pytest_asyncio

from tailevents.cache import ExplanationCache
from tailevents.indexer import Indexer
from tailevents.ingestion import (
    IngestionPipeline,
    IngestionValidationError,
    RawEventValidator,
)
from tailevents.models.enums import ActionType
from tailevents.models.event import RawEvent
from tailevents.storage import (
    SQLiteConnectionManager,
    SQLiteEntityDB,
    SQLiteEventStore,
    SQLiteRelationStore,
    initialize_db,
)


class RecordingHook:
    def __init__(self):
        self.calls = []

    async def on_event_ingested(self, event, result) -> None:
        self.calls.append((event, result))


class FailingIndexer:
    async def process_event(self, event):
        raise RuntimeError("boom")


@pytest_asyncio.fixture
async def storage_bundle():
    database = SQLiteConnectionManager(":memory:")
    await initialize_db(database)

    bundle = {
        "database": database,
        "event_store": SQLiteEventStore(database),
        "entity_db": SQLiteEntityDB(database),
        "relation_store": SQLiteRelationStore(database),
        "cache": ExplanationCache(database),
    }
    yield bundle
    await database.close()


def test_validator_returns_structured_issues():
    validator = RawEventValidator()

    issues = validator.validate(
        {
            "action_type": "unknown",
            "file_path": " ",
            "code_snapshot": "",
            "intent": " ",
        }
    )

    by_field = {issue.field: issue for issue in issues}
    assert "action_type" in by_field
    assert by_field["file_path"].code == "empty"
    assert by_field["intent"].code == "empty"

    with pytest.raises(IngestionValidationError) as exc_info:
        validator.normalize(
            {
                "action_type": "unknown",
                "file_path": " ",
                "code_snapshot": "",
                "intent": " ",
            }
        )

    assert len(exc_info.value.issues) >= 3


@pytest.mark.asyncio
async def test_pipeline_happy_path_creates_entities_and_enriches_events(storage_bundle):
    hook = RecordingHook()
    pipeline = IngestionPipeline(
        event_store=storage_bundle["event_store"],
        indexer=Indexer(
            entity_db=storage_bundle["entity_db"],
            relation_store=storage_bundle["relation_store"],
            cache=storage_bundle["cache"],
        ),
        hooks=[hook],
    )

    event = await pipeline.ingest(
        RawEvent(
            action_type=ActionType.CREATE,
            file_path="api.py",
            code_snapshot="def fetch_data(url):\n    return url\n",
            intent="create fetch helper",
        )
    )

    assert len(event.entity_refs) == 1
    assert hook.calls[0][0].event_id == event.event_id
    assert hook.calls[0][1].pending is False

    stored_event = await storage_bundle["event_store"].get(event.event_id)
    assert stored_event is not None
    assert stored_event.entity_refs == event.entity_refs

    entities = await storage_bundle["entity_db"].get_by_file("api.py")
    assert len(entities) == 1
    assert entities[0].qualified_name == "fetch_data"
    assert entities[0].created_by_event == event.event_id


@pytest.mark.asyncio
async def test_pipeline_keeps_event_when_ast_parse_is_pending(storage_bundle):
    hook = RecordingHook()
    pipeline = IngestionPipeline(
        event_store=storage_bundle["event_store"],
        indexer=Indexer(
            entity_db=storage_bundle["entity_db"],
            relation_store=storage_bundle["relation_store"],
            cache=storage_bundle["cache"],
        ),
        hooks=[hook],
    )

    event = await pipeline.ingest(
        RawEvent(
            action_type=ActionType.MODIFY,
            file_path="broken.py",
            code_snapshot="def broken(:\n    pass\n",
            intent="edit half-finished code",
        )
    )

    stored_event = await storage_bundle["event_store"].get(event.event_id)
    assert stored_event is not None
    assert stored_event.entity_refs == []
    assert hook.calls[0][1].pending is True


@pytest.mark.asyncio
async def test_pipeline_keeps_event_when_indexer_raises(storage_bundle):
    hook = RecordingHook()
    pipeline = IngestionPipeline(
        event_store=storage_bundle["event_store"],
        indexer=FailingIndexer(),
        hooks=[hook],
    )

    event = await pipeline.ingest(
        RawEvent(
            action_type=ActionType.CREATE,
            file_path="service.py",
            code_snapshot="def noop():\n    return None\n",
            intent="store event even if indexer crashes",
        )
    )

    stored_event = await storage_bundle["event_store"].get(event.event_id)
    assert stored_event is not None
    assert stored_event.entity_refs == []
    assert hook.calls[0][1].pending is True


@pytest.mark.asyncio
async def test_pipeline_batch_preserves_input_order(storage_bundle):
    hook = RecordingHook()
    pipeline = IngestionPipeline(
        event_store=storage_bundle["event_store"],
        indexer=Indexer(
            entity_db=storage_bundle["entity_db"],
            relation_store=storage_bundle["relation_store"],
            cache=storage_bundle["cache"],
        ),
        hooks=[hook],
    )

    events = await pipeline.ingest_batch(
        [
            RawEvent(
                action_type=ActionType.CREATE,
                file_path="a.py",
                code_snapshot="def first():\n    return 1\n",
                intent="create first",
                session_id="session-batch",
            ),
            RawEvent(
                action_type=ActionType.CREATE,
                file_path="b.py",
                code_snapshot="def second():\n    return 2\n",
                intent="create second",
                session_id="session-batch",
            ),
        ]
    )

    assert [event.file_path for event in events] == ["a.py", "b.py"]
    assert [call[0].file_path for call in hook.calls] == ["a.py", "b.py"]
    stored = await storage_bundle["event_store"].get_by_session("session-batch")
    assert [event.file_path for event in stored] == ["a.py", "b.py"]
