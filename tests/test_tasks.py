import pytest

from tailevents.models.task import CodingTaskRequest
from tailevents.tasks import CodingTaskParseError, CodingTaskService


class FakeStreamingLLMClient:
    def __init__(self, chunks: list[str]):
        self._chunks = chunks

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        return "".join(self._chunks)

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_coding_task_service_streams_delta_then_result():
    service = CodingTaskService(
        FakeStreamingLLMClient(
            [
                '{"edits":[{"old_text":"print(0)\\n","new_text":"print(1)\\n"}],',
                '"intent":"add print","reasoning":null,"action_type":"modify"}',
            ]
        )
    )
    request = CodingTaskRequest(
        file_path="demo.py",
        file_content="print(0)\n",
        user_prompt="change the output to 1",
    )

    events = []
    async for event in service.run_stream(request):
        events.append(event)

    assert [item[0] for item in events] == ["delta", "delta", "result"]
    assert events[-1][1]["action_type"] == "modify"
    assert events[-1][1]["intent"] == "add print"
    assert events[-1][1]["updated_file_content"] == "print(1)\n"
    assert events[-1][1]["edits"] == [{"old_text": "print(0)\n", "new_text": "print(1)\n"}]


@pytest.mark.asyncio
async def test_coding_task_service_applies_multiple_edits_in_order():
    service = CodingTaskService(
        FakeStreamingLLMClient(
            [
                '{"edits":[',
                '{"old_text":"alpha = 1\\n","new_text":"alpha = 10\\n"},',
                '{"old_text":"beta = 2\\n","new_text":"beta = 20\\n"}],',
                '"intent":"update both values","reasoning":null,"action_type":"modify"}',
            ]
        )
    )
    request = CodingTaskRequest(
        file_path="demo.py",
        file_content="alpha = 1\nbeta = 2\n",
        user_prompt="update both values",
    )

    events = []
    async for event in service.run_stream(request):
        events.append(event)

    assert events[-1][1]["updated_file_content"] == "alpha = 10\nbeta = 20\n"


@pytest.mark.asyncio
async def test_coding_task_service_rejects_invalid_json():
    service = CodingTaskService(FakeStreamingLLMClient(["not json at all"]))
    request = CodingTaskRequest(
        file_path="demo.py",
        file_content="print(0)\n",
        user_prompt="change the output to 1",
    )

    with pytest.raises(CodingTaskParseError):
        async for _event in service.run_stream(request):
            pass


@pytest.mark.asyncio
async def test_coding_task_service_rejects_invalid_action_type():
    service = CodingTaskService(
        FakeStreamingLLMClient(
            [
                '{"edits":[{"old_text":"print(0)\\n","new_text":"print(1)\\n"}],',
                '"intent":"add print","reasoning":null,"action_type":"rename"}',
            ]
        )
    )
    request = CodingTaskRequest(
        file_path="demo.py",
        file_content="print(0)\n",
        user_prompt="change the output to 1",
    )

    with pytest.raises(CodingTaskParseError):
        async for _event in service.run_stream(request):
            pass


@pytest.mark.asyncio
async def test_coding_task_service_rejects_empty_intent():
    service = CodingTaskService(
        FakeStreamingLLMClient(
            [
                '{"edits":[{"old_text":"print(0)\\n","new_text":"print(1)\\n"}],',
                '"intent":"   ","reasoning":null,"action_type":"modify"}',
            ]
        )
    )
    request = CodingTaskRequest(
        file_path="demo.py",
        file_content="print(0)\n",
        user_prompt="change the output to 1",
    )

    with pytest.raises(CodingTaskParseError):
        async for _event in service.run_stream(request):
            pass


@pytest.mark.asyncio
async def test_coding_task_service_rejects_unmatched_old_text():
    service = CodingTaskService(
        FakeStreamingLLMClient(
            [
                '{"edits":[{"old_text":"print(999)\\n","new_text":"print(1)\\n"}],',
                '"intent":"add print","reasoning":null,"action_type":"modify"}',
            ]
        )
    )
    request = CodingTaskRequest(
        file_path="demo.py",
        file_content="print(0)\n",
        user_prompt="change the output to 1",
    )

    with pytest.raises(CodingTaskParseError, match="did not match the file exactly"):
        async for _event in service.run_stream(request):
            pass


@pytest.mark.asyncio
async def test_coding_task_service_rejects_old_text_with_multiple_matches():
    service = CodingTaskService(
        FakeStreamingLLMClient(
            [
                '{"edits":[{"old_text":"x = 1\\n","new_text":"x = 2\\n"}],',
                '"intent":"bump value","reasoning":null,"action_type":"modify"}',
            ]
        )
    )
    request = CodingTaskRequest(
        file_path="demo.py",
        file_content="x = 1\nx = 1\n",
        user_prompt="change the value",
    )

    with pytest.raises(CodingTaskParseError, match="matched multiple locations"):
        async for _event in service.run_stream(request):
            pass


@pytest.mark.asyncio
async def test_coding_task_service_rejects_unchanged_content_after_edits():
    service = CodingTaskService(
        FakeStreamingLLMClient(
            [
                '{"edits":[{"old_text":"print(0)\\n","new_text":"print(0)\\n"}],',
                '"intent":"no-op","reasoning":null,"action_type":"modify"}',
            ]
        )
    )
    request = CodingTaskRequest(
        file_path="demo.py",
        file_content="print(0)\n",
        user_prompt="change the output to 1",
    )

    with pytest.raises(CodingTaskParseError, match="did not change the file content"):
        async for _event in service.run_stream(request):
            pass


@pytest.mark.asyncio
async def test_coding_task_service_rejects_invalid_python_output():
    service = CodingTaskService(
        FakeStreamingLLMClient(
            [
                '{"edits":[{"old_text":"value = 1\\n",',
                '"new_text":"if True:\\nvalue = 1\\n"}],',
                '"intent":"rewrite helper","reasoning":null,"action_type":"modify"}',
            ]
        )
    )
    request = CodingTaskRequest(
        file_path="demo.py",
        file_content="value = 1\n",
        user_prompt="introduce a tiny refactor",
    )

    with pytest.raises(CodingTaskParseError, match="not valid Python"):
        async for _event in service.run_stream(request):
            pass
