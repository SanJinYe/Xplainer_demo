"""Cline trace adapter."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from tailevents.models.event import RawEvent


FILE_CHANGE_ACTIONS = {
    "editedExistingFile": "modify",
    "newFileCreated": "create",
    "fileDeleted": "delete",
}
READ_ONLY_TOOLS = {"readFile"}


class ClineTraceBatchRequest(BaseModel):
    """Cline-native messages posted by a host-side trace tap."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    cwd: str
    messages: list[dict[str, Any]]
    source: Optional[str] = None


class ClineTraceIngestResponse(BaseModel):
    """Summary returned after normalizing and ingesting Cline traces."""

    task_id: str
    session_id: str
    message_count: int
    tool_count: int
    file_change_count: int
    raw_event_count: int
    read_observation_count: int
    completion_count: int
    error_count: int
    ingested_count: int
    skipped: dict[str, int] = Field(default_factory=dict)
    event_ids: list[str] = Field(default_factory=list)


@dataclass
class ClineConversionSummary:
    """Counters for a Cline trace conversion pass."""

    task_id: str
    message_count: int = 0
    tool_count: int = 0
    file_change_count: int = 0
    raw_event_count: int = 0
    read_observation_count: int = 0
    completion_count: int = 0
    error_count: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "message_count": self.message_count,
            "tool_count": self.tool_count,
            "file_change_count": self.file_change_count,
            "raw_event_count": self.raw_event_count,
            "read_observation_count": self.read_observation_count,
            "completion_count": self.completion_count,
            "error_count": self.error_count,
            "skipped": dict(self.skipped),
        }


@dataclass
class ClineConversionResult:
    """Normalized Cline trace conversion output."""

    summary: ClineConversionSummary
    raw_events: list[RawEvent]
    observations: list[dict[str, Any]]


def convert_cline_messages(
    task_id: str,
    workspace_root: Path,
    messages: list[dict[str, Any]],
) -> ClineConversionResult:
    """Convert Cline UI messages into TailEvents RawEvents."""

    summary = ClineConversionSummary(task_id=task_id, message_count=len(messages))
    raw_events: list[RawEvent] = []
    observations: list[dict[str, Any]] = []

    for message in messages:
        if message.get("partial") is True:
            summary.skipped["partial_message"] += 1
            continue

        kind = message.get("ask") or message.get("say")
        if kind == "completion_result":
            summary.completion_count += 1
            continue
        if kind == "error":
            summary.error_count += 1
            continue
        if kind != "tool":
            continue

        summary.tool_count += 1
        payload = parse_message_payload(message)
        if payload is None:
            summary.skipped["tool_payload_not_json"] += 1
            continue

        tool_name = str(payload.get("tool") or "")
        if tool_name in READ_ONLY_TOOLS:
            summary.read_observation_count += 1
            observations.append(
                {
                    "session_id": _session_id(task_id),
                    "agent_step_id": step_id(task_id, message),
                    "tool": tool_name,
                    "path": payload.get("path"),
                }
            )
            continue

        action_type = FILE_CHANGE_ACTIONS.get(tool_name)
        if action_type is None:
            summary.skipped[f"unsupported_tool:{tool_name or 'unknown'}"] += 1
            continue

        summary.file_change_count += 1
        raw_event, skip_reason = to_raw_event(
            task_id=task_id,
            message=message,
            payload=payload,
            action_type=action_type,
            workspace_root=workspace_root,
        )
        if raw_event is None:
            summary.skipped[skip_reason or "invalid_event"] += 1
            continue
        raw_events.append(raw_event)

    summary.raw_event_count = len(raw_events)
    return ClineConversionResult(
        summary=summary,
        raw_events=raw_events,
        observations=observations,
    )


def parse_message_payload(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def to_raw_event(
    task_id: str,
    message: dict[str, Any],
    payload: dict[str, Any],
    action_type: str,
    workspace_root: Path,
) -> tuple[Optional[RawEvent], Optional[str]]:
    raw_path = payload.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "missing_path"

    absolute_path = _resolve_file_path(raw_path, workspace_root)
    snapshot = _read_snapshot(absolute_path, payload)
    if not snapshot:
        return None, "missing_snapshot"

    file_path = _display_file_path(absolute_path, workspace_root, raw_path)
    event_payload = {
        "action_type": action_type,
        "file_path": file_path,
        "code_snapshot": snapshot,
        "intent": _intent_for(action_type, file_path),
        "reasoning": _reasoning_for(message, payload),
        "agent_step_id": step_id(task_id, message),
        "session_id": _session_id(task_id),
        "line_range": _line_range_for(payload, snapshot),
    }
    return RawEvent.model_validate(event_payload), None


def step_id(task_id: str, message: dict[str, Any]) -> str:
    return f"cline:{task_id}:{message.get('ts', 'unknown')}"


def _session_id(task_id: str) -> str:
    return f"cline:{task_id}"


def _resolve_file_path(raw_path: str, workspace_root: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return workspace_root / raw_path


def _display_file_path(absolute_path: Path, workspace_root: Path, raw_path: str) -> str:
    try:
        return absolute_path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return raw_path.replace("\\", "/")


def _read_snapshot(path: Path, payload: dict[str, Any]) -> str:
    if path.exists() and path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")

    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content

    diff = payload.get("diff")
    if isinstance(diff, str) and diff.strip():
        return diff

    return ""


def _intent_for(action_type: str, file_path: str) -> str:
    verbs = {"create": "create", "modify": "modify", "delete": "delete"}
    return f"Cline {verbs.get(action_type, action_type)} {file_path}"


def _reasoning_for(message: dict[str, Any], payload: dict[str, Any]) -> str:
    parts: list[str] = []
    message_reasoning = message.get("reasoning")
    if isinstance(message_reasoning, str) and message_reasoning.strip():
        parts.append(message_reasoning.strip())

    tool = payload.get("tool") or "tool"
    details = payload.get("diff") or payload.get("content") or payload.get("result") or payload.get("path") or ""
    text = str(details)
    if len(text) > 500:
        text = text[:500] + "\n[truncated]"
    parts.append(f"Converted from Cline tool message: {tool}\n{text}")
    return "\n\n".join(parts)


def _line_range_for(payload: dict[str, Any], snapshot: str) -> Optional[tuple[int, int]]:
    start_numbers = payload.get("startLineNumbers")
    if isinstance(start_numbers, list) and start_numbers:
        try:
            start = int(start_numbers[0])
            return (start, max(start, start + len(snapshot.splitlines()) - 1))
        except (TypeError, ValueError):
            return None
    return None


__all__ = [
    "ClineConversionResult",
    "ClineConversionSummary",
    "ClineTraceBatchRequest",
    "ClineTraceIngestResponse",
    "convert_cline_messages",
    "parse_message_payload",
    "step_id",
    "to_raw_event",
]
