import json
from typing import Any, Callable, Optional

import httpx
import pytest
import pytest_asyncio

import tailevents.explanation.llm_client as llm_module
from tailevents.coding import CodingTaskService
from tailevents.explanation.llm_client import (
    ClaudeLLMClient,
    OllamaLLMClient,
    OpenRouterLLMClient,
)
from tailevents.models.task import (
    CodingTaskCreateRequest,
    CodingTaskToolResultRequest,
    ToolCallPayload,
)
from tailevents.storage import SQLiteConnectionManager, SQLiteTaskStepStore, initialize_db


class FakeLLMClient:
    def __init__(
        self,
        outputs: list[str],
        stream_chunks: Optional[list[list[str]]] = None,
    ):
        self._outputs = outputs
        self._stream_chunks = stream_chunks or []
        self.prompts: list[tuple[str, str]] = []

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        self.prompts.append((system_prompt, user_prompt))
        if not self._outputs:
            raise AssertionError("No more fake LLM outputs were configured")
        return self._outputs.pop(0)

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ):
        self.prompts.append((system_prompt, user_prompt))
        if not self._outputs:
            raise AssertionError("No more fake LLM outputs were configured")
        output = self._outputs.pop(0)
        chunks = self._stream_chunks.pop(0) if self._stream_chunks else [output]
        for chunk in chunks:
            yield chunk


class FakeStreamingResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self._status_code = status_code
        self._request = httpx.Request("POST", "https://example.com")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self) -> None:
        if self._status_code >= 400:
            response = httpx.Response(self._status_code, request=self._request)
            raise httpx.HTTPStatusError(
                f"HTTP {self._status_code}",
                request=self._request,
                response=response,
            )

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeStreamingAsyncClient:
    def __init__(
        self,
        lines: list[str],
        recorder: Optional[dict[str, Any]] = None,
    ):
        self._lines = list(lines)
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str, **kwargs):
        if self._recorder is not None:
            self._recorder["method"] = method
            self._recorder["url"] = url
            self._recorder["json"] = kwargs.get("json")
            self._recorder["headers"] = kwargs.get("headers")
        return FakeStreamingResponse(self._lines)


@pytest_asyncio.fixture
async def coding_env():
    database = SQLiteConnectionManager(":memory:")
    await initialize_db(database)
    step_store = SQLiteTaskStepStore(database)
    yield {
        "database": database,
        "step_store": step_store,
    }
    await database.close()


@pytest.mark.asyncio
async def test_coding_task_service_runs_view_edit_verify_and_emits_model_delta(coding_env):
    payload = json.dumps(
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
    llm_client = FakeLLMClient(
        [payload],
        stream_chunks=[[payload[:25], payload[25:]]],
    )
    service = CodingTaskService(
        llm_client=llm_client,
        step_store=coding_env["step_store"],
    )
    request = CodingTaskCreateRequest(
        target_file_path="pkg/demo.py",
        target_file_version=3,
        user_prompt="Change the return value to 1",
        context_files=["pkg/context.py"],
    )

    response = await service.create_task(request)
    events = await drive_task_session(
        service,
        response.task_id,
        {
            "pkg/demo.py": {
                "content": "def value():\n    return 0\n",
                "document_version": 3,
            },
            "pkg/context.py": {
                "content": "VALUE = 1\n",
                "document_version": 1,
            },
        },
    )

    result = await service.get_result(response.task_id)
    assert result is not None
    assert result.updated_file_content == "def value():\n    return 1\n"
    assert result.intent == "Change the return value to 1"

    event_names = [event for event, _ in events]
    assert "tool_call" in event_names
    assert "model_delta" in event_names
    assert "result" in event_names
    assert event_names.index("model_delta") < event_names.index("result")
    assert event_names[-1] == "done"

    stored_steps = await coding_env["step_store"].get_by_task(response.task_id)
    succeeded_kinds = {
        step.step_kind
        for step in stored_steps
        if step.status == "succeeded"
    }
    assert succeeded_kinds == {"view", "edit", "verify"}
    assert sum(1 for step in stored_steps if step.step_kind == "view" and step.status == "succeeded") == 2


@pytest.mark.asyncio
async def test_coding_task_service_fails_when_target_drifts_after_retry(coding_env):
    llm_client = FakeLLMClient(
        [
            json.dumps(
                {
                    "edits": [
                        {
                            "old_text": "    return 0\n",
                            "new_text": "    return 0\n",
                        }
                    ],
                    "intent": "Keep the file unchanged",
                    "reasoning": "This intentionally produces a no-op edit.",
                }
            ),
            json.dumps(
                {
                    "edits": [
                        {
                            "old_text": "    return 999\n",
                            "new_text": "    return 1\n",
                        }
                    ],
                    "intent": "Change the return value to 1",
                    "reasoning": "This first attempt intentionally uses a bad match.",
                }
            )
        ]
    )
    service = CodingTaskService(
        llm_client=llm_client,
        step_store=coding_env["step_store"],
    )
    request = CodingTaskCreateRequest(
        target_file_path="pkg/demo.py",
        target_file_version=3,
        user_prompt="Change the return value to 1",
        context_files=[],
    )

    response = await service.create_task(request)
    target_views = [
        {
            "content": "def value():\n    return 0\n",
            "document_version": 3,
        },
        {
            "content": "def value():\n    return 2\n",
            "document_version": 3,
        },
    ]

    events = await drive_task_session(
        service,
        response.task_id,
        tool_handler=lambda tool_call: build_tool_result(
            tool_call,
            **target_views.pop(0),
        ),
    )

    result = await service.get_result(response.task_id)
    assert result is None
    error_messages = [
        payload["message"]
        for event, payload in events
        if event == "error"
    ]
    assert error_messages == ["Target file content drifted during task execution"]
    assert events[-1][0] == "done"

    stored_steps = await coding_env["step_store"].get_by_task(response.task_id)
    assert any(step.step_kind == "edit" and step.status == "failed" for step in stored_steps)
    assert sum(1 for step in stored_steps if step.step_kind == "view" and step.status == "succeeded") == 2


@pytest.mark.asyncio
async def test_coding_task_service_fails_noop_during_edit(coding_env):
    llm_client = FakeLLMClient(
        [
            json.dumps(
                {
                    "edits": [
                        {
                            "old_text": "    return 0\n",
                            "new_text": "    return 0\n",
                        }
                    ],
                    "intent": "Keep the file unchanged",
                    "reasoning": "This intentionally produces a no-op edit.",
                }
            ),
            json.dumps(
                {
                    "edits": [
                        {
                            "old_text": "    return 0\n",
                            "new_text": "    return 0\n",
                        }
                    ],
                    "intent": "Keep the file unchanged",
                    "reasoning": "This intentionally produces a no-op edit.",
                }
            )
        ]
    )
    service = CodingTaskService(
        llm_client=llm_client,
        step_store=coding_env["step_store"],
    )
    request = CodingTaskCreateRequest(
        target_file_path="pkg/demo.py",
        target_file_version=1,
        user_prompt="Do nothing",
        context_files=[],
    )

    response = await service.create_task(request)
    events = await drive_task_session(
        service,
        response.task_id,
        {
            "pkg/demo.py": {
                "content": "def value():\n    return 0\n",
                "document_version": 1,
            },
        },
    )

    error_messages = [
        payload["message"]
        for event, payload in events
        if event == "error"
    ]
    assert error_messages == ["Edit plan did not change the target file"]

    stored_steps = await coding_env["step_store"].get_by_task(response.task_id)
    edit_failed = next(
        step
        for step in stored_steps
        if step.step_kind == "edit" and step.status == "failed"
    )
    assert edit_failed.output_summary == "Edit plan did not change the target file"
    assert not any(step.step_kind == "verify" for step in stored_steps)


@pytest.mark.asyncio
async def test_coding_task_service_reports_verify_reason_for_invalid_python(coding_env):
    llm_client = FakeLLMClient(
        [
            json.dumps(
                {
                    "edits": [
                        {
                            "old_text": "    return 0\n",
                            "new_text": "    return (\n",
                        }
                    ],
                    "intent": "Produce an invalid draft",
                    "reasoning": "This intentionally breaks Python syntax.",
                }
            ),
            json.dumps(
                {
                    "edits": [
                        {
                            "old_text": "    return 0\n",
                            "new_text": "    return (\n",
                        }
                    ],
                    "intent": "Produce an invalid draft",
                    "reasoning": "This intentionally breaks Python syntax.",
                }
            )
        ]
    )
    service = CodingTaskService(
        llm_client=llm_client,
        step_store=coding_env["step_store"],
    )
    request = CodingTaskCreateRequest(
        target_file_path="pkg/demo.py",
        target_file_version=1,
        user_prompt="Break syntax",
        context_files=[],
    )

    response = await service.create_task(request)
    events = await drive_task_session(
        service,
        response.task_id,
        {
            "pkg/demo.py": {
                "content": "def value():\n    return 0\n",
                "document_version": 1,
            },
        },
    )

    error_messages = [
        payload["message"]
        for event, payload in events
        if event == "error"
    ]
    assert error_messages == ["Draft is not valid Python: line 2: '(' was never closed"]

    stored_steps = await coding_env["step_store"].get_by_task(response.task_id)
    verify_failed = next(
        step
        for step in stored_steps
        if step.step_kind == "verify" and step.status == "failed"
    )
    assert verify_failed.output_summary == "Draft is not valid Python: line 2: '(' was never closed"


@pytest.mark.asyncio
async def test_ollama_stream_generate_yields_chunks(monkeypatch):
    recorder: dict[str, Any] = {}
    monkeypatch.setattr(
        llm_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: FakeStreamingAsyncClient(
            [
                '{"response":"hello","done":false}',
                '{"response":" world","done":false}',
                '{"done":true}',
            ],
            recorder,
        ),
    )

    client = OllamaLLMClient(base_url="http://ollama.local", model="qwen3:32b")
    chunks = [
        chunk
        async for chunk in client.stream_generate(
            "system",
            "user",
            max_tokens=32,
        )
    ]

    assert chunks == ["hello", " world"]
    assert recorder["json"]["stream"] is True


@pytest.mark.asyncio
async def test_claude_stream_generate_yields_text_deltas(monkeypatch):
    monkeypatch.setattr(
        llm_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: FakeStreamingAsyncClient(
            [
                "event: content_block_delta",
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}',
                "",
                "event: ping",
                'data: {"type":"ping"}',
                "",
                "event: content_block_delta",
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}',
                "",
            ]
        ),
    )

    client = ClaudeLLMClient(api_key="test", model="claude-sonnet")
    chunks = [
        chunk
        async for chunk in client.stream_generate(
            "system",
            "user",
            max_tokens=32,
        )
    ]

    assert chunks == ["hello", " world"]


@pytest.mark.asyncio
async def test_openrouter_stream_generate_yields_delta_content(monkeypatch):
    recorder: dict[str, Any] = {}
    monkeypatch.setattr(
        llm_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: FakeStreamingAsyncClient(
            [
                'data: {"choices":[{"delta":{"content":"hello"}}]}',
                "",
                'data: {"choices":[{"delta":{"content":" world"}}]}',
                "",
                "data: [DONE]",
                "",
            ],
            recorder,
        ),
    )

    client = OpenRouterLLMClient(
        api_key="test",
        model="openai/gpt-4o-mini",
        base_url="https://openrouter.ai/api/v1",
    )
    chunks = [
        chunk
        async for chunk in client.stream_generate(
            "system",
            "user",
            max_tokens=32,
        )
    ]

    assert chunks == ["hello", " world"]
    assert recorder["json"]["stream"] is True


async def drive_task_session(
    service: CodingTaskService,
    task_id: str,
    file_views: Optional[dict[str, dict[str, object]]] = None,
    tool_handler: Optional[Callable[[ToolCallPayload], CodingTaskToolResultRequest]] = None,
) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []

    async for event, payload in service.stream_events(task_id):
        events.append((event, payload))
        if event != "tool_call":
            continue

        tool_call = ToolCallPayload.model_validate(payload)
        if tool_handler is not None:
            result = tool_handler(tool_call)
        else:
            if file_views is None or tool_call.file_path not in file_views:
                raise AssertionError(f"No tool result configured for {tool_call.file_path}")
            result = build_tool_result(tool_call, **file_views[tool_call.file_path])
        await service.submit_tool_result(task_id, result)

    return events


def build_tool_result(
    tool_call: ToolCallPayload,
    content: str,
    document_version: int | None,
) -> CodingTaskToolResultRequest:
    import hashlib

    return CodingTaskToolResultRequest(
        call_id=tool_call.call_id,
        tool_name="view_file",
        file_path=tool_call.file_path,
        document_version=document_version,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        error=None,
    )
