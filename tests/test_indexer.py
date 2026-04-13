from datetime import datetime

import pytest
import pytest_asyncio

from tailevents.cache import ExplanationCache
from tailevents.indexer import ASTAnalyzer, DiffParser, EntityExtractor, Indexer, RenameTracker
from tailevents.models.enums import ActionType, EntityType
from tailevents.models.event import TailEvent
from tailevents.storage import SQLiteConnectionManager, SQLiteEntityDB, SQLiteRelationStore, initialize_db


@pytest_asyncio.fixture
async def indexer_bundle():
    database = SQLiteConnectionManager(":memory:")
    await initialize_db(database)

    bundle = {
        "database": database,
        "entity_db": SQLiteEntityDB(database),
        "relation_store": SQLiteRelationStore(database),
        "cache": ExplanationCache(database),
    }
    bundle["indexer"] = Indexer(
        entity_db=bundle["entity_db"],
        relation_store=bundle["relation_store"],
        cache=bundle["cache"],
        rename_similarity_threshold=0.8,
    )

    yield bundle

    await database.close()


@pytest.mark.asyncio
async def test_explanation_cache_ttl_and_invalidation(indexer_bundle):
    cache = indexer_bundle["cache"]

    await cache.put("explanation:ent_1:summary", "cached-summary")
    await cache.put("explanation:ent_1:detailed", "cached-detailed")
    await cache.put("explanation:expired:summary", "expired", ttl=-1)

    assert await cache.get("explanation:ent_1:summary") == "cached-summary"
    assert await cache.get("explanation:expired:summary") is None

    await cache.invalidate("explanation:ent_1:summary")
    assert await cache.get("explanation:ent_1:summary") is None

    await cache.invalidate_prefix("explanation:ent_1")
    assert await cache.get("explanation:ent_1:detailed") is None


def test_ast_extraction_handles_functions_classes_methods_and_nested():
    analyzer = ASTAnalyzer()
    source = """
def helper(value: int) -> int:
    \"\"\"Top level helper.\"\"\"
    return value + 1

class Service:
    def run(self):
        def inner():
            return helper(1)
        return inner()

    class Nested:
        def ping(self) -> str:
            return "pong"
"""

    entities = analyzer.extract_entities(source, "service.py")
    by_qname = {entity["qualified_name"]: entity for entity in entities}

    assert "helper" in by_qname
    assert "Service" in by_qname
    assert "Service.run" in by_qname
    assert "Service.run.inner" in by_qname
    assert "Service.Nested" in by_qname
    assert "Service.Nested.ping" in by_qname

    assert by_qname["helper"]["entity_type"] == EntityType.FUNCTION.value
    assert by_qname["Service"]["entity_type"] == EntityType.CLASS.value
    assert by_qname["Service.run"]["entity_type"] == EntityType.METHOD.value
    assert by_qname["helper"]["return_type"] == "int"
    assert by_qname["helper"]["docstring"] == "Top level helper."
    assert by_qname["helper"]["body_hash"]
    assert by_qname["Service.Nested.ping"]["line_range"] is not None


def test_relation_extraction_covers_calls_imports_and_inheritance():
    analyzer = ASTAnalyzer()
    source = """
class Base:
    pass

def helper():
    return 1

class Child(Base):
    def local(self):
        return helper()

    def run(self):
        import helper
        self.local()
        return helper()
"""

    known_entities = {
        "Base": "ent_base",
        "helper": "ent_helper",
        "Child": "ent_child",
        "Child.local": "ent_local",
        "Child.run": "ent_run",
    }

    relations = analyzer.extract_relations(source, "child.py", known_entities)
    relation_set = {
        (item["source_qname"], item["target_qname"], item["relation_type"])
        for item in relations
    }

    assert ("Child", "Base", "inherits") in relation_set
    assert ("Child.local", "helper", "calls") in relation_set
    assert ("Child.run", "Child.local", "calls") in relation_set
    assert ("Child.run", "helper", "calls") in relation_set
    assert ("Child.run", "helper", "imports") in relation_set

    imports = analyzer.extract_imports(source)
    assert any(item["qualified_name"] == "helper" for item in imports)


@pytest.mark.asyncio
async def test_rename_detection_matches_same_body(indexer_bundle):
    analyzer = ASTAnalyzer()
    entity_extractor = EntityExtractor(analyzer, indexer_bundle["entity_db"])
    rename_tracker = RenameTracker(similarity_threshold=0.8)

    original_event = TailEvent(
        action_type=ActionType.CREATE,
        file_path="module.py",
        code_snapshot="def old_name():\n    return 1\n",
        intent="create old function",
        timestamp=datetime.utcnow(),
    )

    original_inspection = await entity_extractor.inspect(
        original_event.code_snapshot, original_event.file_path
    )
    original_result = await entity_extractor.sync(
        event=original_event,
        inspection=original_inspection,
        rename_matches=[],
    )

    renamed_source = "def new_name():\n    return 1\n"
    renamed_inspection = await entity_extractor.inspect(renamed_source, "module.py")
    rename_matches = rename_tracker.detect_renames(
        disappeared=renamed_inspection.disappeared_entities,
        appeared=renamed_inspection.appeared_entities,
    )

    assert len(rename_matches) == 1
    assert rename_matches[0]["old_entity_id"] == original_result.created_entity_ids[0]
    assert rename_matches[0]["new_qualified_name"] == "new_name"


def test_diff_parser_handles_unified_diff_and_full_source():
    parser = DiffParser()
    diff = """--- a/sample.py
+++ b/sample.py
@@ -1,2 +1,3 @@
 def foo():
-    return 1
+    value = 1
+    return value
"""

    changes = parser.parse(diff, "sample.py")
    assert len(changes) == 1
    assert changes[0]["file_path"] == "sample.py"
    assert changes[0]["added_lines"] == [2, 3]
    assert changes[0]["removed_lines"] == [2]
    assert changes[0]["modified_lines"] == [2, 3]
    assert "value = 1" in changes[0]["source"]

    full_source = "def bar():\n    return 2\n"
    full_changes = parser.parse(full_source, "plain.py")
    assert full_changes[0]["is_diff"] is False
    assert full_changes[0]["source"] == full_source


@pytest.mark.asyncio
async def test_pending_queue_on_invalid_syntax(indexer_bundle):
    indexer = indexer_bundle["indexer"]

    broken_event = TailEvent(
        action_type=ActionType.MODIFY,
        file_path="broken.py",
        code_snapshot="def broken(:\n    pass\n",
        intent="break syntax",
        timestamp=datetime.utcnow(),
    )

    result = await indexer.process_event(broken_event)

    assert result.pending is True
    assert len(indexer.pending_queue.get_pending()) == 1
    assert indexer.pending_queue.get_pending()[0].event_id == broken_event.event_id

    indexer.pending_queue.remove(broken_event.event_id)
    assert indexer.pending_queue.get_pending() == []
