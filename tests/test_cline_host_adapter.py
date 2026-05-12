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
    assert result.guidance_score < 100
    assert any(hint.category == "capture" for hint in result.guidance_hints)
    assert any(hint.category == "review" for hint in result.guidance_hints)
    assert [event.action_type.value for event in result.raw_events] == ["modify", "create", "delete"]
    assert [event.agent_step_id for event in result.raw_events] == [
        "cline:task-1:2",
        "cline:task-1:3",
        "cline:task-1:4",
    ]


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
            assert body["guidance_score"] == 95
            assert body["guidance_hints"]
            assert body["event_ids"]

            events_response = client.get("/api/v1/events", params={"session": "cline:task-route"})
            assert events_response.status_code == 200
            events = events_response.json()
            assert len(events) == 1
            assert events[0]["agent_step_id"] == "cline:task-route:11"
            assert events[0]["file_path"] == "target.py"


def test_cline_host_route_supports_synthetic_wrapper_product_loop() -> None:
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        workspace_root = temp_path / "workspace"
        workspace_root.mkdir()
        (workspace_root / "target.py").write_text(
            "def helper():\n"
            "    return 1\n\n"
            "def target():\n"
            "    return helper() + 1\n",
            encoding="utf-8",
        )
        settings = Settings(db_path=str(temp_path / "tailevents.db"))
        app = create_app(settings=settings, llm_client=FakeLLMClient())

        with TestClient(app) as client:
            ingest_response = client.post(
                "/api/v1/host/cline/events",
                json={
                    "task_id": "task-product-loop",
                    "cwd": str(workspace_root),
                    "messages": [
                        _tool_message(20, {"tool": "readFile", "path": "target.py"}),
                        _tool_message(
                            21,
                            {
                                "tool": "editedExistingFile",
                                "path": "target.py",
                                "diff": "-return helper()\n+return helper() + 1",
                            },
                        ),
                        {"ts": 22, "type": "say", "say": "completion_result", "text": "done"},
                    ],
                },
            )
            assert ingest_response.status_code == 201
            ingest_body = ingest_response.json()
            assert ingest_body["session_id"] == "cline:task-product-loop"
            assert ingest_body["ingested_count"] == 1
            assert ingest_body["guidance_score"] == 100
            assert [hint["category"] for hint in ingest_body["guidance_hints"]] == ["review"]

            events_response = client.get(
                "/api/v1/events",
                params={"session": "cline:task-product-loop"},
            )
            assert events_response.status_code == 200
            events = events_response.json()
            assert len(events) == 1
            assert events[0]["entity_refs"]

            entity_response = client.get(
                "/api/v1/entities/by-location",
                params={"file": "target.py", "line": 4},
            )
            assert entity_response.status_code == 200
            entity = entity_response.json()
            assert entity["name"] == "target"

            summary_response = client.get(f"/api/v1/explain/{entity['entity_id']}/summary")
            assert summary_response.status_code == 200
            summary = summary_response.json()
            assert summary["summary"] == "Cline modify target.py"
            assert summary["history_source"] == "traced_only"

            impact_response = client.get(
                f"/api/v1/relations/{entity['entity_id']}/impact-paths",
                params={"direction": "both"},
            )
            assert impact_response.status_code == 200
