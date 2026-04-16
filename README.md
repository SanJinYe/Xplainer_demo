# TailEvents Coding Explanation Agent

TailEvents 是一个面向 AI 编码会话的可解释性后端与 VS Code 扩展组合。

它当前已经不是单纯的 Requirement A backend，而是三条线并行推进的工作基座：
- Requirement A 后端主链路已完成并可运行
- `B-next` 最小真实 coding agent 已落地
- `C1` baseline onboarding 已落地
- `A1` explanation 收缩第一轮工程实现已落地

## 当前能力

### 解释链路

```text
RawEvent / Baseline Event
  -> IngestionPipeline
  -> Event Store (SQLite)
  -> Indexer (AST)
  -> Entity DB + Relation Store
  -> QueryRouter
  -> ExplanationEngine (LLM)
  -> Cache
  -> FastAPI
```

当前 explanation 默认路径：
- `summary` 使用独立短摘要 prompt
- 默认 detailed 为四段：
  - `核心作用`
  - `关键上下文`
  - `关键事件`
  - `关联实体`
- `summary` 强制 `<= 2` 句且 `<= 120` 中文字
- detailed 强制分段硬上限，总长 `<= 1200` 中文字

### B-next coding task 链路

```text
VS Code Code Mode
  -> POST /api/v1/coding/tasks
  -> backend task session
  -> view_file tool call
  -> view -> edit -> verify
  -> verified draft
  -> Apply
  -> POST /api/v1/events
  -> existing explanation chain
```

当前 `Code` 模式固定包含：
- prompt 输入
- 目标文件
- 最多 `2` 个只读 context files
- `Run / Cancel / Apply`
- `Step Transcript`
- `Model Output`
- `Verified Draft`

### C1 baseline onboarding 链路

```text
TailEvents: Onboard Repository
  -> POST /api/v1/baseline/onboard-file
  -> IngestionPipeline
  -> Indexer
  -> Entity / Relation Store
```

当前 C1 约束：
- 只处理当前 workspace 下的 `.py` 文件
- 同路径同内容 baseline 不重复写入
- 已有真实 trace 的文件会被跳过
- 本地与后端都限制单文件 `<= 512 KB`

## 目录结构

- `tailevents/models/`
- `tailevents/config/`
- `tailevents/storage/`
- `tailevents/cache/`
- `tailevents/indexer/`
- `tailevents/explanation/`
- `tailevents/query/`
- `tailevents/api/`
- `tailevents/ingestion/`
- `tailevents/coding/`
- `tailevents/graph/`：stub
- `vscode-extension/`

## 主要 API

### 基础 explanation / indexing

- `POST /api/v1/events`
- `POST /api/v1/events/batch`
- `GET /api/v1/events`
- `GET /api/v1/entities`
- `GET /api/v1/entities/search`
- `POST /api/v1/explain`
- `GET /api/v1/explain/{entity_id}`
- `GET /api/v1/explain/{entity_id}/summary`
- `GET /api/v1/relations/{entity_id}/incoming`
- `GET /api/v1/relations/{entity_id}/outgoing`

### B-next coding task

- `POST /api/v1/coding/tasks`
- `GET /api/v1/coding/tasks/{task_id}/stream`
- `POST /api/v1/coding/tasks/{task_id}/tool-result`
- `POST /api/v1/coding/tasks/{task_id}/cancel`

### C1 baseline onboarding

- `POST /api/v1/baseline/onboard-file`

### Admin / local debug

- `GET /api/v1/admin/stats`
- `GET /api/v1/admin/health`
- `POST /api/v1/admin/cache/clear`
- `POST /api/v1/admin/reindex`
- `POST /api/v1/admin/reset-state`

## 环境变量

项目从仓库根目录 `.env` 读取配置。

### Ollama

```env
TAILEVENTS_LLM_BACKEND=ollama
TAILEVENTS_OLLAMA_BASE_URL=http://100.115.45.10:11434
TAILEVENTS_OLLAMA_MODEL=qwen3:32b
TAILEVENTS_PROXY_URL=http://127.0.0.1:7897
```

### OpenRouter

```env
TAILEVENTS_LLM_BACKEND=openrouter
TAILEVENTS_OPENROUTER_API_KEY=your_openrouter_key
TAILEVENTS_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
TAILEVENTS_OPENROUTER_MODEL=openai/gpt-5.4
TAILEVENTS_OPENROUTER_SITE_URL=
TAILEVENTS_OPENROUTER_APP_NAME=TailEvents
TAILEVENTS_PROXY_URL=http://127.0.0.1:7897
```

### Claude

```env
TAILEVENTS_LLM_BACKEND=claude
TAILEVENTS_CLAUDE_API_KEY=your_claude_key
TAILEVENTS_CLAUDE_MODEL=claude-sonnet-4-20250514
TAILEVENTS_PROXY_URL=http://127.0.0.1:7897
```

## 启动方式

安装依赖：

```bash
pip install -r requirements.txt
```

启动后端：

```bash
python -m tailevents.main
```

或者：

```bash
uvicorn tailevents.main:app --host 127.0.0.1 --port 8766
```

Swagger:
- `http://127.0.0.1:8766/docs`
- `http://127.0.0.1:8766/redoc`

## VS Code 手测入口

手测脚本位于 `vscode-extension/scripts/`。

常用入口：

```powershell
cd vscode-extension
npm run test:manual:backend
npm run test:manual:prepare
```

然后在仓库根目录按 `F5 -> Run Extension`。

当前 Extension Host 使用独立 workspace：
- [.vscode/extension-host.code-workspace](.vscode/extension-host.code-workspace)

主要手测 fixture：
- [manual_test_target.py](vscode-extension/manual_test_target.py)
- [manual_test_complex_target.py](vscode-extension/manual_test_complex_target.py)

## 最近验证

本轮已验证：
- `.\.venv\Scripts\python.exe -m pytest tests/test_explanation.py tests/test_api.py tests/test_e2e_smoke.py -q`
  - `32 passed`

此前已验证：
- `.\.venv\Scripts\python.exe -m pytest tests/test_ingestion.py tests/test_api.py -q`
  - `21 passed`
- `cd vscode-extension && npm test`
  - `37 passing`

## 当前边界

当前系统仍然只做：
- 单文件 coding task
- 最多 `2` 个显式只读 context files
- 人工 `Apply`
- explanation 默认路径的工程收缩

当前尚未做：
- repo 级搜索和多文件规划
- panel 流式 explanation
- baseline-aware explanation 文案层
- GraphRAG / 全局影响路径

## 参考

- [docs/requirements.md](docs/requirements.md)
- [docs/system_design.md](docs/system_design.md)
- [NEXT_PHASE_TASK.md](NEXT_PHASE_TASK.md)
