# CONTEXT.md

## Project Status

- **Current Phase**: 2 (存储层)
- **Completed Modules**: models, config
- **Next Step**: Generate `storage/` (database, migrations, event_store, entity_db, relation_store)

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
- Deviations from design: none

### Session 3
- Date: 2026-04-13
- Module: README
- Notes: Added root `README.md` describing the current Phase 1 deliverables and next step.
- Deviations from design: none

---

*Update this file at the end of every coding session.*
