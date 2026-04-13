# CONTEXT.md

## Project Status

- **Current Phase**: 1 (基础层)
- **Completed Modules**: none
- **Next Step**: Generate `models/` (enums, event, entity, relation, explanation, protocols)

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
- Date: [TBD]
- Module: models + config
- Notes: [TBD]
- Deviations from design: [TBD]

---

*Update this file at the end of every coding session.*
