# CONTEXT.md

## Project Status

- **Current Phase**: Complete (Requirement A integration done)
- **Completed Modules**: models, config, storage, indexer, cache, explanation, query, api, graph, ingestion
- **Next Step**: Frontend consumer work, release packaging, or selective warning cleanup

## Design Documents

- `docs/requirements.md` — 需求总纲（why & what）
- `docs/system_design.md` — 系统设计（how）
- `AGENTS.md` — 编码规范和全局指令

## Design Decisions (Locked)

These decisions have been made and should not be revisited:

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Entity DB primary key | UUID (`ent_xxxx`) | Stable across renames; `qualified_name` is mutable index |
| Storage backend | Single SQLite file | Simple, sufficient for single-user; Protocol abstraction allows future swap |
| Async model | Full async (`aiosqlite`, `httpx`) | Consistent with existing FastAPI middleware codebase |
| Event structure | Append-only, immutable after Indexer enrichment | Clean separation of concerns |
| Index strategy | No event splitting; Entity DB is an inverted index over events | Simpler than sub-events, avoids data duplication |
| Rename tracking | UUID stable ID + rename_history list on CodeEntity | Handles agent's frequent rename/move operations |
| LLM backend priority | Ollama (Qwen3:32b) local → Claude API fallback | Minimize latency and cost |
| Graph service | Stub only in current phase | Interfaces defined in Protocol, implementation deferred to Phase 4 |
| QueryRouter vs Engine 边界 | QueryRouter 独占查询解析，Engine 只接受 entity_ids | 消除职责重叠 |
| include_relations 传递 | `explain_entity()` 增加 `include_relations` 参数，从 Request 透传 | 断裂修复 |
| 外部 docs 触发 | 看 events 的 `external_refs`，不看 entity 的 `is_external` | 覆盖内部函数调外部包的场景 |
| Event enrichment | 允许一次 `entity_refs` 回填，用独立 `enrich()` 方法 | 保持 append-only 语义 |
| description 字段 | 统一为 `cached_description` + `description_valid` | 无独立 description |
| 主键口径 | `entity_id(UUID)` 为主键，全文档统一 | `requirements.md` 需修正 |

## Environment Constants

```
OS: Windows 11
Python: 3.11+ in venv
Ollama: http://100.115.45.10:11434 (Tailscale, Qwen3:32b)
Proxy: http://127.0.0.1:7897 (Clash)
NO_PROXY: 100.115.45.10
API port: 8766 (avoid conflict with existing middleware on 8765)
DB path: ./tailevents.db
```

## Implementation Log

### Session 1
- Date: 2026-04-13
- Module: design docs alignment
- Notes: Synced locked decisions across `requirements.md`, `system_design.md`, and `CONTEXT.md`; no code generated.
- Deviations from design: none

### Session 2
- Date: 2026-04-13
- Module: models + config
- Notes: Generated `tailevents.models`, `tailevents.config`, `.env.example`, and aligned package exports.
- Deviations from design: `requirements.txt` kept existing workspace dependencies (`openai`, `requests`, `langgraph`) in addition to the design doc baseline.

### Session 3
- Date: 2026-04-13
- Module: README
- Notes: Added root `README.md` describing the current Phase 1 deliverables and next step.
- Deviations from design: none

### Session 4
- Date: 2026-04-13
- Module: storage
- Notes: Generated SQLite connection manager, migrations, event/entity/relation stores, and storage tests. Storage tests passed with the project `.venv`.
- Deviations from design: added `tailevents/storage/exceptions.py` for explicit storage-layer exceptions; `SQLiteEntityDB.upsert()` uses `ON CONFLICT DO UPDATE` instead of `INSERT OR REPLACE` to avoid foreign-key breakage when relations already reference an entity.

### Session 5
- Date: 2026-04-13
- Module: indexer + cache
- Notes: Generated SQLite-backed cache, AST-based indexer pipeline, and tests for cache, AST extraction, relations, rename detection, diff parsing, and pending queue behavior. Tests passed with the project `.venv`.
- Deviations from design: `CodeEntity.tags` temporarily stores hidden `body_hash` / normalized-body metadata for rename detection, avoiding schema changes in Phase 3.

### Session 6
- Date: 2026-04-13
- Module: README refresh
- Notes: Updated `README.md` to reflect completed Phase 1-3 modules and current Phase 4 status.
- Deviations from design: none

### Session 7
- Date: 2026-04-13
- Module: explanation
- Notes: Generated `tailevents/explanation`, added prompt templates, LLM clients, local doc retriever, context assembly, formatter, explanation engine, and unit tests. Updated indexer cache invalidation prefix to match the explanation cache key scheme. Tests passed with the project `.venv`.
- Deviations from design: added `tailevents/explanation/exceptions.py` for explicit explanation-layer exceptions.

### Session 8
- Date: 2026-04-13
- Module: query + api + graph stub
- Notes: Generated `tailevents/query`, `tailevents/api`, and `tailevents/graph`, added a FastAPI app with dependency wiring, minimal event ingestion adapter, admin endpoints, cache runtime stats, and API/query integration tests. Tests passed with the project `.venv`.
- Deviations from design: kept ingestion logic as an API-internal adapter instead of a standalone `ingestion/` module in this phase; implemented admin reindex in the API/container layer by clearing index-side tables and replaying stored events; cache hit/miss metrics are in-memory only and reset on process restart or cache clear.

### Session 9
- Date: 2026-04-13
- Module: ingestion + main entry + graph stub alignment
- Notes: Generated `tailevents/ingestion`, moved API event ingestion onto the formal pipeline, aligned `tailevents/graph/stub.py`, added `tailevents/main.py`, and added ingestion/main/integration tests. Full suite passed with the project `.venv` (`33 passed`).
- Deviations from design: implemented the entry point at `tailevents/main.py` instead of a repository-root `main.py` per the user’s explicit request; kept `tailevents/graph/graph_service_stub.py` as a compatibility shim that forwards to `tailevents/graph/stub.py`.

### Session 10
- Date: 2026-04-13
- Module: README refresh
- Notes: Rewrote the root `README.md` to reflect the completed Requirement A backend, current runnable entry points, implemented modules, and test coverage.
- Deviations from design: none

### Session 11
- Date: 2026-04-14
- Module: openrouter support
- Notes: Added `openrouter` as an LLM backend option, including config fields, `OpenRouterLLMClient`, factory wiring, `.env.example` entries, and targeted unit tests. Also set `env_ignore_empty=True` in `Settings` so blank optional values in `.env` no longer break startup.
- Deviations from design: none

### Session 12
- Date: 2026-04-14
- Module: README debug usage refresh
- Notes: Rewrote `README.md` to document current startup methods, `.env` usage, OpenRouter / Ollama / Claude configuration, minimal API flow, and practical debugging entry points.
- Deviations from design: none

### Session 13
- Date: 2026-04-14
- Module: manual testing tutorial
- Notes: Added `TEST_TUTORIAL.md` with a Swagger-based manual testing flow that simulates a 5-event coding session end-to-end, including entity checks, relation checks, explanation queries, rename verification, and cache-hit verification.
- Deviations from design: none

### Session 14
- Date: 2026-04-14
- Module: repository cleanup + progress snapshot
- Notes: Tightened `.gitignore` to exclude SQLite files and temp test directories, removed tracked Python cache artifacts from the repository, cleaned local runtime leftovers, and added `CURRENT_PROGRESS.md` as a current-state snapshot without next-step planning. The uncommitted OpenRouter support, README refresh, and manual testing tutorial were also included in the repo update.
- Deviations from design: none

### Session 15
- Date: 2026-04-14
- Module: baseline confirmation + explanation quality convergence
- Notes: Fixed the OpenRouter factory test to ignore repo `.env` pollution, added `tests/test_e2e_smoke.py` for domain-layer ingestion/index/explanation/cache coverage, introduced grouped relation context in explanation prompts, hardened formatter parsing for backtick-wrapped and multiline parameter blocks, versioned explanation cache keys with `EXPLANATION_PROMPT_VERSION = "v2"`, generated `explanation_prompt_audit.md`, recorded real-LLM validation in `explanation_quality_check.md`, and regenerated `regression_report.txt` with a green full-suite baseline (`39 passed, 0 skipped, 0 failed`).
- Deviations from design: added a small formatter robustness improvement for multiline parameter blocks based on validation findings; no API or schema changes.

### Session 16
- Date: 2026-04-15
- Module: load testing
- Notes: Added `scripts/loadtest.py` to run repeatable HTTP load tests against the FastAPI app with two scenarios: `ingest` and `hot-cache-explain`. The script can spawn a temporary local app process, seed smoke data, clear/warm explanation cache, collect latency/throughput/success metrics, and write JSON summaries under `loadtest-results/`. Ran baseline and mid-size local load tests successfully. Observed that `ingest` scales with noticeably higher latency under concurrency while `hot-cache-explain` remains fast with a near-100% cache-hit path after warmup.
- Deviations from design: none; this session added tooling only and did not change application behavior.

### Session 17
- Date: 2026-04-15
- Module: mixed workload load testing
- Notes: Extended `scripts/loadtest.py` with a new `mixed-workload` scenario plus CLI options for `--mix`, `--seed-count`, and `--random-seed`. Added unique seed-code generation so mixed-workload can prebuild a stable pool of explain targets, warm their caches, execute a deterministic `70/20/10` explain/write/query mix, and report per-operation latency and cache-hit metrics. Validated the script with a small smoke run and with the planned baseline (`100/10`) and mid-size (`300/20`) mixed-workload runs using spawned local app instances.
- Deviations from design: increased the script's default per-request timeout to 120 seconds and limited mixed-workload cache warmup to a small concurrency of 2 so the planned default commands can complete against the current real LLM backend.

---

*Update this file at the end of every coding session.*
