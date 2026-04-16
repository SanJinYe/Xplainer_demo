# TailEvents Coding Explanation Agent

TailEvents 是一个面向 AI 编码会话的后端与 VS Code 扩展组合。

当前仓库只公开已经实现的功能，不描述未来规划。

## 当前功能

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

当前 explanation 默认行为：
- `summary` 使用独立短摘要 prompt
- 默认 detailed 使用四段结构：
  - `核心作用`
  - `关键上下文`
  - `关键事件`
  - `关联实体`
- `summary` 强制限制为 `<= 2` 句且 `<= 120` 中文字
- detailed 总长强制限制为 `<= 1200` 中文字

### Coding Task

```text
VS Code Code Mode
  -> POST /api/v1/coding/tasks
  -> backend task session
  -> view_file tool call
  -> view -> edit -> verify
  -> verified draft
  -> Apply
  -> POST /api/v1/events
```

当前 `Code` 模式支持：
- 单轮任务
- 单文件编辑
- 最多 `2` 个显式只读 context files
- `Run / Cancel / Apply`
- `Step Transcript`
- `Model Output`
- `Verified Draft`

### Baseline Onboarding

```text
TailEvents: Onboard Repository
  -> POST /api/v1/baseline/onboard-file
  -> IngestionPipeline
  -> Indexer
  -> Entity / Relation Store
```

当前 onboarding 约束：
- 只处理当前 workspace 下的 `.py` 文件
- 同路径同内容 baseline 不重复写入
- 已有真实 trace 的文件会被跳过
- 单文件大小限制为 `<= 512 KB`

## 目录

- `tailevents/`
- `vscode-extension/`
- `scripts/`

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

### Coding task

- `POST /api/v1/coding/tasks`
- `GET /api/v1/coding/tasks/{task_id}/stream`
- `POST /api/v1/coding/tasks/{task_id}/tool-result`
- `POST /api/v1/coding/tasks/{task_id}/cancel`

### Baseline onboarding

- `POST /api/v1/baseline/onboard-file`

### Admin

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

## 启动

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

## 当前边界

当前系统只支持：
- 单文件 coding task
- 人工 `Apply`
- 当前 explanation 默认路径
- Python baseline onboarding

当前仓库不公开设计文档、规划文档、测试与手测资产。
