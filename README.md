# TailEvents

TailEvents is a local-first backend plus VS Code extension for explaining Python code from coding-agent traces and supporting a history-first coding workspace.

It records structured change events, maps them to code entities with AST indexing, and serves:

- fast summaries for hover
- streamed detailed explanations for the sidebar
- baseline-aware history provenance and structured caller/callee context
- typed global impact paths plus bounded subgraph summaries in detailed explanations
- dual-source external doc retrieval for explanations (`pydoc` + authorized workspace docs)
- persistent coding-task history, replay preparation, verified drafts, and apply confirmation
- separate effective `Code` and `Explain` profile selection, with `Explain` following `Code` by default
- a React-based VS Code webview with dedicated `Explain`, `Code`, and `History` views plus a secondary `ProfilePanel`

The repository keeps the shipped product surface only. Local plans, tests, progress logs, and private notes stay out of version control.

## What It Does

- Ingests append-only coding events into SQLite
- Builds and updates entity and relation indexes from Python code
- Resolves symbols and cursor locations to entities
- Returns deterministic low-latency summaries for hover
- Streams detailed explanations over SSE for the sidebar
- Labels explanation history as baseline-only, mixed, or traced-only
- Shows structured caller/callee context in the sidebar instead of free-form related-entity text
- Serves bounded `subgraph` and `impact-paths` graph queries for repo-local entity relations
- Supports AST-derived fallback `external_refs` for external calls and inheritance
- Syncs authorized workspace docs into SQLite FTS for explanation-time retrieval
- Tracks explanation telemetry in admin stats
- Supports baseline onboarding for existing Python files
- Exposes a backend-orchestrated coding loop:
  `view -> edit -> verify -> Apply -> event`
- Persists coding-task history with pagination, filtering, target-path suggestions, detail review, replay lineage, verified per-file drafts, and apply-event state
- Supports coding-profile sync from the extension plus backend environment fallback profiles
- Exposes coding capability discovery for repo observation and multi-file tasks

## Runtime Shape

```text
Coding Agent / Baseline Onboarding
  -> Ingestion
  -> Event Store
  -> Indexer
  -> Entity Store + Relation Store
  -> Query Router
  -> Explanation Engine
  -> FastAPI
  -> VS Code Extension Host
  -> React Webview UI
```

## Main User Flows

### Explain

- Hover asks for a summary and returns immediately from cached description or deterministic event-derived text
- Hover adds a lightweight baseline tag only when the entity comes purely from baseline history
- Sidebar opens an SSE stream and renders:
  - init metadata and summary first
  - detailed explanation deltas progressively
  - final explanation on `done`
- Sidebar and hover both use the effective `Explain` profile; `Explain` follows `Code` unless explicitly overridden
- Explanation APIs accept `profile_id` and return `resolved_profile_id`
- Sidebar shows a baseline/mixed disclaimer when explanation history is not fully traced
- Sidebar renders `Who calls this` and `What this calls` from structured relation data
- Sidebar renders `Global Impact` as a bounded best-effort upstream/downstream path summary
- Sidebar renders `External Docs` from the explanation payload

### Code

- The extension starts a backend-orchestrated coding task from a workspace Python target, defaulting to the active file but allowing an explicit target plus up to 3 readonly context files and 1 additional editable file
- The extension webview is split into `Explain`, `Code`, and `History` views, while the extension host keeps backend/API orchestration and state aggregation and exposes profiles through a secondary `ProfilePanel`
- The `Code` view now uses a chat-native conversation surface: user turns, assistant working turns, assistant results, and assistant errors are the primary UI, while reasoning, tool trace, and file changes live behind expandable details
- Target control still supports explicit `Use Explain File as Target` and `Back to Explain Entity`, but the active editor is treated as the default hint instead of the main visible workflow control
- The backend drives a constrained tool loop and returns verified drafts per file
- Only a verified draft can be applied
- Accepted drafts are written back through one workspace edit and then confirmed to the backend for event persistence
- Task history supports:
  - paginated recent-task review
  - status filters
  - recent target-path suggestions plus exact target filtering
  - incremental `Load More`
  - prompt-preview task cards plus summary-first detail review
  - detail inspection for prompt, context, transcript, model output, verified draft, reasoning, apply status, and structured step history
  - `Reuse Prompt/Context`
  - `Replay Task` preparation with lineage metadata
- Replay-aware tasks surface a compact lineage badge and can jump back to the source task within the loaded history slice
- Reuse keeps the current target while restoring prompt/context; replay restores target, context, editable files, and lineage metadata
- Profiles are managed from the extension through:
  - `TailEvents: Manage Profiles`
  - `TailEvents: Select Code Profile`
  - `TailEvents: Select Explain Profile`

### Baseline

- `TailEvents: Onboard Repository` is available from the TailEvents sidebar title and scans Python files in the current workspace
- Each file is posted to the backend as a baseline event
- Indexed entities become explainable even before real traced edits exist

### Docs

- `TailEvents: Manage Authorized Docs` lets the extension choose workspace `.md` / `.txt` files to authorize for explanation-time retrieval
- The extension snapshots the selected docs and syncs them to the backend
- Detailed explanations can then pull at most a small bounded set of matching doc snippets alongside `pydoc` results

## Key API Endpoints

- `POST /api/v1/events`
- `POST /api/v1/events/batch`
- `GET /api/v1/entities/by-location`
- `POST /api/v1/explain`
- `GET /api/v1/explain/{entity_id}`
- `GET /api/v1/explain/{entity_id}/summary`
- `GET /api/v1/explain/{entity_id}/stream`
- `GET /api/v1/relations/{entity_id}/subgraph`
- `GET /api/v1/relations/{entity_id}/impact-paths`
- `GET /api/v1/events/for-entity/{entity_id}`
- `POST /api/v1/coding/tasks`
- `GET /api/v1/coding/tasks/history`
- `GET /api/v1/coding/tasks/history/targets`
- `GET /api/v1/coding/tasks/{task_id}`
- `GET /api/v1/coding/tasks/{task_id}/stream`
- `POST /api/v1/coding/tasks/{task_id}/applied`
- `POST /api/v1/coding/tasks/{task_id}/retry-events`
- `POST /api/v1/coding/tasks/{task_id}/cancel`
- `POST /api/v1/profiles/sync`
- `GET /api/v1/profiles/status`
- `GET /api/v1/coding/capabilities`
- `POST /api/v1/docs/sync`
- `POST /api/v1/baseline/onboard-file`
- `GET /api/v1/admin/stats`

## Stack

- Python 3.11+
- FastAPI
- aiosqlite
- Pydantic v2
- VS Code Extension API
- React 18 + Vite + Tailwind for the extension webview
- Ollama, Claude, or OpenRouter as the explanation backend

## Quick Start

Install dependencies:

```bash
pip install -r requirements.txt
npm --prefix vscode-extension install
npm --prefix vscode-extension/webview-ui install
```

Set environment variables in `.env` as needed. Common local setup:

```env
TAILEVENTS_LLM_BACKEND=ollama
TAILEVENTS_OLLAMA_BASE_URL=http://100.115.45.10:11434
TAILEVENTS_OLLAMA_MODEL=qwen3:32b
```

Run the backend:

```bash
python -m tailevents.main
```

Or:

```bash
uvicorn tailevents.main:app --host 127.0.0.1 --port 8766
```

For extension development, open the repo in VS Code and start the extension host with the local launch configuration.

## Current Boundaries

- Python is the only indexed language today
- Profile definitions and selection are still command-driven; the sidebar shows effective state but does not provide an inline profile editor
- The React webview is now the default sidebar shell, but `tailEvents.legacyWebview` still exists as a temporary rollback switch
- The shipped `Code` surface is now conversation-first on the frontend, but the backend runtime is still target-file-first and has not yet moved to autonomous scope selection
- `mcp` and `skills` are capability placeholders and currently report `not implemented in Phase 4`
- Graph support is intentionally limited to `subgraph` and `impact-paths`; graph cache, community detection, cycle reports, and importance ranking are not shipped
- The current `Global Impact` surface is best-effort and bounded; deeper graph semantics are intentionally deferred
- External docs are intentionally limited to `pydoc` plus authorized workspace `.md` / `.txt` files; there is no network retrieval
- Repo-scale autonomous observation and broader multi-round task planning are not shipped yet

## License

This repository is licensed under the MIT License.
