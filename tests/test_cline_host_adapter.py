import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from tailevents.api import create_app
from tailevents.config import Settings
from tailevents.host_adapters.cline import convert_cline_messages


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


def _api_result_message(ts: int, request: str) -> dict:
    return {
        "ts": ts,
        "type": "say",
        "say": "api_req_started",
        "text": json.dumps({"request": request}),
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


def test_cline_adapter_converts_tracebridge_final_file_result(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = convert_cline_messages(
        task_id="task-bridge",
        workspace_root=workspace_root,
        messages=[
            {"ts": 1, "type": "say", "say": "task", "text": "Update target"},
            _api_result_message(
                2,
                "[replace_in_file for 'target.py'] Result:\n"
                "The content was successfully saved.\n\n"
                "<final_file_content path=\"target.py\">\n"
                "def target():\n"
                "    return 2\n"
                "</final_file_content>",
            ),
        ],
    )

    assert result.summary.task_prompt == "Update target"
    assert result.summary.tool_count == 1
    assert result.summary.file_change_count == 1
    assert result.summary.raw_event_count == 1
    assert result.raw_events[0].action_type.value == "modify"
    assert result.raw_events[0].file_path == "target.py"
    assert result.raw_events[0].code_snapshot == "def target():\n    return 2\n"
    assert result.raw_events[0].session_id == "cline:task-bridge"
    assert result.raw_events[0].agent_step_id == "cline:task-bridge:2"


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
            assert body["coding_task_id"] == "cline:task-route"
            assert body["read_observation_count"] == 1
            assert body["event_ids"]

            events_response = client.get("/api/v1/events", params={"session": "cline:task-route"})
            assert events_response.status_code == 200
            events = events_response.json()
            assert len(events) == 1
            assert events[0]["agent_step_id"] == "cline:task-route:11"
            assert events[0]["file_path"] == "target.py"

            history_response = client.get("/api/v1/coding/tasks/cline:task-route")
            assert history_response.status_code == 200
            history = history_response.json()
            assert history["task_id"] == "cline:task-route"
            assert history["status"] == "applied"
            assert history["resolved_target_files"] == ["target.py"]
            assert [step["step_kind"] for step in history["steps"]] == ["view", "edit"]
            assert history["applied_events"][0]["event_id"] == body["event_ids"][0]

            entities_response = client.get("/api/v1/entities")
            assert entities_response.status_code == 200
            entities = entities_response.json()
            target_entity = next(item for item in entities if item["name"] == "target")

            explain_response = client.get(
                f"/api/v1/explain/{target_entity['entity_id']}/summary"
            )
            assert explain_response.status_code == 200
            explanation = explain_response.json()
            assert explanation["history_source"] == "traced_only"
            assert explanation["summary"] == "Cline modify target.py"
