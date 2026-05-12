# RECORD

## 2026-05-12 - wrapper contract first

### Context

- Branch: `codex/wrapper-contract-first-v1`.
- Worktree: `C:\Users\16089\demo-worktrees\wrapper-contract-first-v1`.
- The local branch did not contain `AGENTS.md`, `CURRENT_PROGRESS.md`,
  `RECORD.md`, `docs/requirements.md`, or `docs/system_design.md` at session
  start, so the user-provided project rules were treated as the active
  contract.
- `tailevents/models/protocols.py` was inspected and did not require changes.

### Changes

- Added an internal host-agnostic normalized event boundary in
  `tailevents/host_adapters/normalized.py`.
- Updated the Cline adapter so Cline wire messages are first normalized into
  `NormalizedHostEvent` and only then converted to `RawEvent`.
- Kept the existing `/api/v1/host/cline/events` API shape intact.
- Added tests for normalized host events and for the full synthetic wrapper
  path: Cline task messages -> host API -> ingestion -> indexing ->
  explanation summary -> impact paths.

### Validation

- `C:\Users\16089\demo\.venv\Scripts\python.exe -m pytest tests\test_cline_host_adapter.py tests\test_cline_trace_spike.py`
  - Result: 7 passed.
- `C:\Users\16089\demo\.venv\Scripts\python.exe -m pytest`
  - Result: 24 passed.
- Real Cline latest task read-only conversion:
  - Command: `C:\Users\16089\demo\.venv\Scripts\python.exe scripts\cline_trace_spike.py --workspace-root C:\Users\16089\demo-worktrees\wrapper-contract-first-v1`
  - Result: task `1777391397778`, 19 messages, 2 tools, 1 file change, 1 raw event, 1 read observation, 0 skipped.
- Real Cline latest task API post through this worktree backend:
  - Backend used this worktree `.env` and port `8906`.
  - Command: `scripts\cline_trace_spike.py --workspace-root C:\Users\16089\demo-worktrees\wrapper-contract-first-v1 --post --base-url http://127.0.0.1:8906`
  - Result: HTTP 201, `posted_count=1`.
  - Note: the first post attempt was intercepted by the local proxy; setting
    process-level `NO_PROXY=127.0.0.1,localhost` fixed it.
- Extension/webview compile and unit tests were not run because no extension or
  webview files changed.

### Conclusion

BRANCH_COMPLETE: the wrapper path now has a minimal host-agnostic adapter /
normalizer boundary while preserving the Cline-first API path.
