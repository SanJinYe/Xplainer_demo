"""Single-attempt edit execution for coding tasks."""

import json
import hashlib
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from tailevents.coding.capability.base import EditPlan
from tailevents.coding.context.model import CodingContextBundle
from tailevents.coding.exceptions import CodingTaskValidationError
from tailevents.coding.runtime.prompt import CodingPromptBuilder
from tailevents.coding.runtime.session import TaskRuntimeSession
from tailevents.models.task import CodingTaskEdit, TaskStepEvent, new_step_id


CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
MAX_SUMMARY_LENGTH = 240


@dataclass(frozen=True)
class EditExecutionOutcome:
    """Completed edit-stage output for a single attempt."""

    plan: EditPlan
    draft_contents: dict[str, str]
    step_id: str


_RecordStep = Callable[[TaskRuntimeSession, TaskStepEvent], Awaitable[None]]
_CaptureModelOutput = Callable[[TaskRuntimeSession, int, str], Awaitable[None]]
_EmitModelDelta = Callable[[TaskRuntimeSession, str], Awaitable[None]]


class CodeAttemptExecutor:
    """Drive the model edit stage for a single coding attempt."""

    def __init__(self, prompt_builder: CodingPromptBuilder) -> None:
        self._prompt_builder = prompt_builder

    async def execute(
        self,
        session: TaskRuntimeSession,
        context_bundle: CodingContextBundle,
        failure_hint: Optional[str],
        attempt_metadata: dict[str, object],
        record_step: _RecordStep,
        capture_model_output: _CaptureModelOutput,
        emit_model_delta: _EmitModelDelta,
    ) -> EditExecutionOutcome:
        step_id = new_step_id()
        attempt_number = int(attempt_metadata.get("attempt_number", 1))
        editable_views = context_bundle.editable_views
        context_views = context_bundle.readonly_views
        primary_target_path = session.resolved_primary_target_path or next(
            iter(editable_views)
        )
        await record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="edit",
                status="started",
                file_path=primary_target_path,
                content_hash=editable_views[primary_target_path].content_hash,
                intent="Generate local edits for the declared editable files",
                input_summary=self._truncate(
                    f"editable={len(editable_views)}, contexts={len(context_views)}"
                ),
            ),
        )

        raw_output = ""
        captured_output = False
        try:
            async for chunk in session.llm_client.stream_generate(
                system_prompt=self._prompt_builder.build_system_prompt(),
                user_prompt=self._prompt_builder.build_user_prompt(
                    session.request,
                    context_bundle,
                    failure_hint,
                    primary_target_path=primary_target_path,
                    scope_summary=session.scope_summary,
                ),
                max_tokens=4000,
                temperature=0.1,
            ):
                if not chunk:
                    continue
                raw_output += chunk
                await emit_model_delta(session, chunk)
            await capture_model_output(session, attempt_number, raw_output)
            captured_output = True

            default_file_path = None
            if len(editable_views) == 1:
                default_file_path = next(iter(editable_views))
            plan = self._parse_edit_plan(raw_output, default_file_path=default_file_path)
            draft_contents = self._apply_edits(
                editable_views=editable_views,
                editable_paths=session.editable_paths,
                edits=plan.edits,
            )
        except Exception as error:
            if not captured_output:
                await capture_model_output(session, attempt_number, raw_output)
            await record_step(
                session,
                TaskStepEvent(
                    task_id=session.task_id,
                    step_id=step_id,
                    step_kind="edit",
                    status="failed",
                    file_path=primary_target_path,
                    content_hash=editable_views[primary_target_path].content_hash,
                    intent="Generate local edits for the declared editable files",
                    reasoning_summary=self._truncate(str(error)),
                    input_summary=self._truncate(
                        f"editable={len(editable_views)}, contexts={len(context_views)}"
                    ),
                    output_summary=self._truncate(str(error)),
                ),
            )
            raise

        changed_count = len(draft_contents)
        edit_count = len(plan.edits)
        await record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="edit",
                status="succeeded",
                file_path=primary_target_path,
                content_hash=self._hash_content(json.dumps(draft_contents, sort_keys=True)),
                intent=plan.intent,
                reasoning_summary=self._truncate(plan.reasoning),
                input_summary=self._truncate(
                    f"editable={len(editable_views)}, contexts={len(context_views)}"
                ),
                output_summary=self._truncate(
                    f"generated {edit_count} edit(s) across {changed_count} file(s)"
                ),
            ),
        )
        return EditExecutionOutcome(
            plan=plan,
            draft_contents=draft_contents,
            step_id=step_id,
        )

    def _parse_edit_plan(
        self,
        raw_output: str,
        default_file_path: Optional[str] = None,
    ) -> EditPlan:
        normalized = raw_output.strip()
        if not normalized:
            raise CodingTaskValidationError("Model returned an empty response")

        stripped = CODE_FENCE_PATTERN.sub("", normalized).strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise CodingTaskValidationError("Model output did not contain a JSON object")

        payload = stripped[start : end + 1]
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as error:
            raise CodingTaskValidationError(
                f"Model output was not valid JSON: {error.msg}"
            ) from error

        if default_file_path and isinstance(parsed, dict):
            edits = parsed.get("edits")
            if isinstance(edits, list):
                for item in edits:
                    if isinstance(item, dict) and "file_path" not in item:
                        item["file_path"] = default_file_path

        try:
            plan = EditPlan.model_validate(parsed)
        except Exception as error:
            raise CodingTaskValidationError(
                f"Model output failed validation: {error}"
            ) from error

        if not plan.edits:
            raise CodingTaskValidationError("Model returned no edits")
        if not plan.intent.strip():
            raise CodingTaskValidationError("Model returned an empty intent")
        return plan

    def _apply_edits(
        self,
        editable_views: dict[str, object],
        editable_paths: set[str],
        edits: list[CodingTaskEdit],
    ) -> dict[str, str]:
        working_content = {
            file_path: observed.content
            for file_path, observed in editable_views.items()
        }

        for index, edit in enumerate(edits, start=1):
            if edit.file_path not in editable_paths:
                raise CodingTaskValidationError(
                    f"Edit {index} targeted a file outside the editable set: {edit.file_path}"
                )
            if not edit.old_text:
                raise CodingTaskValidationError(f"Edit {index} old_text must not be empty")

            current_content = working_content[edit.file_path]
            matches = current_content.count(edit.old_text)
            if matches == 0:
                raise CodingTaskValidationError(
                    f"Edit {index} old_text did not match the observed file: {edit.file_path}"
                )
            if matches > 1:
                raise CodingTaskValidationError(
                    f"Edit {index} old_text matched multiple locations in {edit.file_path}"
                )

            working_content[edit.file_path] = current_content.replace(
                edit.old_text,
                edit.new_text,
                1,
            )

        changed = {
            file_path: content
            for file_path, content in working_content.items()
            if content != editable_views[file_path].content
        }
        if not changed:
            raise CodingTaskValidationError("Edit plan did not change any editable file")
        return changed

    def _hash_content(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _truncate(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) <= MAX_SUMMARY_LENGTH:
            return stripped
        return f"{stripped[: MAX_SUMMARY_LENGTH - 3]}..."


__all__ = ["CodeAttemptExecutor", "EditExecutionOutcome"]
