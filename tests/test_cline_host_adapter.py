import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from tailevents.api import create_app
from tailevents.config import Settings
from tailevents.host_adapters.cline import convert_cline_messages, normalize_cline_messages


class FakeLLMClient:
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        return "ok"

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ):
        yield "ok"


def _tool_message(ts: int, payload: dict, partial: bool = False) -> dict:
    return {
        "ts": ts,
        "type": "say",
        "say": "tool",
        "text": json.dumps(payload),
        "partial": partial,
    }


def test_cline_adapter_converts_tools_and_tracks_skips(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "target.py").write_text("def target():\n    return 2\n", encoding="utf-8")
    (workspace_root / "created.py").write_text("def created():\n    return 1\n", encoding="utf-8")

    result = convert_cline_messages(
        task_id="task-1",
        workspace_root=workspace_root,
        messages=[
            _tool_message(1, {"tool": "readFile", "path": "target.py"}),
            _tool_message(2, {"tool": "editedExistingFile", "path": "target.py", "diff": "-1\n+2"}),
            _tool_message(3, {"tool": "newFileCreated", "path": "created.py", "content": "fallback"}),
            _tool_message(4, {"tool": "fileDeleted", "path": "deleted.py", "content": "def old():\n    pass\n"}),
            _tool_message(5, {"tool": "fileDeleted", "path": "missing.py"}),
            _tool_message(6, {"tool": "editedExistingFile", "path": "target.py"}, partial=True),
            {"ts": 7, "type": "say", "say": "completion_result", "text": "done"},
        ],
    )

    assert result.summary.read_observation_count == 1
    assert result.summary.file_change_count == 4
    assert result.summary.raw_event_count == 3
    assert result.summary.completion_count == 1
    assert result.summary.skipped["missing_snapshot"] == 1
    assert result.summary.skipped["partial_message"] == 1
    assert [event.action_type.value for event in result.raw_events] == ["modify", "create", "delete"]
    assert [event.agent_step_id for event in result.raw_events] == [
        "cline:task-1:2",
        "cline:task-1:3",
        "cline:task-1:4",
    ]


def test_cline_adapter_emits_host_agnostic_normalized_events(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "target.py").write_text("def target():\n    return 2\n", encoding="utf-8")

    result = normalize_cline_messages(
        task_id="task-normalized",
        workspace_root=workspace_root,
        messages=[
            _tool_message(1, {"tool": "readFile", "path": "target.py"}),
            _tool_message(2, {"tool": "editedExistingFile", "path": "target.py", "diff": "-1\n+2"}),
            {"ts": 3, "type": "say", "say": "completion_result", "text": "done"},
        ],
    )

    assert [event.kind for event in result.normalized_events] == [
        "read_observation",
        "file_change",
        "completion",
    ]
    file_change = result.normalized_events[1]
    assert file_change.host == "cline"
    assert file_change.task_id == "task-normalized"
    assert file_change.session_id == "cline:task-normalized"
    assert file_change.agent_step_id == "cline:task-normalized:2"
    assert file_change.tool_name == "editedExistingFile"
    assert file_change.file_path == "target.py"
    assert result.raw_events[0].session_id == file_change.session_id


def test_cline_host_route_ingests_and_lists_session_events() -> None:
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        workspace_root = temp_path / "workspace"
        workspace_root.mkdir()
        (workspace_root / "target.py").write_text("def target():\n    return 2\n", encoding="utf-8")
        settings = Settings(db_path=str(temp_path / "tailevents.db"))
        app = create_app(settings=settings, llm_client=FakeLLMClient())

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/host/cline/events",
                json={
                    "task_id": "task-route",
                    "cwd": str(workspace_root),
                    "messages": [
                        _tool_message(10, {"tool": "readFile", "path": "target.py"}),
                        _tool_message(
                            11,
                            {"tool": "editedExistingFile", "path": "target.py", "diff": "-1\n+2"},
                        ),
                    ],
                },
            )

            assert response.status_code == 201
            body = response.json()
            assert body["ingested_count"] == 1
            assert body["read_observation_count"] == 1
            assert body["event_ids"]

            events_response = client.get("/api/v1/events", params={"session": "cline:task-route"})
            assert events_response.status_code == 200
            events = events_response.json()
            assert len(events) == 1
            assert events[0]["agent_step_id"] == "cline:task-route:11"
            assert events[0]["file_path"] == "target.py"


def test_cline_host_route_supports_full_wrapper_explain_and_graph_path() -> None:
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        workspace_root = temp_path / "workspace"
        workspace_root.mkdir()
        (workspace_root / "flow.py").write_text(
            "def helper():\n"
            "    return 1\n\n"
            "def caller():\n"
            "    return helper()\n",
            encoding="utf-8",
        )
        settings = Settings(db_path=str(temp_path / "tailevents.db"))
        app = create_app(settings=settings, llm_client=FakeLLMClient())

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/host/cline/events",
                json={
                    "task_id": "task-full-path",
                    "cwd": str(workspace_root),
                    "messages": [
                        _tool_message(20, {"tool": "readFile", "path": "flow.py"}),
                        _tool_message(
                            21,
                            {"tool": "editedExistingFile", "path": "flow.py", "diff": "-old\n+new"},
                        ),
                        {"ts": 22, "type": "say", "say": "completion_result", "text": "done"},
                    ],
                },
            )
            assert response.status_code == 201
            assert response.json()["ingested_count"] == 1

            entities_response = client.get("/api/v1/entities")
            assert entities_response.status_code == 200
            entities = entities_response.json()
            caller = next(entity for entity in entities if entity["qualified_name"] == "caller")
            helper = next(entity for entity in entities if entity["qualified_name"] == "helper")

            explain_response = client.get(f"/api/v1/explain/{caller['entity_id']}/summary")
            assert explain_response.status_code == 200
            explanation = explain_response.json()
            assert explanation["entity_id"] == caller["entity_id"]
            assert explanation["history_source"] == "traced_only"
            assert explanation["creation_intent"] == "Cline modify flow.py"

            impact_response = client.get(
                f"/api/v1/relations/{helper['entity_id']}/impact-paths",
                params={"direction": "upstream"},
            )
            assert impact_response.status_code == 200
            assert impact_response.json()
