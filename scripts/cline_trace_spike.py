"""Local spike for converting Cline task traces into TailEvents RawEvents."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tailevents.host_adapters.cline import (
    ClineConversionResult as ConversionResult,
    ClineConversionSummary as ConversionSummary,
    convert_cline_messages,
)


CLINE_EXTENSION_IDS = ("saoudrizwan.claude-dev", "cline.cline")


def load_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate_openrouter_env(env_path: Path) -> None:
    values = load_env_values(env_path)
    required = [
        "TAILEVENTS_OPENROUTER_API_KEY",
        "TAILEVENTS_OPENROUTER_MODEL",
    ]
    missing = [key for key in required if not values.get(key)]
    if values.get("TAILEVENTS_LLM_BACKEND", "").lower() != "openrouter":
        missing.append("TAILEVENTS_LLM_BACKEND=openrouter")
    if missing:
        raise RuntimeError("Missing OpenRouter configuration: " + ", ".join(missing))


def discover_cline_storage(explicit_path: Optional[Path] = None) -> Path:
    if explicit_path is not None:
        return _validate_storage_path(explicit_path)

    candidates: list[Path] = []
    for env_key in ("CLINE_GLOBAL_STORAGE", "CLINE_STORAGE_PATH"):
        env_value = os.environ.get(env_key)
        if env_value:
            candidates.append(Path(env_value))

    appdata = os.environ.get("APPDATA")
    if appdata:
        for product in ("Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf"):
            for extension_id in CLINE_EXTENSION_IDS:
                candidates.append(Path(appdata) / product / "User" / "globalStorage" / extension_id)

    home = Path.home()
    candidates.append(home / ".cline" / "data")

    for candidate in candidates:
        if _looks_like_storage(candidate):
            return candidate
    raise RuntimeError(
        "Could not find Cline global storage. Pass --cline-storage with the directory "
        "containing state/taskHistory.json and tasks/<taskId>/ui_messages.json."
    )


def _validate_storage_path(path: Path) -> Path:
    if not _looks_like_storage(path):
        raise RuntimeError(f"Cline storage path is missing expected state/tasks files: {path}")
    return path


def _looks_like_storage(path: Path) -> bool:
    return path.exists() and (
        (path / "state" / "taskHistory.json").exists() or (path / "tasks").exists()
    )


def read_task_history(storage_path: Path) -> list[dict[str, Any]]:
    path = storage_path / "state" / "taskHistory.json"
    if not path.exists():
        return []
    data = _read_json(path)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise RuntimeError(f"Expected taskHistory.json to contain a list: {path}")
    return [item for item in data if isinstance(item, dict)]


def choose_task_id(storage_path: Path, requested_task_id: str) -> str:
    if requested_task_id != "latest":
        return requested_task_id

    history = read_task_history(storage_path)
    if history:
        latest = max(history, key=lambda item: int(item.get("ts") or 0))
        task_id = latest.get("id")
        if task_id:
            return str(task_id)

    tasks_dir = storage_path / "tasks"
    if not tasks_dir.exists():
        raise RuntimeError("No Cline tasks directory found.")
    task_dirs = [path for path in tasks_dir.iterdir() if path.is_dir()]
    if not task_dirs:
        raise RuntimeError("No Cline task directories found.")
    return max(task_dirs, key=lambda path: path.stat().st_mtime).name


def read_ui_messages(storage_path: Path, task_id: str) -> list[dict[str, Any]]:
    path = storage_path / "tasks" / task_id / "ui_messages.json"
    if not path.exists():
        raise RuntimeError(f"Missing ui_messages.json for task {task_id}: {path}")
    data = _read_json(path)
    if not isinstance(data, list):
        raise RuntimeError(f"Expected ui_messages.json to contain a list: {path}")
    return [item for item in data if isinstance(item, dict)]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def convert_task(
    storage_path: Path,
    task_id: str,
    workspace_root: Path,
    messages: Optional[list[dict[str, Any]]] = None,
) -> ConversionResult:
    ui_messages = messages if messages is not None else read_ui_messages(storage_path, task_id)
    return convert_cline_messages(
        task_id=task_id,
        workspace_root=workspace_root,
        messages=ui_messages,
    )


def post_cline_messages(
    base_url: str,
    task_id: str,
    workspace_root: Path,
    messages: list[dict[str, Any]],
) -> int:
    url = base_url.rstrip("/") + "/api/v1/host/cline/events"
    payload = {
        "task_id": task_id,
        "cwd": str(workspace_root),
        "messages": messages,
        "source": "cline-trace-spike",
    }
    response = httpx.post(url, json=payload, timeout=30.0)
    response.raise_for_status()
    data = response.json()
    return int(data.get("ingested_count", 0)) if isinstance(data, dict) else 0


def print_summary(result: ConversionResult, posted_count: Optional[int] = None) -> None:
    summary = result.summary.to_dict()
    if posted_count is not None:
        summary["posted_count"] = posted_count
    print(json.dumps(summary, indent=2, sort_keys=True))


def watch_task(
    storage_path: Path,
    task_id: str,
    workspace_root: Path,
    seconds: int,
    interval: float,
) -> ConversionResult:
    deadline = time.monotonic() + seconds
    seen_steps: set[str] = set()
    last_result = ConversionResult(
        summary=ConversionSummary(task_id=task_id),
        raw_events=[],
        observations=[],
    )
    while time.monotonic() <= deadline:
        current_task_id = choose_task_id(storage_path, task_id)
        result = convert_task(storage_path, current_task_id, workspace_root)
        new_events = [event for event in result.raw_events if event.agent_step_id not in seen_steps]
        for event in new_events:
            seen_steps.add(event.agent_step_id or "")
        if new_events:
            print(
                json.dumps(
                    {
                        "task_id": current_task_id,
                        "new_raw_events": len(new_events),
                        "agent_step_ids": [event.agent_step_id for event in new_events],
                    },
                    sort_keys=True,
                )
            )
        last_result = result
        time.sleep(interval)
    return last_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read Cline task traces and convert file-change messages into TailEvents RawEvents."
    )
    parser.add_argument("--cline-storage", type=Path, default=None)
    parser.add_argument("--task-id", default="latest")
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--check-openrouter-config", action="store_true")
    parser.add_argument("--post", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--watch-seconds", type=int, default=0)
    parser.add_argument("--watch-interval", type=float, default=1.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.check_openrouter_config:
            validate_openrouter_env(args.env_file)

        storage_path = discover_cline_storage(args.cline_storage)
        workspace_root = args.workspace_root.resolve()

        if args.watch_seconds > 0:
            result = watch_task(
                storage_path=storage_path,
                task_id=args.task_id,
                workspace_root=workspace_root,
                seconds=args.watch_seconds,
                interval=args.watch_interval,
            )
            task_id = result.summary.task_id
            messages = read_ui_messages(storage_path, task_id)
        else:
            task_id = choose_task_id(storage_path, args.task_id)
            messages = read_ui_messages(storage_path, task_id)
            result = convert_task(storage_path, task_id, workspace_root, messages=messages)

        posted_count: Optional[int] = None
        if args.post:
            posted_count = post_cline_messages(
                base_url=args.base_url,
                task_id=task_id,
                workspace_root=workspace_root,
                messages=messages,
            )
        print_summary(result, posted_count=posted_count)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
