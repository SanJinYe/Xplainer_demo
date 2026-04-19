"""Draft verification for coding tasks."""

import ast
import hashlib
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from tailevents.coding.context.model import CodingContextBundle, ObservedFileView
from tailevents.coding.exceptions import CodingTaskValidationError
from tailevents.coding.runtime.session import TaskRuntimeSession
from tailevents.models.task import TaskStepEvent, VerifiedFileDraft, new_step_id


MAX_SUMMARY_LENGTH = 240


@dataclass(frozen=True)
class DraftVerificationOutcome:
    """Completed verification result for a single attempt."""

    step_id: str
    verified_files: list[VerifiedFileDraft]


_RecordStep = Callable[[TaskRuntimeSession, TaskStepEvent], Awaitable[None]]
_ViewRequester = Callable[[TaskRuntimeSession, str, str], Awaitable[ObservedFileView]]


class DraftVerifier:
    """Re-observe editable files and verify the generated draft."""

    async def verify(
        self,
        session: TaskRuntimeSession,
        context_bundle: CodingContextBundle,
        draft_contents: dict[str, str],
        request_view: _ViewRequester,
        record_step: _RecordStep,
    ) -> DraftVerificationOutcome:
        step_id = new_step_id()
        await record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="verify",
                status="started",
                file_path=session.request.target_file_path,
                content_hash=self._hash_content(json.dumps(draft_contents, sort_keys=True)),
                intent="Verify the task draft before Apply",
                input_summary=self._truncate(
                    f"task-level drift + python syntax across {len(draft_contents)} file(s)"
                ),
            ),
        )

        try:
            for file_path, draft_content in draft_contents.items():
                latest_view = await request_view(
                    session,
                    file_path,
                    f"Re-observe editable file {file_path} before Apply verification",
                )
                original_view = context_bundle.editable_views[file_path]
                self._validate_expected_version(
                    file_path,
                    latest_view,
                    session.expected_versions[file_path],
                )
                if latest_view.content_hash != original_view.content_hash:
                    raise CodingTaskValidationError(
                        f"Editable file content drifted during task execution: {file_path}"
                    )
                self._validate_python_source(file_path, draft_content)
        except Exception as error:
            await record_step(
                session,
                TaskStepEvent(
                    task_id=session.task_id,
                    step_id=step_id,
                    step_kind="verify",
                    status="failed",
                    file_path=session.request.target_file_path,
                    content_hash=self._hash_content(json.dumps(draft_contents, sort_keys=True)),
                    intent="Verify the task draft before Apply",
                    reasoning_summary=self._truncate(str(error)),
                    input_summary=self._truncate(
                        f"task-level drift + python syntax across {len(draft_contents)} file(s)"
                    ),
                    output_summary=self._truncate(str(error)),
                ),
            )
            raise

        verified_files: list[VerifiedFileDraft] = []
        for file_path in self._ordered_editable_paths(session):
            if file_path not in draft_contents:
                continue
            original_view = context_bundle.editable_views[file_path]
            verified_files.append(
                VerifiedFileDraft(
                    file_path=file_path,
                    content=draft_contents[file_path],
                    content_hash=self._hash_content(draft_contents[file_path]),
                    original_content_hash=original_view.content_hash,
                    original_document_version=original_view.document_version,
                )
            )

        await record_step(
            session,
            TaskStepEvent(
                task_id=session.task_id,
                step_id=step_id,
                step_kind="verify",
                status="succeeded",
                file_path=session.request.target_file_path,
                content_hash=self._hash_content(json.dumps(draft_contents, sort_keys=True)),
                intent="Verify the task draft before Apply",
                reasoning_summary=self._truncate(
                    "All changed files passed drift checks and Python syntax validation."
                ),
                input_summary=self._truncate(
                    f"task-level drift + python syntax across {len(draft_contents)} file(s)"
                ),
                output_summary=self._truncate(
                    f"verified {len(verified_files)} file(s) ready for Apply"
                ),
            ),
        )
        return DraftVerificationOutcome(step_id=step_id, verified_files=verified_files)

    def _validate_expected_version(
        self,
        file_path: str,
        observed: ObservedFileView,
        expected_version: int,
    ) -> None:
        if observed.document_version is None:
            raise CodingTaskValidationError(
                f"Observed file did not include a document version: {file_path}"
            )
        if observed.document_version != expected_version:
            raise CodingTaskValidationError(
                f"Editable file changed before the task could start: {file_path}"
            )

    def _validate_python_source(self, file_path: str, source: str) -> None:
        if not file_path.lower().endswith(".py"):
            return
        try:
            ast.parse(source)
        except SyntaxError as error:
            message = error.msg or "invalid syntax"
            line = error.lineno or "unknown"
            raise CodingTaskValidationError(
                f"Draft is not valid Python: {file_path}: line {line}: {message}"
            ) from error

    def _ordered_editable_paths(self, session: TaskRuntimeSession) -> list[str]:
        return [session.request.target_file_path] + [
            item.file_path for item in session.request.editable_files
        ]

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


__all__ = ["DraftVerificationOutcome", "DraftVerifier"]
