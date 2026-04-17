# TailEvents

TailEvents is a local-first backend plus VS Code extension for explaining code from coding-agent traces.

It records structured change events, maps them to code entities with AST indexing, and serves two explanation paths:

- fast summaries for hover
- streamed detailed explanations for the sidebar

The current repository only keeps the implemented product surface. Design notes, plans, local progress logs, and tests stay out of version control.

## What It Does

- Ingests append-only coding events into SQLite
- Builds and updates entity and relation indexes from Python code
- Resolves symbols and cursor locations to entities
- Returns deterministic low-latency summaries for hover
- Streams detailed explanations over SSE for the sidebar
- Tracks explanation telemetry in admin stats
- Supports baseline onboarding for existing Python files
- Exposes a minimal coding-task loop in the VS Code extension:
  `view -> edit -> verify -> Apply -> event`

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
  -> VS Code Extension
```

## Main User Flows

### Explain

- Hover asks for a summary and returns immediately from cached description or deterministic event-derived text
- Sidebar opens an SSE stream and renders:
  - init metadata and summary first
  - detailed explanation deltas progressively
  - final explanation on `done`

### Code

- The extension starts a coding task on the backend
- The backend drives a constrained tool loop
- Only a verified draft can be applied
- Accepted edits are written back as new events

### Baseline

- `TailEvents: Onboard Repository` scans Python files in the current workspace
- Each file is posted to the backend as a baseline event
- Indexed entities become explainable even before real traced edits exist

## Key API Endpoints

- `POST /api/v1/events`
- `POST /api/v1/events/batch`
- `GET /api/v1/entities/by-location`
- `GET /api/v1/explain/{entity_id}`
- `GET /api/v1/explain/{entity_id}/summary`
- `GET /api/v1/explain/{entity_id}/stream`
- `GET /api/v1/events/for-entity/{entity_id}`
- `POST /api/v1/coding/tasks`
- `GET /api/v1/coding/tasks/{task_id}/stream`
- `POST /api/v1/baseline/onboard-file`
- `GET /api/v1/admin/stats`

## Stack

- Python 3.11+
- FastAPI
- aiosqlite
- Pydantic v2
- VS Code Extension API
- Ollama, Claude, or OpenRouter as the explanation backend

## Quick Start

Install dependencies:

```bash
pip install -r requirements.txt
cd vscode-extension && npm install
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
- Coding tasks are still single-file oriented
- Graph analysis is still a stub
- Streamed detailed explanation is implemented, but multi-model routing is only reserved in config and not enabled by default

## License

This repository is licensed under the MIT License.
