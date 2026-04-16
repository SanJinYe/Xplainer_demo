# TailEvents Coding Explanation Agent

TailEvents 是一个面向 AI 编码会话的可解释性后端与 VSCode 扩展组合。它记录结构化编码事件，按代码实体建立索引，并在需要时生成解释；同时也开始支持一个最小但真实的 coding agent 闭环。

## 当前状态

- Requirement A 后端闭环已完成并可运行
- `vscode-extension/` 已从 explain-only MVP 演进到 `B-next`：
  - `Explain` 模式：hover、侧边栏 explanation、history、related entities
  - `Code` 模式：backend 编排的 `view -> edit -> verify -> Apply`
  - `Step Transcript`、`Model Output`、`Verified Draft` 三块输出
- 支持三种 LLM backend：
  - `ollama`
  - `claude`
  - `openrouter`

## 核心链路

```text
Coding Agent / VSCode Extension
  -> IngestionPipeline
  -> Event Store (SQLite)
  -> Indexer (AST)
  -> Entity DB + Relation Store
  -> QueryRouter
  -> ExplanationEngine (LLM)
  -> Cache
  -> FastAPI
```

`B-next` 的 coding task 链路是：

```text
VSCode Code Mode
  -> POST /api/v1/coding/tasks
  -> backend task session
  -> view_file tool call
  -> view -> edit -> verify
  -> verified draft
  -> Apply
  -> POST /api/v1/events
  -> existing ingestion / explain chain
```

## 主要目录

- `tailevents/models/`
- `tailevents/config/`
- `tailevents/storage/`
- `tailevents/cache/`
- `tailevents/indexer/`
- `tailevents/explanation/`
- `tailevents/query/`
- `tailevents/api/`
- `tailevents/graph/`（stub）
- `tailevents/ingestion/`
- `tailevents/coding/`
- `vscode-extension/`

## 主要 API

说明类接口：

- `/api/v1/events`
- `/api/v1/entities`
- `/api/v1/explain`
- `/api/v1/relations`
- `/api/v1/admin`

`B-next` coding task 接口：

- `POST /api/v1/coding/tasks`
- `GET /api/v1/coding/tasks/{task_id}/stream`
- `POST /api/v1/coding/tasks/{task_id}/tool-result`
- `POST /api/v1/coding/tasks/{task_id}/cancel`

## 环境变量

项目读取仓库根目录 `.env`。

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

## 运行方式

安装依赖：

```bash
pip install -r requirements.txt
```

启动后端：

```bash
python -m tailevents.main
```

或：

```bash
uvicorn tailevents.main:app --host 127.0.0.1 --port 8766
```

启动后访问：

- `http://127.0.0.1:8766/docs`
- `http://127.0.0.1:8766/redoc`

## VSCode 手动调试

基础流程：

1. 启动后端：

   ```powershell
   cd vscode-extension
   npm run test:manual:backend
   ```

2. 编译 extension 并准备基础 explain 数据：

   ```powershell
   cd vscode-extension
   npm run test:manual:prepare
   ```

3. 在仓库根目录按 `F5 -> Run Extension`

当前 Extension Host 使用独立 workspace 文件：

- [.vscode/extension-host.code-workspace](.vscode/extension-host.code-workspace)

主要手测样例：

- [manual_test_target.py](vscode-extension/manual_test_target.py)
- [manual_test_complex_target.py](vscode-extension/manual_test_complex_target.py)

`Code` 模式当前固定包含三块输出：

- `Step Transcript`
  - 只显示 task / status / tool_call / view / edit / verify
- `Model Output`
  - 只显示模型原始 token 流
- `Verified Draft`
  - 只显示 verify 成功后的最终 draft

手测时最关键的检查点是：

- `Run` 后要同时看到 transcript 和 model token 流
- 只有 verified draft 出来后 `Apply` 才可点击
- no-op edit 必须显示在 `edit/failed`
- 成功 `Apply` 后才会写最终 `RawEvent`
- 对复杂样例，在首次成功 `Apply` 前如果 explain 返回 404，这属于预期

## 当前验证结果

目标自动测试当前通过：

- `cd vscode-extension && npm test`
  - `31 passing`
- `.\.venv\Scripts\python.exe -m pytest tests/test_coding.py tests/test_api.py -q`
  - `15 passed`

说明：

- 这两组是当前 `B-next` 的目标测试
- 不代表根目录所有历史测试都在当前 shell 环境下全绿

当前已经人工验证过的行为包括：

- hover summary
- `TailEvents: Explain Current Symbol`
- explanation sidebar 的 history / related entities
- `Code` 模式下的 `Run / Cancel / Apply`
- `view -> edit -> verify -> final RawEvent` 闭环

## 当前边界

当前 `B-next` 仍然只做：

- 单文件编辑
- 最多 2 个显式只读上下文文件
- backend 编排 loop
- 人工 `Apply`

当前不做：

- 多文件编辑
- repo 级自主搜索
- task history 页面
- 多模型 profile UI
- MCP / skills

## 参考

- [docs/requirements.md](docs/requirements.md)
- [docs/system_design.md](docs/system_design.md)
