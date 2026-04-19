"""Code capability for a single coding attempt."""

from typing import Awaitable, Callable, Optional

from tailevents.coding.capability.base import CodeAttemptOutcome
from tailevents.coding.context.model import CodingContextBundle, ObservedFileView
from tailevents.coding.runtime.executor import CodeAttemptExecutor
from tailevents.coding.runtime.session import TaskRuntimeSession
from tailevents.coding.runtime.verifier import DraftVerifier
from tailevents.models.task import TaskStepEvent


_RecordStep = Callable[[TaskRuntimeSession, TaskStepEvent], Awaitable[None]]
_ViewRequester = Callable[[TaskRuntimeSession, str, str], Awaitable[ObservedFileView]]
_CaptureModelOutput = Callable[[TaskRuntimeSession, int, str], Awaitable[None]]
_EmitModelDelta = Callable[[TaskRuntimeSession, str], Awaitable[None]]


class CodeCapability:
    """Single-attempt code execution capability."""

    name = "code"

    def __init__(
        self,
        executor: CodeAttemptExecutor,
        verifier: DraftVerifier,
    ) -> None:
        self._executor = executor
        self._verifier = verifier

    async def execute_attempt(
        self,
        session: TaskRuntimeSession,
        context_bundle: CodingContextBundle,
        failure_hint: Optional[str],
        attempt_metadata: dict[str, object],
        request_view: _ViewRequester,
        record_step: _RecordStep,
        capture_model_output: _CaptureModelOutput,
        emit_model_delta: _EmitModelDelta,
    ) -> CodeAttemptOutcome:
        edit_outcome = await self._executor.execute(
            session=session,
            context_bundle=context_bundle,
            failure_hint=failure_hint,
            attempt_metadata=attempt_metadata,
            record_step=record_step,
            capture_model_output=capture_model_output,
            emit_model_delta=emit_model_delta,
        )
        verify_outcome = await self._verifier.verify(
            session=session,
            context_bundle=context_bundle,
            draft_contents=edit_outcome.draft_contents,
            request_view=request_view,
            record_step=record_step,
        )
        return CodeAttemptOutcome(
            plan=edit_outcome.plan,
            draft_contents=edit_outcome.draft_contents,
            verified_files=verify_outcome.verified_files,
            edit_step_id=edit_outcome.step_id,
            verify_step_id=verify_outcome.step_id,
        )


__all__ = ["CodeCapability"]
