"""Scope selection for hint-first coding tasks."""

import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from tailevents.coding.exceptions import CodingTaskValidationError
from tailevents.coding.runtime.session import TaskRuntimeSession


MAX_SEARCH_MATCHES = 8
MAX_EDITABLE_FILES = 2
MAX_CONTEXT_FILES = 3

_SearchWorkspace = Callable[[TaskRuntimeSession, str, int, str], Awaitable[list[str]]]

_SCOPE_SYSTEM_PROMPT = """
You are resolving coding task scope from workspace file paths only.

Return exactly one JSON object with:
- primary_target_path
- editable_files
- context_files
- scope_summary

Rules:
- editable_files must contain one or two file paths.
- primary_target_path must be one of editable_files.
- context_files must contain at most three file paths.
- Only use file paths that appear in the provided candidate list.
- Prefer the smallest scope that can plausibly satisfy the task.
""".strip()


@dataclass(frozen=True)
class ResolvedScope:
    """Resolved file scope for one coding task."""

    primary_target_path: str
    target_files: list[str]
    editable_files: list[str]
    context_files: list[str]
    scope_summary: str


class ScopeResolver:
    """Resolve editable and context files from hints and workspace search."""

    async def resolve(
        self,
        session: TaskRuntimeSession,
        search_workspace: _SearchWorkspace,
    ) -> ResolvedScope:
        request = session.request
        target_hint = self._normalize_path(request.target_file_path)
        editable_hints = self._dedupe(
            [self._normalize_path(item.file_path) for item in request.editable_files]
        )
        context_hints = self._dedupe(
            [self._normalize_path(item) for item in request.context_files]
        )

        preferred_paths = self._dedupe(
            [target_hint, *editable_hints, *context_hints]
        )
        if preferred_paths:
            if target_hint is not None:
                primary_target = target_hint
                editable_files = self._dedupe(
                    [primary_target, *editable_hints]
                )[:MAX_EDITABLE_FILES]
                context_files = [
                    item
                    for item in context_hints
                    if item not in editable_files
                ][:MAX_CONTEXT_FILES]
            elif editable_hints:
                primary_target = editable_hints[0]
                editable_files = self._dedupe(editable_hints)[:MAX_EDITABLE_FILES]
                context_files = [
                    item
                    for item in context_hints
                    if item not in editable_files
                ][:MAX_CONTEXT_FILES]
            else:
                primary_target = context_hints[0]
                editable_files = [primary_target]
                context_files = [
                    item
                    for item in context_hints[1:]
                    if item != primary_target
                ][:MAX_CONTEXT_FILES]
            return ResolvedScope(
                primary_target_path=primary_target,
                target_files=list(editable_files),
                editable_files=list(editable_files),
                context_files=context_files,
                scope_summary="Resolved scope from explicit target and selected file hints.",
            )

        if "repo_observe" not in session.requested_lanes:
            raise CodingTaskValidationError(
                "No target hint was provided and repo_observe is unavailable"
            )

        matches = await search_workspace(
            session,
            request.user_prompt,
            MAX_SEARCH_MATCHES,
            "Search workspace files that may satisfy the coding task",
        )
        if not matches:
            raise CodingTaskValidationError("No workspace files matched the task prompt")

        candidate_paths = self._dedupe(matches)
        selection = await self._select_from_candidates(
            session=session,
            candidate_paths=candidate_paths,
        )
        if selection is not None:
            return selection

        primary_target = candidate_paths[0]
        editable_files = candidate_paths[:1]
        context_files = candidate_paths[1 : 1 + MAX_CONTEXT_FILES]
        return ResolvedScope(
            primary_target_path=primary_target,
            target_files=list(editable_files),
            editable_files=list(editable_files),
            context_files=list(context_files),
            scope_summary="Resolved scope from workspace search fallback.",
        )

    async def _select_from_candidates(
        self,
        session: TaskRuntimeSession,
        candidate_paths: list[str],
    ) -> Optional[ResolvedScope]:
        try:
            raw_output = await session.llm_client.generate(
                system_prompt=_SCOPE_SYSTEM_PROMPT,
                user_prompt=self._build_scope_user_prompt(
                    session=session,
                    candidate_paths=candidate_paths,
                ),
                max_tokens=800,
                temperature=0.0,
            )
        except Exception:
            return None

        try:
            parsed = json.loads(raw_output)
        except Exception:
            return None

        if not isinstance(parsed, dict):
            return None

        primary_target = self._normalize_path(parsed.get("primary_target_path"))
        editable_files = self._dedupe(parsed.get("editable_files", []))
        context_files = self._dedupe(parsed.get("context_files", []))
        scope_summary = str(parsed.get("scope_summary") or "").strip()

        if not primary_target or primary_target not in candidate_paths:
            return None
        editable_files = [
            item for item in editable_files if item in candidate_paths
        ][:MAX_EDITABLE_FILES]
        if primary_target not in editable_files:
            editable_files = [primary_target, *editable_files]
        editable_files = self._dedupe(editable_files)[:MAX_EDITABLE_FILES]
        context_files = [
            item
            for item in context_files
            if item in candidate_paths and item not in editable_files
        ][:MAX_CONTEXT_FILES]
        if not editable_files:
            return None
        return ResolvedScope(
            primary_target_path=primary_target,
            target_files=list(editable_files),
            editable_files=list(editable_files),
            context_files=list(context_files),
            scope_summary=scope_summary or "Resolved scope from workspace search.",
        )

    def _build_scope_user_prompt(
        self,
        session: TaskRuntimeSession,
        candidate_paths: list[str],
    ) -> str:
        joined_candidates = "\n".join(f"- {item}" for item in candidate_paths)
        return (
            "Task goal:\n"
            f"{session.request.user_prompt}\n\n"
            "Candidate workspace files:\n"
            f"{joined_candidates}\n"
        )

    def _dedupe(self, values: list[Optional[str]]) -> list[str]:
        seen: set[str] = set()
        items: list[str] = []
        for value in values:
            normalized = self._normalize_path(value)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            items.append(normalized)
        return items

    def _normalize_path(self, value: Optional[object]) -> Optional[str]:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized


__all__ = ["ResolvedScope", "ScopeResolver"]
