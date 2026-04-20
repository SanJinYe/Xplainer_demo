"""Context assembly for coding-task execution."""

from typing import Awaitable, Callable, Optional

from tailevents.coding.context.model import CodingContextBundle, ObservedFileView
from tailevents.coding.exceptions import CodingTaskValidationError
from tailevents.coding.runtime.session import TaskRuntimeSession


_ViewRequester = Callable[[TaskRuntimeSession, str, str], Awaitable[ObservedFileView]]
_VersionValidator = Callable[[str, ObservedFileView, Optional[int]], None]


class TaileventsContextAdapter:
    """Assemble the current coding context without changing prompt semantics."""

    async def build_bundle(
        self,
        session: TaskRuntimeSession,
        request_view: _ViewRequester,
        validate_expected_version: _VersionValidator,
    ) -> CodingContextBundle:
        editable_views = await self._observe_editable_views(
            session=session,
            request_view=request_view,
            validate_expected_version=validate_expected_version,
        )
        readonly_views = await self._observe_context_views(
            session=session,
            request_view=request_view,
        )
        return CodingContextBundle(
            editable_views=editable_views,
            readonly_views=readonly_views,
        )

    async def rebuild_bundle_for_retry(
        self,
        session: TaskRuntimeSession,
        request_view: _ViewRequester,
        validate_expected_version: _VersionValidator,
        initial_hashes: dict[str, str],
    ) -> CodingContextBundle:
        editable_views = await self._observe_editable_views(
            session=session,
            request_view=request_view,
            validate_expected_version=validate_expected_version,
        )
        for file_path, observed in editable_views.items():
            if observed.content_hash != initial_hashes[file_path]:
                raise CodingTaskValidationError(
                    f"Editable file content drifted during task execution: {file_path}"
                )
        readonly_views = await self._observe_context_views(
            session=session,
            request_view=request_view,
        )
        return CodingContextBundle(
            editable_views=editable_views,
            readonly_views=readonly_views,
        )

    async def _observe_editable_views(
        self,
        session: TaskRuntimeSession,
        request_view: _ViewRequester,
        validate_expected_version: _VersionValidator,
    ) -> dict[str, ObservedFileView]:
        observed: dict[str, ObservedFileView] = {}
        primary_target_path = session.resolved_primary_target_path
        if not primary_target_path:
            raise CodingTaskValidationError("Resolved primary target path is missing")

        for editable_path in session.resolved_editable_files:
            intent = (
                "Observe the resolved primary target file before editing"
                if editable_path == primary_target_path
                else f"Observe additional editable file {editable_path}"
            )
            observed[editable_path] = await request_view(
                session,
                editable_path,
                intent,
            )
            expected_version = session.expected_versions.get(editable_path)
            if expected_version is not None:
                validate_expected_version(
                    editable_path,
                    observed[editable_path],
                    expected_version,
                )

        return observed

    async def _observe_context_views(
        self,
        session: TaskRuntimeSession,
        request_view: _ViewRequester,
    ) -> list[ObservedFileView]:
        observed: list[ObservedFileView] = []
        for context_file in session.resolved_context_files:
            observed.append(
                await request_view(
                    session,
                    context_file,
                    f"Observe readonly context file {context_file}",
                )
            )
        return observed


__all__ = ["TaileventsContextAdapter"]
