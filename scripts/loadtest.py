"""Simple HTTP load test runner for TailEvents."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import time
import uuid
from pathlib import Path
from statistics import mean
from typing import Any, Optional

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8766/api/v1/"
DEFAULT_OUTPUT_DIR = Path("loadtest-results")
DEFAULT_MIX = "70,20,10"
QUERY_SEARCH_TERMS = ["fetch_api_data_", "DataProcessor_"]
MIXED_OPERATIONS = ["explain", "ingest", "entity_search", "admin_stats"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run simple TailEvents HTTP load tests.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base API URL, e.g. http://127.0.0.1:8766/api/v1",
    )
    parser.add_argument(
        "--scenario",
        choices=[
            "ingest",
            "summary",
            "impact-paths",
            "impact-paths-mixed",
            "hot-cache-explain",
            "mixed-workload",
        ],
        required=True,
        help="Load test scenario to run.",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=100,
        help="Total request count to send.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Maximum concurrent requests.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write JSON summary.",
    )
    parser.add_argument(
        "--spawn-app",
        action="store_true",
        help="Start a temporary local app process before the load test and stop it afterwards.",
    )
    parser.add_argument(
        "--app-host",
        default="127.0.0.1",
        help="Host for the spawned app process.",
    )
    parser.add_argument(
        "--app-port",
        type=int,
        default=8877,
        help="Port for the spawned app process.",
    )
    parser.add_argument(
        "--db-path",
        default=".tmp/loadtest.db",
        help="SQLite db path for the spawned app process.",
    )
    parser.add_argument(
        "--mix",
        default=DEFAULT_MIX,
        help="Mix ratio for mixed-workload as explain,ingest,query. Default: 70,20,10",
    )
    parser.add_argument(
        "--seed-count",
        type=int,
        default=10,
        help="Number of unique seed code sessions for mixed-workload.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Random seed used to shuffle mixed-workload operations.",
    )
    return parser.parse_args()


def make_smoke_events(session_id: str) -> list[dict[str, Any]]:
    return [
        {
            "action_type": "create",
            "file_path": "data_processor.py",
            "line_range": [1, 3],
            "code_snapshot": (
                "def fetch_data(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    return request_remote(url, timeout=timeout)\n"
            ),
            "intent": "create fetch_data to isolate remote API access",
            "reasoning": "start with a small helper before building the processing flow",
            "session_id": session_id,
        },
        {
            "action_type": "modify",
            "file_path": "data_processor.py",
            "line_range": [1, 6],
            "code_snapshot": (
                "def fetch_data(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    try:\n"
                "        return request_remote(url, timeout=timeout)\n"
                "    except Exception:\n"
                '        return {"items": [], "error": "upstream_failed"}\n'
            ),
            "intent": "add error handling to fetch_data",
            "reasoning": "return a safe fallback when the remote API fails",
            "decision_alternatives": [
                "raise the exception to the caller",
                "return None",
            ],
            "session_id": session_id,
        },
        {
            "action_type": "rename",
            "file_path": "data_processor.py",
            "line_range": [1, 6],
            "code_snapshot": (
                "def fetch_api_data(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    try:\n"
                "        return request_remote(url, timeout=timeout)\n"
                "    except Exception:\n"
                '        return {"items": [], "error": "upstream_failed"}\n'
            ),
            "intent": "rename fetch_data to fetch_api_data",
            "reasoning": "make the helper name explicit before other callers depend on it",
            "session_id": session_id,
        },
        {
            "action_type": "create",
            "file_path": "data_processor.py",
            "line_range": [1, 11],
            "code_snapshot": (
                "def fetch_api_data(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    try:\n"
                "        return request_remote(url, timeout=timeout)\n"
                "    except Exception:\n"
                '        return {"items": [], "error": "upstream_failed"}\n'
                "\n"
                "class DataProcessor:\n"
                "    def process(self, url):\n"
                "        raw = fetch_api_data(url)\n"
                '        return raw.get("items", [])\n'
            ),
            "intent": "create DataProcessor.process to call fetch_api_data",
            "reasoning": "keep processing separate while reusing the fetch helper",
            "session_id": session_id,
        },
        {
            "action_type": "modify",
            "file_path": "data_processor.py",
            "line_range": [1, 14],
            "code_snapshot": (
                "import logging\n"
                "\n"
                "def fetch_api_data(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    try:\n"
                "        return request_remote(url, timeout=timeout)\n"
                "    except Exception:\n"
                '        return {"items": [], "error": "upstream_failed"}\n'
                "\n"
                "class DataProcessor:\n"
                "    def process(self, url):\n"
                '        logging.info("processing url=%s", url)\n'
                "        raw = fetch_api_data(url)\n"
                '        return raw.get("items", [])\n'
            ),
            "intent": "add logging to DataProcessor.process",
            "reasoning": "record processing requests without mixing logging into the fetch helper",
            "session_id": session_id,
        },
    ]


def build_seed_names(suffix: str) -> dict[str, str]:
    return {
        "file_path": f"data_processor_{suffix}.py",
        "fetch_name": f"fetch_data_{suffix}",
        "fetch_api_name": f"fetch_api_data_{suffix}",
        "class_name": f"DataProcessor_{suffix}",
        "qualified_method_name": f"DataProcessor_{suffix}.process",
    }


def make_seed_smoke_events(session_id: str, suffix: str) -> list[dict[str, Any]]:
    names = build_seed_names(suffix)
    fetch_name = names["fetch_name"]
    fetch_api_name = names["fetch_api_name"]
    class_name = names["class_name"]
    file_path = names["file_path"]

    return [
        {
            "action_type": "create",
            "file_path": file_path,
            "line_range": [1, 3],
            "code_snapshot": (
                f"def {fetch_name}(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    return request_remote(url, timeout=timeout)\n"
            ),
            "intent": f"create {fetch_name} to isolate remote API access",
            "reasoning": "start with a small helper before building the processing flow",
            "session_id": session_id,
        },
        {
            "action_type": "modify",
            "file_path": file_path,
            "line_range": [1, 6],
            "code_snapshot": (
                f"def {fetch_name}(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    try:\n"
                "        return request_remote(url, timeout=timeout)\n"
                "    except Exception:\n"
                '        return {"items": [], "error": "upstream_failed"}\n'
            ),
            "intent": f"add error handling to {fetch_name}",
            "reasoning": "return a safe fallback when the remote API fails",
            "decision_alternatives": [
                "raise the exception to the caller",
                "return None",
            ],
            "session_id": session_id,
        },
        {
            "action_type": "rename",
            "file_path": file_path,
            "line_range": [1, 6],
            "code_snapshot": (
                f"def {fetch_api_name}(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    try:\n"
                "        return request_remote(url, timeout=timeout)\n"
                "    except Exception:\n"
                '        return {"items": [], "error": "upstream_failed"}\n'
            ),
            "intent": f"rename {fetch_name} to {fetch_api_name}",
            "reasoning": "make the helper name explicit before other callers depend on it",
            "session_id": session_id,
        },
        {
            "action_type": "create",
            "file_path": file_path,
            "line_range": [1, 11],
            "code_snapshot": (
                f"def {fetch_api_name}(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    try:\n"
                "        return request_remote(url, timeout=timeout)\n"
                "    except Exception:\n"
                '        return {"items": [], "error": "upstream_failed"}\n'
                "\n"
                f"class {class_name}:\n"
                "    def process(self, url):\n"
                f"        raw = {fetch_api_name}(url)\n"
                '        return raw.get("items", [])\n'
            ),
            "intent": f"create {class_name}.process to call {fetch_api_name}",
            "reasoning": "keep processing separate while reusing the fetch helper",
            "session_id": session_id,
        },
        {
            "action_type": "modify",
            "file_path": file_path,
            "line_range": [1, 14],
            "code_snapshot": (
                "import logging\n"
                "\n"
                f"def {fetch_api_name}(url, timeout=5.0):\n"
                '    """Fetch raw API data."""\n'
                "    try:\n"
                "        return request_remote(url, timeout=timeout)\n"
                "    except Exception:\n"
                '        return {"items": [], "error": "upstream_failed"}\n'
                "\n"
                f"class {class_name}:\n"
                "    def process(self, url):\n"
                '        logging.info("processing url=%s", url)\n'
                f"        raw = {fetch_api_name}(url)\n"
                '        return raw.get("items", [])\n'
            ),
            "intent": f"add logging to {class_name}.process",
            "reasoning": "record processing requests without mixing logging into the fetch helper",
            "session_id": session_id,
        },
    ]


def make_explain_targets(suffix: str) -> list[dict[str, Any]]:
    names = build_seed_names(suffix)
    function_query = names["fetch_api_name"]
    method_query = names["qualified_method_name"]
    return [
        {
            "target": function_query,
            "payload": {
                "query": function_query,
                "cursor_word": function_query,
                "detail_level": "trace",
                "include_relations": True,
            },
        },
        {
            "target": method_query,
            "payload": {
                "query": method_query,
                "cursor_word": method_query,
                "detail_level": "trace",
                "include_relations": True,
            },
        },
    ]


def make_ingest_payload(run_id: str) -> list[dict[str, Any]]:
    file_path = f"pressure_{run_id}.py"
    session_id = f"pressure-{run_id}"
    return [
        {
            "action_type": "create",
            "file_path": file_path,
            "line_range": [1, 3],
            "code_snapshot": (
                f"def fetch_data_{run_id}(url, timeout=5.0):\n"
                "    return request_remote(url, timeout=timeout)\n"
            ),
            "intent": f"create fetch helper {run_id}",
            "reasoning": "pressure test create path",
            "session_id": session_id,
        },
        {
            "action_type": "rename",
            "file_path": file_path,
            "line_range": [1, 3],
            "code_snapshot": (
                f"def fetch_api_data_{run_id}(url, timeout=5.0):\n"
                "    return request_remote(url, timeout=timeout)\n"
            ),
            "intent": f"rename fetch helper {run_id}",
            "reasoning": "pressure test rename path",
            "session_id": session_id,
        },
    ]


def build_mixed_operations(
    request_count: int,
    mix: tuple[int, int, int],
    explain_targets: list[dict[str, Any]],
    random_seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not explain_targets:
        raise ValueError("mixed-workload requires at least one explain target")

    explain_count, ingest_count, query_count = allocate_counts(request_count, mix)
    entity_search_count = query_count // 2
    admin_stats_count = query_count - entity_search_count

    operations: list[dict[str, Any]] = []
    for index in range(explain_count):
        target = explain_targets[index % len(explain_targets)]
        operations.append(
            {
                "operation": "explain",
                "target": target["target"],
                "payload": target["payload"],
            }
        )
    for index in range(ingest_count):
        operations.append(
            {
                "operation": "ingest",
                "request_index": index,
            }
        )
    for index in range(entity_search_count):
        operations.append(
            {
                "operation": "entity_search",
                "query": QUERY_SEARCH_TERMS[index % len(QUERY_SEARCH_TERMS)],
            }
        )
    for _ in range(admin_stats_count):
        operations.append({"operation": "admin_stats"})

    random.Random(random_seed).shuffle(operations)

    return operations, {
        "input": f"{mix[0]},{mix[1]},{mix[2]}",
        "weights": {
            "explain": mix[0],
            "ingest": mix[1],
            "query": mix[2],
        },
        "planned_counts": {
            "explain": explain_count,
            "ingest": ingest_count,
            "entity_search": entity_search_count,
            "admin_stats": admin_stats_count,
        },
    }


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def parse_mix(value: str) -> tuple[int, int, int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise ValueError("mix must contain exactly three comma-separated integers")
    mix = tuple(int(part) for part in parts)
    if any(part < 0 for part in mix):
        raise ValueError("mix values must be non-negative")
    if sum(mix) <= 0:
        raise ValueError("mix values must sum to a positive integer")
    return mix


def allocate_counts(total: int, weights: tuple[int, ...]) -> list[int]:
    remaining = total
    allocated: list[int] = []
    weight_sum = sum(weights)
    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            allocated.append(remaining)
            break
        count = total * weight // weight_sum
        allocated.append(count)
        remaining -= count
    return allocated


def build_response_result(
    response: httpx.Response,
    operation: str,
    from_cache: bool = False,
) -> dict[str, Any]:
    result = {
        "operation": operation,
        "status_code": response.status_code,
        "ok": response.is_success,
        "from_cache": from_cache,
    }
    if not response.is_success:
        result["error"] = response.text[:300]
    return result


def build_failed_result(operation: str, error: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "status_code": None,
        "ok": False,
        "error": error,
        "from_cache": False,
    }


def build_metric_summary(latencies_ms: list[float]) -> dict[str, float]:
    return {
        "avg": round(mean(latencies_ms), 2) if latencies_ms else 0.0,
        "p50": round(percentile(latencies_ms, 0.50), 2),
        "p95": round(percentile(latencies_ms, 0.95), 2),
        "p99": round(percentile(latencies_ms, 0.99), 2),
        "max": round(max(latencies_ms), 2) if latencies_ms else 0.0,
    }


def build_operation_stats() -> dict[str, dict[str, Any]]:
    return {
        operation: {
            "requests": 0,
            "success_count": 0,
            "failure_count": 0,
            "latencies_ms_raw": [],
            "from_cache_count": 0,
        }
        for operation in MIXED_OPERATIONS
    }


def finalize_operation_stats(stats: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    finalized: dict[str, dict[str, Any]] = {}
    for operation, values in stats.items():
        requests = values["requests"]
        summary = {
            "requests": requests,
            "success_count": values["success_count"],
            "failure_count": values["failure_count"],
            "latency_ms": build_metric_summary(values["latencies_ms_raw"]),
        }
        if operation == "explain":
            summary["from_cache_count"] = values["from_cache_count"]
            summary["from_cache_rate"] = (
                round(values["from_cache_count"] / requests, 4) if requests else 0.0
            )
        finalized[operation] = summary
    return finalized


async def fetch_stats(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("admin/stats")
    response.raise_for_status()
    return response.json()


async def clear_cache(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post("admin/cache/clear")
    response.raise_for_status()
    return response.json()


async def seed_smoke_data(client: httpx.AsyncClient) -> dict[str, Any]:
    session_id = f"loadtest-smoke-{uuid.uuid4().hex[:8]}"
    response = await client.post("events/batch", json=make_smoke_events(session_id))
    response.raise_for_status()
    return {"session_id": session_id, "events": response.json()}


async def seed_mixed_targets(
    client: httpx.AsyncClient,
    seed_count: int,
) -> dict[str, Any]:
    explain_targets: list[dict[str, Any]] = []
    for index in range(seed_count):
        suffix = f"{index:04d}"
        session_id = f"mixed-seed-{suffix}"
        response = await client.post(
            "events/batch",
            json=make_seed_smoke_events(session_id, suffix),
        )
        response.raise_for_status()
        explain_targets.extend(make_explain_targets(suffix))
    return {
        "seed_count": seed_count,
        "explain_targets": explain_targets,
    }


async def _resolve_entity_id_by_qname(
    client: httpx.AsyncClient,
    qualified_name: str,
) -> str:
    response = await client.get("entities/search", params={"q": qualified_name})
    response.raise_for_status()
    entities = response.json()
    for entity in entities:
        if entity.get("qualified_name") == qualified_name:
            return str(entity["entity_id"])
    if entities:
        return str(entities[0]["entity_id"])
    raise RuntimeError(f"unable to resolve entity id for {qualified_name}")


async def seed_summary_targets(
    client: httpx.AsyncClient,
    target_count: int,
) -> dict[str, Any]:
    entity_ids: list[str] = []
    seed_index = 0

    while len(entity_ids) < target_count:
        suffix = f"{seed_index:04d}"
        session_id = f"summary-seed-{suffix}"
        response = await client.post(
            "events/batch",
            json=make_seed_smoke_events(session_id, suffix),
        )
        response.raise_for_status()
        names = build_seed_names(suffix)
        entity_ids.append(
            await _resolve_entity_id_by_qname(client, names["fetch_api_name"])
        )
        seed_index += 1

    return {
        "seed_count": seed_index,
        "entity_ids": entity_ids,
    }


async def seed_mixed_graph_targets(
    client: httpx.AsyncClient,
    target_count: int,
) -> dict[str, Any]:
    entity_ids: list[str] = []
    seed_index = 0

    while len(entity_ids) < target_count:
        suffix = f"{seed_index:04d}"
        session_id = f"impact-mixed-seed-{suffix}"
        file_path = f"pkg_{suffix}/service.py"
        class_name = f"Service_{suffix}"
        response = await client.post(
            "events/batch",
            json=[
                {
                    "action_type": "create",
                    "file_path": f"pkg_{suffix}/base.py",
                    "code_snapshot": (
                        "class BaseHandler:\n"
                        "    def normalize(self, value):\n"
                        "        return value.strip()\n"
                    ),
                    "intent": "create base handler",
                    "session_id": session_id,
                },
                {
                    "action_type": "create",
                    "file_path": file_path,
                    "code_snapshot": (
                        f"from pkg_{suffix}.base import BaseHandler\n"
                        f"import pkg_{suffix}.base as base_mod\n\n"
                        f"class {class_name}(BaseHandler):\n"
                        "    def run(self, value):\n"
                        "        helper = base_mod.BaseHandler()\n"
                        "        return helper.normalize(value)\n"
                    ),
                    "intent": "create service with inheritance and import bridge",
                    "session_id": session_id,
                },
            ],
        )
        response.raise_for_status()
        entity_ids.append(
            await _resolve_entity_id_by_qname(client, class_name)
        )
        seed_index += 1

    return {
        "seed_count": seed_index,
        "entity_ids": entity_ids,
    }


async def run_explain_request(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post("explain", json=payload)
    from_cache = False
    if response.headers.get("content-type", "").startswith("application/json"):
        body = response.json()
        explanations = body.get("explanations") or []
        if explanations:
            from_cache = bool(explanations[0].get("from_cache"))
    return build_response_result(response, operation="explain", from_cache=from_cache)


async def warm_mixed_targets(
    client: httpx.AsyncClient,
    explain_targets: list[dict[str, Any]],
) -> list[str]:
    semaphore = asyncio.Semaphore(min(2, max(1, len(explain_targets))))
    warmup_cache_keys: list[str] = []
    
    async def warm_one(target: dict[str, Any]) -> str:
        async with semaphore:
            response = await client.post("explain", json=target["payload"])
            response.raise_for_status()
            body = response.json()
            explanation = body["explanations"][0]
            return (
                f"{explanation['entity_id']}:"
                f"{target['payload']['detail_level']}:"
                f"{target['payload']['include_relations']}"
            )

    warmup_cache_keys.extend(await asyncio.gather(*(warm_one(target) for target in explain_targets)))
    return warmup_cache_keys


async def warm_hot_cache(client: httpx.AsyncClient) -> dict[str, Any]:
    seed_result = await seed_smoke_data(client)
    await clear_cache(client)
    payload = {
        "query": "fetch_api_data",
        "cursor_word": "fetch_api_data",
        "detail_level": "trace",
        "include_relations": True,
    }
    cold_response = await run_explain_request(client, payload)
    warm_response = await run_explain_request(client, payload)
    return {
        "seed_session_id": seed_result["session_id"],
        "payload": payload,
        "cold_from_cache": cold_response["from_cache"],
        "warm_from_cache": warm_response["from_cache"],
    }


async def run_ingest_request(client: httpx.AsyncClient, request_index: int) -> dict[str, Any]:
    run_id = f"{request_index}_{uuid.uuid4().hex[:8]}"
    response = await client.post("events/batch", json=make_ingest_payload(run_id))
    return build_response_result(response, operation="ingest")


async def run_hot_cache_request(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await run_explain_request(client, payload)


async def run_summary_request(
    client: httpx.AsyncClient,
    entity_id: str,
) -> dict[str, Any]:
    response = await client.get(f"explain/{entity_id}/summary")
    from_cache = False
    if response.headers.get("content-type", "").startswith("application/json"):
        body = response.json()
        from_cache = bool(body.get("from_cache"))
    return build_response_result(response, operation="summary", from_cache=from_cache)


async def run_impact_paths_request(
    client: httpx.AsyncClient,
    entity_id: str,
) -> dict[str, Any]:
    response = await client.get(
        f"relations/{entity_id}/impact-paths",
        params={"direction": "both", "limit": 3},
    )
    return build_response_result(response, operation="impact-paths")


async def run_entity_search_request(
    client: httpx.AsyncClient,
    query: str,
) -> dict[str, Any]:
    response = await client.get("entities/search", params={"q": query})
    return build_response_result(response, operation="entity_search")


async def run_admin_stats_request(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("admin/stats")
    return build_response_result(response, operation="admin_stats")


async def run_scenario(
    client: httpx.AsyncClient,
    scenario: str,
    request_count: int,
    concurrency: int,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)
    latencies_ms: list[float] = []
    success_count = 0
    failure_count = 0
    from_cache_count = 0
    failures: list[dict[str, Any]] = []

    async def one_request(index: int) -> None:
        nonlocal success_count, failure_count, from_cache_count
        start = time.perf_counter()
        async with semaphore:
            try:
                if scenario == "ingest":
                    result = await run_ingest_request(client, index)
                elif scenario == "summary":
                    if payload is None:
                        raise RuntimeError("summary scenario missing entity_ids payload")
                    entity_ids = payload["entity_ids"]
                    result = await run_summary_request(client, entity_ids[index])
                elif scenario in {"impact-paths", "impact-paths-mixed"}:
                    if payload is None:
                        raise RuntimeError("impact-paths scenario missing entity_ids payload")
                    entity_ids = payload["entity_ids"]
                    result = await run_impact_paths_request(client, entity_ids[index])
                else:
                    if payload is None:
                        raise RuntimeError("scenario payload is missing")
                    result = await run_hot_cache_request(client, payload)
            except Exception as exc:  # noqa: BLE001
                if scenario == "ingest":
                    operation = "ingest"
                elif scenario == "summary":
                    operation = "summary"
                elif scenario in {"impact-paths", "impact-paths-mixed"}:
                    operation = "impact-paths"
                else:
                    operation = "explain"
                result = build_failed_result(operation=operation, error=str(exc))
        latency_ms = (time.perf_counter() - start) * 1000
        latencies_ms.append(latency_ms)
        if result["ok"]:
            success_count += 1
            if result.get("from_cache"):
                from_cache_count += 1
        else:
            failure_count += 1
            failures.append(
                {
                    "index": index,
                    "status_code": result.get("status_code"),
                    "error": result.get("error"),
                }
            )

    wall_start = time.perf_counter()
    await asyncio.gather(*(one_request(index) for index in range(request_count)))
    wall_seconds = time.perf_counter() - wall_start

    return {
        "scenario": scenario,
        "requests": request_count,
        "concurrency": concurrency,
        "wall_seconds": round(wall_seconds, 3),
        "throughput_rps": round(request_count / wall_seconds, 2) if wall_seconds else 0.0,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": round(success_count / request_count, 4) if request_count else 0.0,
        "latency_ms": build_metric_summary(latencies_ms),
        "from_cache_count": from_cache_count,
        "failures": failures[:10],
    }


async def run_mixed_workload(
    client: httpx.AsyncClient,
    operations: list[dict[str, Any]],
    concurrency: int,
) -> tuple[dict[str, Any], dict[str, int], list[dict[str, Any]]]:
    semaphore = asyncio.Semaphore(concurrency)
    latencies_ms: list[float] = []
    success_count = 0
    failure_count = 0
    by_operation = build_operation_stats()
    actual_counts = {operation: 0 for operation in MIXED_OPERATIONS}
    failures: list[dict[str, Any]] = []

    async def one_request(index: int, plan_item: dict[str, Any]) -> None:
        nonlocal success_count, failure_count
        operation = plan_item["operation"]
        start = time.perf_counter()
        async with semaphore:
            try:
                if operation == "explain":
                    result = await run_explain_request(client, plan_item["payload"])
                elif operation == "ingest":
                    result = await run_ingest_request(client, plan_item["request_index"])
                elif operation == "entity_search":
                    result = await run_entity_search_request(client, plan_item["query"])
                else:
                    result = await run_admin_stats_request(client)
            except Exception as exc:  # noqa: BLE001
                result = build_failed_result(operation=operation, error=str(exc))
        latency_ms = (time.perf_counter() - start) * 1000

        latencies_ms.append(latency_ms)
        actual_counts[operation] += 1
        by_operation[operation]["requests"] += 1
        by_operation[operation]["latencies_ms_raw"].append(latency_ms)

        if result["ok"]:
            success_count += 1
            by_operation[operation]["success_count"] += 1
            if operation == "explain" and result.get("from_cache"):
                by_operation[operation]["from_cache_count"] += 1
        else:
            failure_count += 1
            by_operation[operation]["failure_count"] += 1
            failures.append(
                {
                    "index": index,
                    "operation": operation,
                    "status_code": result.get("status_code"),
                    "error": result.get("error"),
                }
            )

    wall_start = time.perf_counter()
    await asyncio.gather(
        *(one_request(index, plan_item) for index, plan_item in enumerate(operations))
    )
    wall_seconds = time.perf_counter() - wall_start

    result = {
        "scenario": "mixed-workload",
        "requests": len(operations),
        "concurrency": concurrency,
        "wall_seconds": round(wall_seconds, 3),
        "throughput_rps": round(len(operations) / wall_seconds, 2) if wall_seconds else 0.0,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": round(success_count / len(operations), 4) if operations else 0.0,
        "latency_ms": build_metric_summary(latencies_ms),
        "by_operation": finalize_operation_stats(by_operation),
    }
    return result, actual_counts, failures[:10]


def write_summary(summary: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def wait_for_health(base_url: str, timeout: float) -> None:
    deadline = time.perf_counter() + timeout
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(3.0),
        trust_env=False,
    ) as client:
        while time.perf_counter() < deadline:
            try:
                response = await client.get("admin/health")
                if response.is_success:
                    return
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError("spawned app did not become healthy in time")


def spawn_app_process(
    output_dir: Path,
    host: str,
    port: int,
    db_path: str,
) -> subprocess.Popen[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / f"server-{port}.out.log"
    stderr_path = output_dir / f"server-{port}.err.log"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    stdout_file = stdout_path.open("w", encoding="utf-8")
    stderr_file = stderr_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tailevents.main",
            "--db-path",
            db_path,
            "--host",
            host,
            "--port",
            str(port),
        ],
        stdout=stdout_file,
        stderr=stderr_file,
        text=True,
        env=env,
    )


def stop_app_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def async_main() -> None:
    args = parse_args()
    mix = parse_mix(args.mix)
    base_url = args.base_url.rstrip("/") + "/"
    if args.spawn_app:
        base_url = f"http://{args.app_host}:{args.app_port}/api/v1/"

    output_path = (
        Path(args.output)
        if args.output
        else DEFAULT_OUTPUT_DIR / f"{args.scenario}-{int(time.time())}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    process: Optional[subprocess.Popen[str]] = None
    process_logs: dict[str, Any] = {}
    summary: dict[str, Any]

    if args.spawn_app:
        process = spawn_app_process(
            output_dir=output_path.parent,
            host=args.app_host,
            port=args.app_port,
            db_path=args.db_path,
        )
        process_logs = {
            "pid": process.pid,
            "stdout": str((output_path.parent / f"server-{args.app_port}.out.log").as_posix()),
            "stderr": str((output_path.parent / f"server-{args.app_port}.err.log").as_posix()),
        }
        await wait_for_health(base_url, timeout=args.timeout)

    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(args.timeout),
            trust_env=False,
        ) as client:
            before_stats = await fetch_stats(client)
            setup: dict[str, Any] = {}

            if args.scenario == "mixed-workload":
                seed_setup = await seed_mixed_targets(client, args.seed_count)
                await clear_cache(client)
                warmup_cache_keys = await warm_mixed_targets(
                    client,
                    seed_setup["explain_targets"],
                )
                operations, planned_mix = build_mixed_operations(
                    request_count=args.requests,
                    mix=mix,
                    explain_targets=seed_setup["explain_targets"],
                    random_seed=args.random_seed,
                )
                result, actual_counts, top_failures = await run_mixed_workload(
                    client=client,
                    operations=operations,
                    concurrency=args.concurrency,
                )
                after_stats = await fetch_stats(client)
                seed_summary = {
                    "seed_count": args.seed_count,
                    "explain_target_count": len(seed_setup["explain_targets"]),
                    "warmup_cache_keys": warmup_cache_keys,
                }
                setup = {
                    "seed_count": args.seed_count,
                    "random_seed": args.random_seed,
                }
                summary = {
                    "base_url": base_url,
                    "scenario": args.scenario,
                    "spawn_app": args.spawn_app,
                    "process": process_logs,
                    "before_stats": before_stats,
                    "setup": setup,
                    "planned_mix": planned_mix,
                    "actual_counts": actual_counts,
                    "seed_summary": seed_summary,
                    "top_failures": top_failures,
                    "result": result,
                    "after_stats": after_stats,
                }
            else:
                scenario_payload: Optional[dict[str, Any]] = None
                if args.scenario == "summary":
                    target_count = max(args.seed_count, args.requests)
                    setup = await seed_summary_targets(client, target_count)
                    scenario_payload = setup
                elif args.scenario == "impact-paths":
                    target_count = max(args.seed_count, args.requests)
                    setup = await seed_summary_targets(client, target_count)
                    scenario_payload = setup
                elif args.scenario == "impact-paths-mixed":
                    target_count = max(args.seed_count, args.requests)
                    setup = await seed_mixed_graph_targets(client, target_count)
                    scenario_payload = setup
                elif args.scenario == "hot-cache-explain":
                    setup = await warm_hot_cache(client)
                    scenario_payload = setup["payload"]

                result = await run_scenario(
                    client=client,
                    scenario=args.scenario,
                    request_count=args.requests,
                    concurrency=args.concurrency,
                    payload=scenario_payload,
                )
                after_stats = await fetch_stats(client)
                summary = {
                    "base_url": base_url,
                    "scenario": args.scenario,
                    "spawn_app": args.spawn_app,
                    "process": process_logs,
                    "before_stats": before_stats,
                    "setup": setup,
                    "result": result,
                    "after_stats": after_stats,
                }
    finally:
        if process is not None:
            stop_app_process(process)
    write_summary(summary, output_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary_written={output_path}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
