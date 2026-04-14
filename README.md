# TailEvents Coding Explanation Agent

TailEvents 是一个面向 AI 编码会话的可解释性后端。它接收编码代理产生的结构化事件，按函数 / 类 / 方法建立索引，并在查询时生成“这段代码做什么、为什么这样写、和谁有关”的解释。

## 当前状态

- Requirement A 后端闭环已完成
- 已实现模块：
  - `models`
  - `config`
  - `storage`
  - `cache`
  - `indexer`
  - `explanation`
  - `query`
  - `api`
  - `graph`（stub）
  - `ingestion`
- 当前服务入口：
  - `python -m tailevents.main`
  - `uvicorn tailevents.main:app`

## 系统链路

```text
Coding Agent
  -> IngestionPipeline
  -> Event Store (SQLite)
  -> Indexer (AST)
  -> Entity DB + Relation Store
  -> QueryRouter
  -> ExplanationEngine (LLM)
  -> Cache
  -> FastAPI
```

## 主要目录

### `tailevents/models/`

- `enums.py`：动作、实体、关系等枚举
- `event.py`：`RawEvent`、`TailEvent`、`EntityRef`、`ExternalRef`
- `entity.py`：`CodeEntity`、`ParamInfo`、`EventRef`、`RenameRecord`
- `relation.py`：`Relation`
- `explanation.py`：`ExplanationRequest`、`EntityExplanation`、`ExplanationResponse`
- `protocols.py`：模块间 Protocol 契约

### `tailevents/config/`

- `settings.py`：Pydantic `Settings`
- `defaults.py`：默认值
- `__init__.py`：`get_settings()`

### `tailevents/storage/`

- `database.py`：`aiosqlite` 连接管理、`initialize_db()`
- `migrations.py`：SQLite schema、索引、FTS5
- `event_store.py`
- `entity_db.py`
- `relation_store.py`

### `tailevents/indexer/`

- AST 提取 entity / relation / import
- unified diff 解析
- rename 检测
- pending queue
- 主 `Indexer`

### `tailevents/explanation/`

- prompt 模板
- Ollama / Claude / OpenRouter LLM client
- 本地 `pydoc` 文档检索
- context assembly
- formatter
- `ExplanationEngine`

### `tailevents/query/`

- `LocationResolver`
- `SymbolResolver`
- `QueryRouter`

### `tailevents/api/`

- `/api/v1/events`
- `/api/v1/entities`
- `/api/v1/explain`
- `/api/v1/relations`
- `/api/v1/admin`

### `tailevents/ingestion/`

- `RawEventValidator`
- `IngestionPipeline`
- `LoggingHook`
- `GraphUpdateHook`

### `tailevents/graph/`

- `stub.py`：正式 stub 实现
- `graph_service_stub.py`：兼容 shim

## 环境变量

项目只读取**当前仓库根目录**下的 `.env`。

### 1. 本地 Ollama

```env
TAILEVENTS_LLM_BACKEND=ollama
TAILEVENTS_OLLAMA_BASE_URL=http://100.115.45.10:11434
TAILEVENTS_OLLAMA_MODEL=qwen3:32b
TAILEVENTS_PROXY_URL=http://127.0.0.1:7897
```

### 2. OpenRouter

```env
TAILEVENTS_LLM_BACKEND=openrouter
TAILEVENTS_OPENROUTER_API_KEY=你的_openrouter_key
TAILEVENTS_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
TAILEVENTS_OPENROUTER_MODEL=openai/gpt-5.4
TAILEVENTS_OPENROUTER_SITE_URL=
TAILEVENTS_OPENROUTER_APP_NAME=TailEvents
TAILEVENTS_PROXY_URL=http://127.0.0.1:7897
```

说明：

- `TAILEVENTS_OPENROUTER_SITE_URL` 是可选，会映射到 `HTTP-Referer`
- `TAILEVENTS_OPENROUTER_APP_NAME` 是可选，会映射到 `X-Title`
- 留空不影响基本调用

### 3. Claude

```env
TAILEVENTS_LLM_BACKEND=claude
TAILEVENTS_CLAUDE_API_KEY=你的_claude_key
TAILEVENTS_CLAUDE_MODEL=claude-sonnet-4-20250514
TAILEVENTS_PROXY_URL=http://127.0.0.1:7897
```

## 运行方式

安装依赖：

```bash
pip install -r requirements.txt
```

直接启动：

```bash
python -m tailevents.main
```

带参数启动：

```bash
python -m tailevents.main --db-path ./tailevents.db --host 127.0.0.1 --port 8766
```

或用 Uvicorn：

```bash
uvicorn tailevents.main:app --host 127.0.0.1 --port 8766
```

启动后可访问：

- `http://127.0.0.1:8766/docs`
- `http://127.0.0.1:8766/redoc`

## 最小使用流程

### 1. 写入一个事件

```bash
curl -X POST http://127.0.0.1:8766/api/v1/events ^
  -H "Content-Type: application/json" ^
  -d "{\"action_type\":\"create\",\"file_path\":\"api.py\",\"code_snapshot\":\"def fetch_data(url):\n    return url\n\",\"intent\":\"create fetch helper\"}"
```

### 2. 查实体

```bash
curl "http://127.0.0.1:8766/api/v1/entities"
```

### 3. 查解释

```bash
curl -X POST http://127.0.0.1:8766/api/v1/explain ^
  -H "Content-Type: application/json" ^
  -d "{\"query\":\"fetch_data\",\"detail_level\":\"detailed\",\"include_relations\":true}"
```

## 调试方法

### 1. 最推荐的断点位置

- 启动入口：[tailevents/main.py](/c:/Users/16089/demo/tailevents/main.py:1)
- 依赖装配：[tailevents/api/dependencies.py](/c:/Users/16089/demo/tailevents/api/dependencies.py:148)
- 事件入口：[tailevents/api/routes/events.py](/c:/Users/16089/demo/tailevents/api/routes/events.py:21)
- 摄取主流程：[tailevents/ingestion/pipeline.py](/c:/Users/16089/demo/tailevents/ingestion/pipeline.py:28)
- Indexer 主入口：[tailevents/indexer/indexer.py](/c:/Users/16089/demo/tailevents/indexer/indexer.py:27)
- 解释主流程：[tailevents/explanation/engine.py](/c:/Users/16089/demo/tailevents/explanation/engine.py:30)
- Query 路由：[tailevents/query/router.py](/c:/Users/16089/demo/tailevents/query/router.py:13)

### 2. VS Code / PyCharm 调试

把运行目标设成模块：

```text
tailevents.main
```

参数填：

```text
--db-path ./tailevents.db --host 127.0.0.1 --port 8766
```

### 3. 如果服务启动了但结果不对

- 事件写入正常但没有实体：
  - 先看 `IngestionPipeline`
  - 再看 `Indexer.process_event()`
- 有实体但解释为空：
  - 看 `ExplanationEngine.explain_entity()`
  - 再看当前 `.env` 里的 LLM backend 和 key
- 路由解析不对：
  - 看 `QueryRouter`
  - 再看 `LocationResolver` / `SymbolResolver`

### 4. 常见问题

- `.env` 改了但没生效：
  - 确认改的是当前仓库根目录 `.env`
  - 重启进程
- PowerShell 报 `profile.ps1` 执行策略错误：
  - 用 `powershell -NoProfile` 跑命令
  - 这和项目逻辑无关
- OpenRouter key 填了但还是报错：
  - 确认 `TAILEVENTS_LLM_BACKEND=openrouter`
  - 确认 `TAILEVENTS_OPENROUTER_MODEL` 不是空
  - 确认代理可用

## 测试

当前测试包括：

- `tests/test_storage.py`
- `tests/test_indexer.py`
- `tests/test_explanation.py`
- `tests/test_api.py`
- `tests/test_ingestion.py`
- `tests/test_main.py`
- `tests/test_integration.py`

常用回归命令：

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_explanation.py -q
```

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_integration.py -q
```

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_integration.py tests/test_ingestion.py tests/test_main.py tests/test_api.py tests/test_explanation.py tests/test_storage.py tests/test_indexer.py -q
```

## 参考

- `docs/requirements.md`
- `docs/system_design.md`
- `AGENTS.md`
- `CONTEXT.md`
