# Current Progress

## Current State

TailEvents Requirement A backend is complete and runnable. The end-to-end backend path is implemented:

`Ingestion -> Event Store -> Indexer -> Entity/Relation Store -> Query Router -> Explanation Engine -> Cache -> FastAPI`

Implemented modules:

- `tailevents/models/`
- `tailevents/config/`
- `tailevents/storage/`
- `tailevents/cache/`
- `tailevents/indexer/`
- `tailevents/explanation/`
- `tailevents/query/`
- `tailevents/api/`
- `tailevents/graph/` (stub)
- `tailevents/ingestion/`

## Implemented Capabilities

- Capture `RawEvent` inputs and persist normalized `TailEvent` records into SQLite.
- Index Python functions, classes, methods, imports, calls, and inheritance relations via the AST-based indexer.
- Track entity lifecycle across create / modify / rename flows with stable `entity_id`.
- Resolve explanations by location, symbol, or free-text query through `QueryRouter`.
- Generate structured explanations with cache support through `ExplanationEngine`.
- Expose the full backend through FastAPI routes under `/api/v1`.
- Provide a local HTTP load-test runner at `scripts/loadtest.py` for:
  - `ingest`
  - `hot-cache-explain`
  - `mixed-workload`
- Support three LLM backends:
  - `ollama`
  - `claude`
  - `openrouter`

## Validated Behavior

Automated validation completed:

- Current full regression baseline: `39 passed`.
- Script validation passed:
  - `python -m py_compile scripts/loadtest.py`

Manual end-to-end validation completed through Swagger:

- Ingested a 5-event coding session covering create, modify, and rename.
- Verified entity creation and rename continuity:
  - `fetch_data -> fetch_api_data` kept the same `entity_id`.
- Verified relation extraction:
  - `DataProcessor.process -> fetch_api_data` produced an active `calls` relation.
- Verified explanation generation with structured output.
- Verified explanation cache hit / miss behavior.
- Verified admin stats and session event lookup behavior.

Load-test validation completed locally:

- `ingest` baseline and mid-size runs completed without failures.
- `hot-cache-explain` baseline and mid-size runs completed without failures.
- `mixed-workload` smoke run completed without failures.
- Planned `mixed-workload` baseline and mid-size runs completed without failures:
  - baseline: `100 requests / 10 concurrency`
  - mid-size: `300 requests / 20 concurrency`
- `mixed-workload` validation confirmed:
  - exact `70/20/10` request mix
  - `explain.from_cache_rate = 1.0`
  - per-operation latency reporting for `explain`, `ingest`, `entity_search`, and `admin_stats`

## Runtime and Entry Points

Supported startup methods:

- `python -m tailevents.main`
- `uvicorn tailevents.main:app`

Configuration source:

- repo-root `.env`
- settings class: `tailevents/config/settings.py`

## Known Implementation Deviations

- The application entry point is implemented at `tailevents/main.py` instead of a repository-root `main.py`, matching the user-requested runtime shape.
- `tailevents/graph/` remains a stub by design; no Requirement B graph analysis is implemented.
- Cache hit/miss stats are runtime in-memory counters and reset on process restart or cache clear.
- `CodeEntity.tags` temporarily stores hidden rename-detection metadata (`body_hash` / normalized body) instead of introducing a dedicated schema field.

## Current Repository Notes

- `README.md` is updated to describe current startup, configuration, usage, and debugging flow.
- `TEST_TUTORIAL.md` documents the manual Swagger-based validation flow.
- OpenRouter support has been integrated into config, LLM client selection, and `.env.example`.
- `scripts/loadtest.py` provides reproducible local pressure tests and writes JSON summaries into `loadtest-results/` (ignored by Git).
