# AGENTS.md

## Project: TailEvents Coding Explanation Agent

### What This Is

A system that captures structured trace events ("TailEvents") from AI coding agents during vibe coding sessions, indexes them by code entity (function/class/method), and generates on-demand explanations of what each piece of code does, why it was written, and how it relates to the rest of the codebase.

### Architecture Summary

```
Coding Agent → Ingestion → Event Store (append-only)
                   ↓
               Indexer (AST) → Entity DB (key=entity_id) + Relation Store
                                          ↓
               Query Router → Explanation Engine (LLM) → Cache
                                          ↓
                                    API Server (FastAPI)
```

Full design: see `docs/system_design.md`
Requirements & rationale: see `docs/requirements.md`

### Module Map

| Module | Path | Status |
|--------|------|--------|
| models | `tailevents/models/` | ✅ |
| config | `tailevents/config/` | ✅ |
| storage | `tailevents/storage/` | ✅ |
| indexer | `tailevents/indexer/` | ✅ |
| cache | `tailevents/cache/` | ✅ |
| explanation | `tailevents/explanation/` | ✅ |
| query | `tailevents/query/` | ✅ |
| api | `tailevents/api/` | ✅ |
| graph (stub) | `tailevents/graph/` | ✅ |
| ingestion | `tailevents/ingestion/` | ✅ |

Update status to ✅ as each module is completed.

---

## Coding Conventions

### Python

- Python 3.11+
- **Fully async**: all I/O operations use `async/await`. Storage uses `aiosqlite`.
- Type hints on every function signature. Use `Optional[X]` not `X | None` for 3.11 compatibility.
- Pydantic v2 for all data models. Use `model_dump()` not `.dict()`.
- Import style: absolute imports from project root (`from tailevents.models.event import TailEvent`).
- Docstrings: Google style, concise. No docstring filler — if the function name is self-explanatory, a one-liner is fine.
- Error handling: define custom exceptions in each module's `__init__.py` or a dedicated `exceptions.py`. Never silently swallow exceptions.

### Database

- Single SQLite file at configurable path (default `./tailevents.db`).
- Use `aiosqlite` for all database access.
- All JSON fields stored as TEXT, serialized/deserialized in the storage layer.
- Schema creation in `storage/migrations.py` — idempotent `CREATE TABLE IF NOT EXISTS`.
- Foreign keys enabled (`PRAGMA foreign_keys = ON`).
- WAL mode enabled for concurrent reads (`PRAGMA journal_mode = WAL`).

### Module Interface Pattern

Every module exposes its functionality through a Protocol (defined in `models/protocols.py`). Concrete implementations are injected via constructor. This enables testing with mocks and future replacement of implementations.

```python
# Good: depend on protocol
class ExplanationEngine:
    def __init__(self, entity_db: EntityDBProtocol, ...): ...

# Bad: depend on concrete class
class ExplanationEngine:
    def __init__(self, entity_db: SQLiteEntityDB, ...): ...
```

### Testing

- Use `pytest` + `pytest-asyncio`.
- Each module has a `tests/` directory or tests at project root in `tests/{module}/`.
- Storage tests use an in-memory SQLite (`:memory:`).
- No external service dependencies in unit tests — mock LLM calls.

### File Naming

- Snake_case for all Python files.
- One primary class per file (file named after the class in snake_case).
- `__init__.py` re-exports the module's public interface.

---

## Environment

- **OS**: Windows 11 workstation
- **GPU**: RTX 5090 (for local LLM inference)
- **Local LLM**: Qwen3:32b via Ollama at `100.115.45.10:11434` (Tailscale)
- **Proxy**: Clash at `127.0.0.1:7897`. Set `NO_PROXY=100.115.45.10` for Ollama.
- **Python venv**: `C:\Users\16089\agent\.venv` (or project-local venv)

### LLM Configuration

The system supports multiple LLM backends. Default is local Ollama. Configuration via `.env`:

```
TAILEVENTS_LLM_BACKEND=ollama
TAILEVENTS_OLLAMA_BASE_URL=http://100.115.45.10:11434
TAILEVENTS_OLLAMA_MODEL=qwen3:32b
```

### Dependencies (requirements.txt)

```
fastapi>=0.110
uvicorn>=0.29
aiosqlite>=0.20
pydantic>=2.0
pydantic-settings>=2.0
httpx>=0.27
```

---

## Rules for the Coding Agent

1. **Read the design doc first.** Before writing any module, read `docs/system_design.md` sections relevant to that module. The design doc contains the SQLite schema, Protocol interfaces, and detailed logic descriptions. Follow them.

2. **Don't invent new interfaces.** The Protocol definitions in `models/protocols.py` are the contract. If you think an interface needs changing, flag it in CONTEXT.md rather than silently diverging.

3. **Don't implement Requirement B.** The graph module is a stub only. Don't build graph analysis, community detection, or GraphRAG features. Only implement the `GraphServiceStub` with `NotImplementedError` or empty returns.

4. **Don't build frontend.** No VSCode extension code. The API server is the boundary — frontend will consume it later.

5. **Keep modules independent.** Each module should be testable in isolation. If you find yourself importing from a module that isn't listed as a dependency for your current module, stop and reconsider.

6. **Update CONTEXT.md after every session.** Record what was completed, any deviations from the design, and what the next session should pick up.

7. **Handle the half-finished code case.** The Indexer must gracefully handle AST parse failures. This is not an edge case — it will happen regularly. Use the pending queue mechanism described in the design.

8. **JSON serialization in storage.** List and dict fields in Pydantic models are stored as JSON TEXT in SQLite. The storage layer handles serialization/deserialization. Don't leak JSON handling into other modules.

9. **Proxy configuration matters.** When making HTTP requests to Ollama (Tailscale IP), ensure NO_PROXY is respected. When making requests to external APIs (Claude, docs), use the proxy. The `httpx` client should be configured accordingly in `explanation/llm_client.py`.

10. **Chinese + English bilingual.** Code and comments in English. User-facing explanations can be in Chinese or English depending on the query — this is handled by the prompt template, not hardcoded.
