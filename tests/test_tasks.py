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
                '{"updated_file_content":"print(1)\\n",',
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
                '{"updated_file_content":"print(1)\\n",',
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
