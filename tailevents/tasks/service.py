"""Minimal coding task service for the B0 vertical slice."""

import json
import re
from typing import AsyncIterator

from tailevents.models.protocols import LLMClientProtocol
from tailevents.models.task import CodingTaskRequest, CodingTaskResult
from tailevents.tasks.exceptions import CodingTaskParseError


SYSTEM_PROMPT = """
你是一个代码修改生成器。

你会收到一个 Python 文件路径、当前完整文件内容和用户的修改请求。
你的任务是返回一个且仅一个 JSON 对象，不允许输出任何 JSON 之外的说明文字。

JSON 必须只包含以下字段：
- updated_file_content: 修改后的完整文件内容
- intent: 一句简短说明这次修改的意图
- reasoning: 对修改原因的简短说明；如果没有可填 null
- action_type: 只能是 "create" 或 "modify"

要求：
1. 输出必须是合法 JSON。
2. 不要使用 Markdown 代码块。
3. 不要附加解释性前后文。
4. 如果用户请求不需要修改文件，也必须返回合法 JSON，但 updated_file_content 仍应是完整文件内容。
""".strip()

USER_PROMPT_TEMPLATE = """
file_path:
{file_path}

current_file_content:
{file_content}

user_prompt:
{user_prompt}
""".strip()

DEFAULT_MAX_TOKENS = 4_000
DEFAULT_TEMPERATURE = 0.1
CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class CodingTaskService:
    """Generate a minimal coding task result as streamed model output."""

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ):
        self._llm_client = llm_client
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def run_stream(
        self,
        request: CodingTaskRequest,
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        chunks: list[str] = []
        async for text in self._llm_client.stream_generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=self._build_user_prompt(request),
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        ):
            if not text:
                continue
            chunks.append(text)
            yield ("delta", {"text": text})

        result = self._parse_result("".join(chunks))
        yield ("result", result.model_dump())

    def _build_user_prompt(self, request: CodingTaskRequest) -> str:
        return USER_PROMPT_TEMPLATE.format(
            file_path=request.file_path,
            file_content=request.file_content,
            user_prompt=request.user_prompt,
        )

    def _parse_result(self, raw_output: str) -> CodingTaskResult:
        normalized = raw_output.strip()
        if not normalized:
            raise CodingTaskParseError("Model returned an empty response")

        stripped = CODE_FENCE_PATTERN.sub("", normalized).strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise CodingTaskParseError("Model output did not contain a JSON object")

        payload = stripped[start : end + 1]
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as error:
            raise CodingTaskParseError(f"Model output was not valid JSON: {error.msg}") from error

        try:
            result = CodingTaskResult.model_validate(parsed)
        except Exception as error:
            raise CodingTaskParseError(f"Model output failed validation: {error}") from error

        if not result.updated_file_content.strip():
            raise CodingTaskParseError("updated_file_content must not be empty")
        if not result.intent.strip():
            raise CodingTaskParseError("intent must not be empty")
        return result


__all__ = ["CodingTaskService"]
