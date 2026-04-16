"""Minimal coding task service for the B0 vertical slice."""

import ast
import json
import re
from typing import AsyncIterator, Literal, Optional

from pydantic import BaseModel

from tailevents.models.protocols import LLMClientProtocol
from tailevents.models.task import CodingTaskEdit, CodingTaskRequest, CodingTaskResult
from tailevents.tasks.exceptions import CodingTaskParseError


SYSTEM_PROMPT = """
You are a code editing engine for a single-file coding task.

You will receive:
1. a file path
2. the current full file content
3. the user's requested change

Return exactly one JSON object and nothing else.
Do not add markdown fences.
Do not add commentary before or after the JSON object.

The JSON object must contain exactly these fields:
- edits
- intent
- reasoning
- action_type

Field rules:
- edits: an array of exact-match replacements
- intent: one short sentence describing the change
- reasoning: one short sentence or null
- action_type: "create" or "modify"

Each edit object must contain exactly:
- old_text
- new_text

Editing rules:
- old_text must be copied exactly from the current file content, including whitespace.
- old_text must describe only the smallest necessary block to replace.
- new_text must preserve normal source-code formatting, indentation, spaces, and blank lines.
- Return as few edits as possible, usually 1 to 3 edits.
- Do not return the full file content.
- Do not modify unrelated code.
- For Python files, the final code after applying edits must be syntactically valid Python.
""".strip()

USER_PROMPT_TEMPLATE = """
file_path:
{file_path}

current_file_content:
<current_file_content>
{file_content}
</current_file_content>

user_prompt:
<user_prompt>
{user_prompt}
</user_prompt>

Return exact-match replacements only.
""".strip()

DEFAULT_MAX_TOKENS = 4_000
DEFAULT_TEMPERATURE = 0.1
CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class _CodingTaskModelOutput(BaseModel):
    """Validated model output before local edit application."""

    edits: list[CodingTaskEdit]
    intent: str
    reasoning: Optional[str] = None
    action_type: Literal["create", "modify"]


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

        parsed_output = self._parse_model_output("".join(chunks))
        result = self._build_result(request, parsed_output)
        self._validate_result(request, result)
        yield ("result", result.model_dump())

    def _build_user_prompt(self, request: CodingTaskRequest) -> str:
        return USER_PROMPT_TEMPLATE.format(
            file_path=request.file_path,
            file_content=request.file_content,
            user_prompt=request.user_prompt,
        )

    def _parse_model_output(self, raw_output: str) -> _CodingTaskModelOutput:
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
            result = _CodingTaskModelOutput.model_validate(parsed)
        except Exception as error:
            raise CodingTaskParseError(f"Model output failed validation: {error}") from error

        if not result.intent.strip():
            raise CodingTaskParseError("intent must not be empty")
        if not result.edits:
            raise CodingTaskParseError("edits must not be empty")
        return result

    def _build_result(
        self,
        request: CodingTaskRequest,
        parsed_output: _CodingTaskModelOutput,
    ) -> CodingTaskResult:
        updated_file_content = self._apply_edits(request.file_content, parsed_output.edits)
        return CodingTaskResult(
            updated_file_content=updated_file_content,
            edits=parsed_output.edits,
            intent=parsed_output.intent,
            reasoning=parsed_output.reasoning,
            action_type=parsed_output.action_type,
        )

    def _apply_edits(self, original_content: str, edits: list[CodingTaskEdit]) -> str:
        working_content = original_content

        for index, edit in enumerate(edits, start=1):
            if not edit.old_text:
                raise CodingTaskParseError(f"Edit {index} old_text must not be empty")

            matches = working_content.count(edit.old_text)
            if matches == 0:
                raise CodingTaskParseError(
                    f"Edit {index} old_text did not match the file exactly"
                )
            if matches > 1:
                raise CodingTaskParseError(
                    f"Edit {index} old_text matched multiple locations"
                )

            working_content = working_content.replace(edit.old_text, edit.new_text, 1)

        if working_content == original_content:
            raise CodingTaskParseError("Applied edits did not change the file content")

        return working_content

    def _validate_result(
        self,
        request: CodingTaskRequest,
        result: CodingTaskResult,
    ) -> None:
        if request.file_path.lower().endswith(".py"):
            self._validate_python_source(result.updated_file_content)

    def _validate_python_source(self, source: str) -> None:
        try:
            ast.parse(source)
        except SyntaxError as error:
            message = error.msg or "invalid syntax"
            line = error.lineno or "unknown"
            raise CodingTaskParseError(
                f"updated_file_content is not valid Python: line {line}: {message}"
            ) from error


__all__ = ["CodingTaskService"]
