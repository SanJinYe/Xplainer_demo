from datetime import datetime

import pytest
import pytest_asyncio

from tailevents.models.entity import CodeEntity, EventRef, ParamInfo, RenameRecord
from tailevents.models.enums import (
    ActionType,
    EntityRole,
    EntityType,
    Provenance,
    RelationType,
    UsagePattern,
)
from tailevents.models.event import EntityRef as EventEntityRef
from tailevents.models.event import ExternalRef, TailEvent
from tailevents.models.relation import Relation
from tailevents.storage import (
    EventEnrichmentError,
    SQLiteConnectionManager,
    SQLiteEntityDB,
    SQLiteEventStore,
    SQLiteRelationStore,
    initialize_db,
)


@pytest_asyncio.fixture
async def storage_bundle():
    database = SQLiteConnectionManager(":memory:")
    await initialize_db(database)

    yield {
        "database": database,
        "events": SQLiteEventStore(database),
        "entities": SQLiteEntityDB(database),
        "relations": SQLiteRelationStore(database),
    }

    await database.close()


@pytest.mark.asyncio
async def test_event_store_crud_and_enrichment(storage_bundle):
    event_store = storage_bundle["events"]

    event = TailEvent(
        action_type=ActionType.CREATE,
        file_path="src/app.py",
        line_range=(10, 20),
        code_snapshot="def foo():\n    return 1\n",
        intent="create foo",
        reasoning="needed for bootstrap",
        decision_alternatives=["use lambda", "inline logic"],
        agent_step_id="step-1",
        session_id="session-1",
        external_refs=[
            ExternalRef(
                package="httpx",
                symbol="httpx.get",
                version="0.27",
                doc_uri="https://www.python-httpx.org/",
                usage_pattern=UsagePattern.DIRECT_CALL,
            )
        ],
    )

    await event_store.put(event)
    loaded = await event_store.get(event.event_id)

    assert loaded is not None
    assert loaded.event_id == event.event_id
    assert loaded.line_range == (10, 20)
    assert loaded.decision_alternatives == ["use lambda", "inline logic"]
    assert loaded.external_refs[0].package == "httpx"
    assert loaded.entity_refs == []

    assert len(await event_store.get_batch([event.event_id])) == 1
    assert len(await event_store.get_by_session("session-1")) == 1
    assert len(await event_store.get_by_file("src/app.py")) == 1
    assert len(await event_store.get_recent(limit=1)) == 1
    assert await event_store.count() == 1

    refs = [EventEntityRef(entity_id="ent_abc123", role=EntityRole.PRIMARY)]
    await event_store.enrich(event.event_id, refs)

    enriched = await event_store.get(event.event_id)
    assert enriched is not None
    assert len(enriched.entity_refs) == 1
    assert enriched.entity_refs[0].entity_id == "ent_abc123"

    with pytest.raises(EventEnrichmentError):
        await event_store.enrich(event.event_id, refs)


@pytest.mark.asyncio
async def test_entity_db_crud_search_and_json_round_trip(storage_bundle):
    entity_db = storage_bundle["entities"]

    entity = CodeEntity(
        name="retry_with_backoff",
        qualified_name="utils.network.retry_with_backoff",
        entity_type=EntityType.FUNCTION,
        file_path="utils/network.py",
        line_range=(45, 78),
        signature="def retry_with_backoff(fn, max_retries=3) -> Any",
        params=[
            ParamInfo(
                name="fn",
                type_hint="Callable",
                default=None,
                description="target function",
            )
        ],
        return_type="Any",
        docstring="Retry a function.",
        created_at=datetime.utcnow(),
        created_by_event="te_create",
        last_modified_event="te_modify",
        last_modified_at=datetime.utcnow(),
        modification_count=2,
        event_refs=[
            EventRef(
                event_id="te_create",
                role=EntityRole.PRIMARY,
                timestamp=datetime.utcnow(),
            )
        ],
        rename_history=[
            RenameRecord(
                old_qualified_name="utils.old.retry_with_backoff",
                new_qualified_name="utils.network.retry_with_backoff",
                event_id="te_rename",
                timestamp=datetime.utcnow(),
            )
        ],
        cached_description="Retries API calls with backoff.",
        description_valid=True,
        in_degree=1,
        out_degree=2,
        tags=["retry", "network"],
    )

    await entity_db.upsert(entity)

    loaded = await entity_db.get(entity.entity_id)
    assert loaded is not None
    assert loaded.params[0].name == "fn"
    assert loaded.event_refs[0].event_id == "te_create"
    assert loaded.rename_history[0].old_qualified_name == "utils.old.retry_with_backoff"
    assert loaded.tags == ["retry", "network"]

    assert len(await entity_db.get_by_name("retry_with_backoff")) == 1
    assert len(await entity_db.get_by_file("utils/network.py")) == 1
    assert await entity_db.get_by_qualified_name(
        "utils.network.retry_with_backoff"
    ) is not None
    assert await entity_db.get_by_qualified_name(
        "utils.old.retry_with_backoff"
    ) is not None

    search_results = await entity_db.search("backoff")
    assert len(search_results) == 1
    assert search_results[0].entity_id == entity.entity_id

    await entity_db.update_description(entity.entity_id, "Retries external requests.")
    updated = await entity_db.get(entity.entity_id)
    assert updated is not None
    assert updated.cached_description == "Retries external requests."
    assert updated.description_valid is True

    assert len(await entity_db.search("external")) == 1

    await entity_db.invalidate_description(entity.entity_id)
    invalidated = await entity_db.get(entity.entity_id)
    assert invalidated is not None
    assert invalidated.description_valid is False
    assert invalidated.cached_description == "Retries external requests."

    assert await entity_db.count() == 1


@pytest.mark.asyncio
async def test_entity_db_mark_deleted_removes_from_fts(storage_bundle):
    entity_db = storage_bundle["entities"]

    entity = CodeEntity(
        name="fetch_data",
        qualified_name="client.fetch_data",
        entity_type=EntityType.FUNCTION,
        file_path="client.py",
        cached_description="Fetch remote data",
    )

    await entity_db.upsert(entity)
    assert len(await entity_db.search("remote")) == 1

    await entity_db.mark_deleted(entity.entity_id, "te_delete")

    deleted = await entity_db.get(entity.entity_id)
    assert deleted is not None
    assert deleted.is_deleted is True
    assert len(await entity_db.search("remote")) == 0


@pytest.mark.asyncio
async def test_relation_store_crud(storage_bundle):
    entity_db = storage_bundle["entities"]
    relation_store = storage_bundle["relations"]

    source = CodeEntity(
        entity_id="ent_source",
        name="caller",
        qualified_name="app.caller",
        entity_type=EntityType.FUNCTION,
        file_path="app.py",
    )
    target = CodeEntity(
        entity_id="ent_target",
        name="callee",
        qualified_name="app.callee",
        entity_type=EntityType.FUNCTION,
        file_path="app.py",
    )

    await entity_db.upsert(source)
    await entity_db.upsert(target)

    relation = Relation(
        source=source.entity_id,
        target=target.entity_id,
        relation_type=RelationType.CALLS,
        provenance=Provenance.AST_DERIVED,
        confidence=0.95,
        from_event="te_call",
        context="caller invokes callee",
    )

    await relation_store.put(relation)

    assert len(await relation_store.get_outgoing(source.entity_id)) == 1
    assert len(await relation_store.get_incoming(target.entity_id)) == 1
    assert len(await relation_store.get_between(source.entity_id, target.entity_id)) == 1
    assert len(await relation_store.get_by_event("te_call")) == 1
    assert len(await relation_store.get_all_active()) == 1
    assert await relation_store.count() == 1

    await relation_store.deactivate_by_source(source.entity_id)

    assert len(await relation_store.get_outgoing(source.entity_id)) == 0
    assert len(await relation_store.get_all_active()) == 0


@pytest.mark.asyncio
async def test_entity_upsert_does_not_break_existing_relations(storage_bundle):
    entity_db = storage_bundle["entities"]
    relation_store = storage_bundle["relations"]

    source = CodeEntity(
        entity_id="ent_source_fk",
        name="caller",
        qualified_name="app.caller",
        entity_type=EntityType.FUNCTION,
        file_path="app.py",
    )
    target = CodeEntity(
        entity_id="ent_target_fk",
        name="callee",
        qualified_name="app.callee",
        entity_type=EntityType.FUNCTION,
        file_path="app.py",
    )

    await entity_db.upsert(source)
    await entity_db.upsert(target)
    await relation_store.put(
        Relation(
            source=source.entity_id,
            target=target.entity_id,
            relation_type=RelationType.CALLS,
            provenance=Provenance.AST_DERIVED,
        )
    )

    updated_source = source.model_copy(update={"cached_description": "updated"})
    await entity_db.upsert(updated_source)

    loaded = await entity_db.get(source.entity_id)
    assert loaded is not None
    assert loaded.cached_description == "updated"
    assert len(await relation_store.get_between(source.entity_id, target.entity_id)) == 1
