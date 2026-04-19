import asyncio
import hashlib
import json
from typing import Optional

import pytest
import pytest_asyncio

from tailevents.coding import CodingTaskService
from tailevents.coding.capability.explain import ExplanationCapability
from tailevents.coding.capability.graph import GraphCapability
from tailevents.coding.capability.policy import CapabilityPolicy
from tailevents.coding.capability.registry import CapabilityRegistry
from tailevents.coding.context.adapter import TaileventsContextAdapter
from tailevents.coding.context.model import CodingContextBundle, ObservedFileView
from tailevents.coding.exceptions import CodingTaskValidationError
from tailevents.coding.runtime.applier import ApplyCoordinator, MAX_EVENT_WRITE_RETRIES
from tailevents.coding.runtime.events import RuntimeEventSink
from tailevents.coding.runtime.executor import CodeAttemptExecutor
from tailevents.coding.runtime.prompt import CodingPromptBuilder, SYSTEM_PROMPT
from tailevents.coding.runtime.session import TaskRuntimeSession
from tailevents.coding.runtime.verifier import DraftVerifier
from tailevents.models.profile import CodingCapabilitiesResponse
from tailevents.models.task import (
    AppliedEventRecord,
    CodingTaskAppliedRequest,
    CodingTaskCreateRequest,
    CodingTaskRecord,
    EditableFileReference,
    TaskStepEvent,
    VerifiedFileDraft,
    new_task_id,
)
from tailevents.storage import (
    SQLiteCodingTaskStore,
    SQLiteConnectionManager,
    SQLiteTaskStepStore,
    initialize_db,
)


class FakeLLMClient:
    def __init__(self, outputs: list[str]):
        self._outputs = list(outputs)

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        raise AssertionError("generate() is not used in these tests")

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ):
        if not self._outputs:
            raise AssertionError("No more fake LLM outputs were configured")
        yield self._outputs.pop(0)


class FakeProfileRegistry:
    def __init__(self, payload: dict[str, dict[str, object]]):
        self._payload = payload

    def get_capabilities(self) -> CodingCapabilitiesResponse:
        return CodingCapabilitiesResponse.model_validate(self._payload)


class FailingIngestionPipeline:
    async def ingest(self, raw_event):
        raise RuntimeError("ingest failed")


@pytest_asyncio.fixture
async def runtime_env():
    database = SQLiteConnectionManager(":memory:")
    await initialize_db(database)
    task_store = SQLiteCodingTaskStore(database)
    step_store = SQLiteTaskStepStore(database)
    yield {
        "database": database,
        "task_store": task_store,
        "step_store": step_store,
    }
    await database.close()


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _build_request() -> CodingTaskCreateRequest:
    return CodingTaskCreateRequest(
        target_file_path="pkg/demo.py",
        target_file_version=3,
        user_prompt="Change the return value to 1",
        context_files=["pkg/context.py"],
        editable_files=[
            EditableFileReference(file_path="pkg/helper.py", document_version=2),
        ],
    )


def _build_session(
    request: Optional[CodingTaskCreateRequest] = None,
    llm_client: Optional[FakeLLMClient] = None,
) -> TaskRuntimeSession:
    resolved_request = request or _build_request()
    task_id = new_task_id()
    record = CodingTaskRecord(
        task_id=task_id,
        target_file_path=resolved_request.target_file_path,
        user_prompt=resolved_request.user_prompt,
        context_files=list(resolved_request.context_files),
        editable_files=[item.file_path for item in resolved_request.editable_files],
    )
    editable_paths = {
        resolved_request.target_file_path,
        *[item.file_path for item in resolved_request.editable_files],
    }
    readonly_paths = set(resolved_request.context_files)
    expected_versions = {resolved_request.target_file_path: resolved_request.target_file_version}
    for editable in resolved_request.editable_files:
        expected_versions[editable.file_path] = editable.document_version
    return TaskRuntimeSession(
        task_id=task_id,
        request=resolved_request,
        record=record,
        llm_client=llm_client or FakeLLMClient([]),
        editable_paths=editable_paths,
        readonly_paths=readonly_paths,
        allowed_files=editable_paths | readonly_paths,
        expected_versions=expected_versions,
    )


async def _record_step(
    session: TaskRuntimeSession,
    event: TaskStepEvent,
) -> None:
    await session.event_sink.emit("step", event.model_dump(mode="json"))


async def _capture_model_output(
    session: TaskRuntimeSession,
    attempt_number: int,
    raw_output: str,
) -> None:
    session.model_output_text = f"attempt {attempt_number}: {raw_output}"


async def _emit_model_delta(session: TaskRuntimeSession, text: str) -> None:
    await session.event_sink.emit("model_delta", {"text": text})


async def _request_same_view(
    session: TaskRuntimeSession,
    file_path: str,
    intent: str,
) -> ObservedFileView:
    del session, intent
    content = "def value():\n    return 0\n"
    version = 3 if file_path == "pkg/demo.py" else 2
    return ObservedFileView(
        file_path=file_path,
        content=content,
        content_hash=_hash_content(content),
        document_version=version,
    )


@pytest.mark.asyncio
async def test_runtime_event_sink_streams_buffered_and_future_events():
    sink = RuntimeEventSink()
    await sink.emit("status", {"status": "running"})

    async def consume() -> list[tuple[str, dict[str, object]]]:
        items: list[tuple[str, dict[str, object]]] = []
        async for event, payload in sink.stream():
            items.append((event, payload))
        return items

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await sink.emit("done", {})
    await sink.mark_done()

    assert await consumer == [
        ("status", {"status": "running"}),
        ("done", {}),
    ]


@pytest.mark.asyncio
async def test_runtime_event_sink_stops_after_mark_done():
    sink = RuntimeEventSink()
    await sink.mark_done()
    items = [item async for item in sink.stream()]
    assert items == []


def test_capability_policy_filters_requested_lanes():
    policy = CapabilityPolicy(
        FakeProfileRegistry(
            {
                "repo_observe": {"available": True},
                "multi_file": {"available": False, "reason": "disabled"},
                "mcp": {"available": False, "reason": "disabled"},
                "skills": {"available": True},
            }
        )
    )

    allowed = policy.resolve_requested_lanes(
        ["repo_observe", "multi_file", "skills"]
    )

    assert allowed == {"repo_observe", "skills"}


def test_capability_policy_defaults_without_registry():
    policy = CapabilityPolicy()
    allowed = policy.resolve_requested_lanes(["repo_observe", "mcp"])
    assert allowed == {"repo_observe"}


def test_capability_registry_tracks_disabled_capability():
    registry = CapabilityRegistry()
    registry.register("enabled", object(), enabled=True)
    registry.register("disabled", object(), enabled=False)

    assert registry.is_enabled("enabled") is True
    assert registry.is_enabled("disabled") is False
    with pytest.raises(ValueError):
        registry.require_enabled("disabled")


def test_capability_registry_returns_sorted_names():
    registry = CapabilityRegistry()
    registry.register("graph", object(), enabled=False)
    registry.register("code", object(), enabled=True)
    registry.register("explain", object(), enabled=False)

    assert registry.names() == ["code", "explain", "graph"]


def test_coding_prompt_builder_preserves_prompt_shape():
    builder = CodingPromptBuilder()
    request = _build_request()
    bundle = CodingContextBundle(
        editable_views={
            "pkg/demo.py": ObservedFileView(
                file_path="pkg/demo.py",
                content="def value():\n    return 0\n",
                content_hash="hash-demo",
                document_version=3,
            ),
            "pkg/helper.py": ObservedFileView(
                file_path="pkg/helper.py",
                content="def helper():\n    return 0\n",
                content_hash="hash-helper",
                document_version=2,
            ),
        },
        readonly_views=[
            ObservedFileView(
                file_path="pkg/context.py",
                content="VALUE = 1\n",
                content_hash="hash-context",
                document_version=1,
            )
        ],
    )

    user_prompt = builder.build_user_prompt(request, bundle, "bad edit")

    assert builder.build_system_prompt() == SYSTEM_PROMPT
    assert "Task goal:\nChange the return value to 1" in user_prompt
    assert "Primary target file:\npkg/demo.py" in user_prompt
    assert "<editable_file path=\"pkg/demo.py\">" in user_prompt
    assert "<context_file path=\"pkg/context.py\">" in user_prompt
    assert "Previous failure to fix:\nbad edit" in user_prompt


@pytest.mark.asyncio
async def test_context_adapter_builds_file_only_bundle():
    session = _build_session()
    adapter = TaileventsContextAdapter()

    async def request_view(
        runtime_session: TaskRuntimeSession,
        file_path: str,
        intent: str,
    ) -> ObservedFileView:
        del runtime_session, intent
        if file_path == "pkg/context.py":
            content = "VALUE = 1\n"
            version = 1
        elif file_path == "pkg/helper.py":
            content = "def helper():\n    return 0\n"
            version = 2
        else:
            content = "def value():\n    return 0\n"
            version = 3
        return ObservedFileView(
            file_path=file_path,
            content=content,
            content_hash=_hash_content(content),
            document_version=version,
        )

    bundle = await adapter.build_bundle(
        session=session,
        request_view=request_view,
        validate_expected_version=lambda file_path, observed, expected: None,
    )

    assert list(bundle.editable_views) == ["pkg/demo.py", "pkg/helper.py"]
    assert [item.file_path for item in bundle.readonly_views] == ["pkg/context.py"]
    assert bundle.entity_refs == []
    assert bundle.relation_context == []
    assert bundle.external_docs == []
    assert bundle.impact_paths == []
    assert bundle.explanation_evidence == []


@pytest.mark.asyncio
async def test_context_adapter_rebuild_for_retry_rejects_drift():
    session = _build_session()
    adapter = TaileventsContextAdapter()
    seen: dict[str, int] = {}

    async def request_view(
        runtime_session: TaskRuntimeSession,
        file_path: str,
        intent: str,
    ) -> ObservedFileView:
        del runtime_session, intent
        seen[file_path] = seen.get(file_path, 0) + 1
        if file_path == "pkg/context.py":
            content = "VALUE = 1\n"
            version = 1
        elif file_path == "pkg/helper.py":
            content = "def helper():\n    return 0\n"
            version = 2
        else:
            content = (
                "def value():\n    return 0\n"
                if seen[file_path] == 1
                else "def value():\n    return 2\n"
            )
            version = 3
        return ObservedFileView(
            file_path=file_path,
            content=content,
            content_hash=_hash_content(content),
            document_version=version,
        )

    initial_bundle = await adapter.build_bundle(
        session=session,
        request_view=request_view,
        validate_expected_version=lambda file_path, observed, expected: None,
    )

    with pytest.raises(CodingTaskValidationError, match="content drifted"):
        await adapter.rebuild_bundle_for_retry(
            session=session,
            request_view=request_view,
            validate_expected_version=lambda file_path, observed, expected: None,
            initial_hashes={
                file_path: item.content_hash
                for file_path, item in initial_bundle.editable_views.items()
            },
        )


@pytest.mark.asyncio
async def test_explanation_capability_requires_engine():
    capability = ExplanationCapability()
    with pytest.raises(ValueError, match="not configured"):
        await capability.explain_entity("entity_1")


@pytest.mark.asyncio
async def test_graph_capability_requires_service():
    capability = GraphCapability()
    with pytest.raises(ValueError, match="not configured"):
        await capability.get_subgraph("entity_1")


@pytest.mark.asyncio
async def test_code_attempt_executor_injects_default_file_path():
    request = CodingTaskCreateRequest(
        target_file_path="pkg/demo.py",
        target_file_version=3,
        user_prompt="Change the return value to 1",
        context_files=[],
    )
    session = _build_session(
        request=request,
        llm_client=FakeLLMClient(
            [
                json.dumps(
                    {
                        "edits": [
                            {
                                "old_text": "    return 0\n",
                                "new_text": "    return 1\n",
                            }
                        ],
                        "intent": "Change the return value to 1",
                        "reasoning": "A minimal local edit is enough.",
                    }
                )
            ]
        ),
    )
    bundle = CodingContextBundle(
        editable_views={
            "pkg/demo.py": ObservedFileView(
                file_path="pkg/demo.py",
                content="def value():\n    return 0\n",
                content_hash=_hash_content("def value():\n    return 0\n"),
                document_version=3,
            )
        },
        readonly_views=[],
    )
    executor = CodeAttemptExecutor(CodingPromptBuilder())

    outcome = await executor.execute(
        session=session,
        context_bundle=bundle,
        failure_hint=None,
        attempt_metadata={"attempt_number": 1},
        record_step=_record_step,
        capture_model_output=_capture_model_output,
        emit_model_delta=_emit_model_delta,
    )

    assert outcome.plan.edits[0].file_path == "pkg/demo.py"
    assert outcome.draft_contents["pkg/demo.py"] == "def value():\n    return 1\n"


@pytest.mark.asyncio
async def test_code_attempt_executor_rejects_edit_outside_editable_set():
    request = CodingTaskCreateRequest(
        target_file_path="pkg/demo.py",
        target_file_version=3,
        user_prompt="Change the return value to 1",
        context_files=[],
    )
    session = _build_session(
        request=request,
        llm_client=FakeLLMClient(
            [
                json.dumps(
                    {
                        "edits": [
                            {
                                "file_path": "pkg/other.py",
                                "old_text": "x = 0\n",
                                "new_text": "x = 1\n",
                            }
                        ],
                        "intent": "Change another file",
                        "reasoning": "This should fail.",
                    }
                )
            ]
        ),
    )
    bundle = CodingContextBundle(
        editable_views={
            "pkg/demo.py": ObservedFileView(
                file_path="pkg/demo.py",
                content="def value():\n    return 0\n",
                content_hash=_hash_content("def value():\n    return 0\n"),
                document_version=3,
            )
        },
        readonly_views=[],
    )
    executor = CodeAttemptExecutor(CodingPromptBuilder())

    with pytest.raises(CodingTaskValidationError, match="outside the editable set"):
        await executor.execute(
            session=session,
            context_bundle=bundle,
            failure_hint=None,
            attempt_metadata={"attempt_number": 1},
            record_step=_record_step,
            capture_model_output=_capture_model_output,
            emit_model_delta=_emit_model_delta,
        )


@pytest.mark.asyncio
async def test_draft_verifier_rejects_invalid_python():
    request = CodingTaskCreateRequest(
        target_file_path="pkg/demo.py",
        target_file_version=3,
        user_prompt="Break syntax",
        context_files=[],
    )
    session = _build_session(request=request)
    bundle = CodingContextBundle(
        editable_views={
            "pkg/demo.py": ObservedFileView(
                file_path="pkg/demo.py",
                content="def value():\n    return 0\n",
                content_hash=_hash_content("def value():\n    return 0\n"),
                document_version=3,
            )
        },
        readonly_views=[],
    )
    verifier = DraftVerifier()

    with pytest.raises(CodingTaskValidationError, match="Draft is not valid Python"):
        await verifier.verify(
            session=session,
            context_bundle=bundle,
            draft_contents={"pkg/demo.py": "def value():\n    return (\n"},
            request_view=_request_same_view,
            record_step=_record_step,
        )


@pytest.mark.asyncio
async def test_apply_coordinator_mark_applied_rejects_hash_mismatch(runtime_env):
    coordinator = ApplyCoordinator(
        task_store=runtime_env["task_store"],
        step_store=runtime_env["step_store"],
    )
    record = CodingTaskRecord(
        task_id="task_apply_bad_hash",
        target_file_path="pkg/demo.py",
        user_prompt="Change the return value to 1",
        status="ready_to_apply",
        verified_files=[
            VerifiedFileDraft(
                file_path="pkg/demo.py",
                content="def value():\n    return 1\n",
                content_hash=_hash_content("def value():\n    return 1\n"),
                original_content_hash=_hash_content("def value():\n    return 0\n"),
                original_document_version=3,
            )
        ],
    )
    await runtime_env["task_store"].put(record)

    with pytest.raises(CodingTaskValidationError, match="content hash did not match"):
        await coordinator.mark_applied(
            "task_apply_bad_hash",
            CodingTaskAppliedRequest(
                applied_files=[
                    {
                        "file_path": "pkg/demo.py",
                        "content_hash": "bad-hash",
                    }
                ]
            ),
        )


@pytest.mark.asyncio
async def test_apply_coordinator_retry_event_writes_marks_applied_without_events_after_limit(
    runtime_env,
):
    coordinator = ApplyCoordinator(
        task_store=runtime_env["task_store"],
        step_store=runtime_env["step_store"],
        ingestion_pipeline=FailingIngestionPipeline(),
    )
    record = CodingTaskRecord(
        task_id="task_retry_events",
        target_file_path="pkg/demo.py",
        user_prompt="Change the return value to 1",
        status="applied_event_pending",
        verified_files=[
            VerifiedFileDraft(
                file_path="pkg/demo.py",
                content="def value():\n    return 1\n",
                content_hash=_hash_content("def value():\n    return 1\n"),
                original_content_hash=_hash_content("def value():\n    return 0\n"),
                original_document_version=3,
            )
        ],
        applied_events=[
            AppliedEventRecord(
                file_path="pkg/demo.py",
                status="failed",
                last_error="ingest failed",
            )
        ],
        applied_event_retry_count=MAX_EVENT_WRITE_RETRIES - 1,
    )
    await runtime_env["task_store"].put(record)

    await coordinator.retry_event_writes("task_retry_events")

    updated = await runtime_env["task_store"].get("task_retry_events")
    assert updated is not None
    assert updated.status == "applied_without_events"
    assert updated.applied_event_retry_count == MAX_EVENT_WRITE_RETRIES
    assert updated.applied_events[0].status == "failed"
    assert updated.applied_events[0].last_error == "ingest failed"


@pytest.mark.asyncio
async def test_coding_task_service_registers_internal_capabilities(runtime_env):
    service = CodingTaskService(
        llm_client=FakeLLMClient([]),
        task_store=runtime_env["task_store"],
        step_store=runtime_env["step_store"],
    )

    assert service._capability_registry.names() == [
        "code",
        "explain",
        "graph",
        "graphrag",
    ]
    assert service._capability_registry.is_enabled("code") is True
    assert service._capability_registry.is_enabled("explain") is False
    assert service._capability_registry.is_enabled("graph") is False
    assert service._capability_registry.is_enabled("graphrag") is False
