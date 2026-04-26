from pathlib import Path

import pytest

from scripts.cline_trace_spike import (
    choose_task_id,
    convert_task,
    validate_openrouter_env,
)


def _write_task(storage_path: Path, task_id: str, messages: list[dict]) -> None:
    task_dir = storage_path / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "ui_messages.json").write_text(
        __import__("json").dumps(messages),
        encoding="utf-8",
    )
    state_dir = storage_path / "state"
    state_dir.mkdir()
    (state_dir / "taskHistory.json").write_text(
        __import__("json").dumps(
            [
                {
                    "id": task_id,
                    "ts": 3000,
                    "task": "small task",
                    "tokensIn": 1,
                    "tokensOut": 1,
                    "totalCost": 0,
                }
            ]
        ),
        encoding="utf-8",
    )


def _tool_message(ts: int, payload: dict, partial: bool = False) -> dict:
    return {
        "ts": ts,
        "type": "say",
        "say": "tool",
        "text": __import__("json").dumps(payload),
        "partial": partial,
    }


def test_convert_cline_task_messages_to_raw_events(tmp_path: Path) -> None:
    storage_path = tmp_path / "cline-storage"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "existing.py").write_text("def existing():\n    return 2\n", encoding="utf-8")
    (workspace_root / "created.py").write_text("def created():\n    return 1\n", encoding="utf-8")

    _write_task(
        storage_path,
        "task-1",
        [
            {"ts": 1, "type": "say", "say": "task", "text": "small task"},
            _tool_message(2, {"tool": "readFile", "path": "existing.py"}),
            _tool_message(3, {"tool": "editedExistingFile", "path": "existing.py", "diff": "-1\n+2"}),
            _tool_message(4, {"tool": "newFileCreated", "path": "created.py", "content": "fallback"}),
            _tool_message(5, {"tool": "fileDeleted", "path": "deleted.py", "content": "def old():\n    pass\n"}),
            _tool_message(6, {"tool": "fileDeleted", "path": "missing.py"}),
            {"ts": 7, "type": "say", "say": "completion_result", "text": "done"},
        ],
    )

    task_id = choose_task_id(storage_path, "latest")
    result = convert_task(storage_path, task_id, workspace_root)

    assert result.summary.task_id == "task-1"
    assert result.summary.tool_count == 5
    assert result.summary.read_observation_count == 1
    assert result.summary.file_change_count == 4
    assert result.summary.raw_event_count == 3
    assert result.summary.completion_count == 1
    assert result.summary.skipped["missing_snapshot"] == 1

    assert [event.action_type.value for event in result.raw_events] == ["modify", "create", "delete"]
    assert [event.file_path for event in result.raw_events] == ["existing.py", "created.py", "deleted.py"]
    assert result.raw_events[0].session_id == "cline:task-1"
    assert result.raw_events[0].agent_step_id == "cline:task-1:3"
    assert result.raw_events[0].code_snapshot == "def existing():\n    return 2\n"
    assert result.observations == [
        {
            "session_id": "cline:task-1",
            "agent_step_id": "cline:task-1:2",
            "tool": "readFile",
            "path": "existing.py",
        }
    ]


def test_multiple_edits_to_same_file_generate_ordered_events(tmp_path: Path) -> None:
    storage_path = tmp_path / "cline-storage"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "target.py").write_text("def target():\n    return 3\n", encoding="utf-8")
    _write_task(
        storage_path,
        "task-2",
        [
            _tool_message(10, {"tool": "editedExistingFile", "path": "target.py", "diff": "-1\n+2"}),
            _tool_message(11, {"tool": "editedExistingFile", "path": "target.py", "diff": "-2\n+3"}),
        ],
    )

    result = convert_task(storage_path, "task-2", workspace_root)

    assert result.summary.raw_event_count == 2
    assert [event.agent_step_id for event in result.raw_events] == [
        "cline:task-2:10",
        "cline:task-2:11",
    ]


def test_openrouter_config_error_does_not_leak_secret(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TAILEVENTS_LLM_BACKEND=openrouter\n"
        "TAILEVENTS_OPENROUTER_API_KEY=sk-secret-value\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError) as exc:
        validate_openrouter_env(env_path)

    message = str(exc.value)
    assert "TAILEVENTS_OPENROUTER_MODEL" in message
    assert "sk-secret-value" not in message
