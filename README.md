# TailEvents Coding Explanation Agent

TailEvents 是一个面向 AI 编码会话的可解释性后端。它接收编码代理产生的结构化事件，按函数、类、方法建立索引，并在查询时生成“这段代码做什么、为什么这样写、和谁有关”的解释。

## 当前状态

- Requirement A 后端闭环已完成
- 当前入口：
  - `python -m tailevents.main`
  - `uvicorn tailevents.main:app`
- 当前模块：
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
- 当前全量回归基线：`39 passed`
- 当前压测脚本：`scripts/loadtest.py`

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

项目只读取当前仓库根目录下的 `.env`。

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

- `TAILEVENTS_OPENROUTER_SITE_URL` 可选，会映射到 `HTTP-Referer`
- `TAILEVENTS_OPENROUTER_APP_NAME` 可选，会映射到 `X-Title`

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

启动后访问：

- `http://127.0.0.1:8766/docs`
- `http://127.0.0.1:8766/redoc`

## 冒烟测试

推荐直接在 Swagger `/docs` 里手工验证。

### 1. 写入 5 个事件

对 `POST /api/v1/events/batch` 发送：

```json
[
  {
    "action_type": "create",
    "file_path": "data_processor.py",
    "line_range": [1, 3],
    "code_snapshot": "def fetch_data(url, timeout=5.0):\n    \"\"\"Fetch raw API data.\"\"\"\n    return request_remote(url, timeout=timeout)\n",
    "intent": "create fetch_data to isolate remote API access",
    "reasoning": "start with a small helper before building the processing flow",
    "session_id": "manual-app-smoke-001"
  },
  {
    "action_type": "modify",
    "file_path": "data_processor.py",
    "line_range": [1, 6],
    "code_snapshot": "def fetch_data(url, timeout=5.0):\n    \"\"\"Fetch raw API data.\"\"\"\n    try:\n        return request_remote(url, timeout=timeout)\n    except Exception:\n        return {\"items\": [], \"error\": \"upstream_failed\"}\n",
    "intent": "add error handling to fetch_data",
    "reasoning": "return a safe fallback when the remote API fails",
    "decision_alternatives": [
      "raise the exception to the caller",
      "return None"
    ],
    "session_id": "manual-app-smoke-001"
  },
  {
    "action_type": "rename",
    "file_path": "data_processor.py",
    "line_range": [1, 6],
    "code_snapshot": "def fetch_api_data(url, timeout=5.0):\n    \"\"\"Fetch raw API data.\"\"\"\n    try:\n        return request_remote(url, timeout=timeout)\n    except Exception:\n        return {\"items\": [], \"error\": \"upstream_failed\"}\n",
    "intent": "rename fetch_data to fetch_api_data",
    "reasoning": "make the helper name explicit before other callers depend on it",
    "session_id": "manual-app-smoke-001"
  },
  {
    "action_type": "create",
    "file_path": "data_processor.py",
    "line_range": [1, 11],
    "code_snapshot": "def fetch_api_data(url, timeout=5.0):\n    \"\"\"Fetch raw API data.\"\"\"\n    try:\n        return request_remote(url, timeout=timeout)\n    except Exception:\n        return {\"items\": [], \"error\": \"upstream_failed\"}\n\nclass DataProcessor:\n    def process(self, url):\n        raw = fetch_api_data(url)\n        return raw.get(\"items\", [])\n",
    "intent": "create DataProcessor.process to call fetch_api_data",
    "reasoning": "keep processing separate while reusing the fetch helper",
    "session_id": "manual-app-smoke-001"
  },
  {
    "action_type": "modify",
    "file_path": "data_processor.py",
    "line_range": [1, 14],
    "code_snapshot": "import logging\n\ndef fetch_api_data(url, timeout=5.0):\n    \"\"\"Fetch raw API data.\"\"\"\n    try:\n        return request_remote(url, timeout=timeout)\n    except Exception:\n        return {\"items\": [], \"error\": \"upstream_failed\"}\n\nclass DataProcessor:\n    def process(self, url):\n        logging.info(\"processing url=%s\", url)\n        raw = fetch_api_data(url)\n        return raw.get(\"items\", [])\n",
    "intent": "add logging to DataProcessor.process",
    "reasoning": "record processing requests without mixing logging into the fetch helper",
    "session_id": "manual-app-smoke-001"
  }
]
```

### 2. 解释 `fetch_api_data`

对 `POST /api/v1/explain` 发送：

```json
{
  "query": "fetch_api_data",
  "cursor_word": "fetch_api_data",
  "detail_level": "trace",
  "include_relations": true
}
```

### 3. 解释 `DataProcessor.process`

```json
{
  "query": "DataProcessor.process",
  "cursor_word": "DataProcessor.process",
  "detail_level": "trace",
  "include_relations": true
}
```

### 4. 缓存验证

先调用：

```text
POST /api/v1/admin/cache/clear
```

然后对同一个实体连续调用两次 explain：

- 第一次应看到 `from_cache = false`
- 第二次应看到 `from_cache = true`

### 5. 结果检查点

- `fetch_data -> fetch_api_data` 应保持同一个 `entity_id`
- `DataProcessor.process` 应存在指向 `fetch_api_data` 的 `calls` 关系
- `fetch_api_data` 的 explanation 应体现 rename 历史
- `DataProcessor.process` 的 explanation 应体现它调用了 `fetch_api_data`
- 返回中不应出现 `body_hash`、`__body_norm__` 等内部元数据
- `param_explanations` 的 key 不应带反引号

## 压测方法

不要一上来就拿大并发压真实 LLM。这个项目的瓶颈往往不在 FastAPI，而在 explanation 的 LLM 路径。

当前压测入口统一在：

```bash
.\.venv\Scripts\python.exe scripts\loadtest.py --scenario <scenario>
```

支持场景：

- `ingest`
- `hot-cache-explain`
- `mixed-workload`

### 建议的压测顺序

1. `hot-cache explain`
2. `ingest-only`
3. `mixed workload`
4. `cold-cache explain`

### 1. 热缓存 explain

目标：测 API + SQLite + cache，不让 LLM 成为主瓶颈。

步骤：

1. 先完成上面的冒烟数据写入
2. 先 explain 一次，完成预热
3. 再高并发重复请求同一个 explain

重点看：

- `P50 / P95 / P99`
- 错误率
- `from_cache` 比例
- `/api/v1/admin/stats` 中的 `cache_hits` / `cache_misses`

### 2. ingestion-only

目标：测 `POST /api/v1/events/batch` 的写入、索引、关系提取、SQLite 写路径。

注意：

- 不要一直复用同一个 `session_id`
- 不要一直写同一个 `file_path` 和同一组函数名
- 建议把 `RUN_ID` 拼进 `session_id`、`file_path`、函数名

示例模板：

```json
[
  {
    "action_type": "create",
    "file_path": "data_processor_${RUN_ID}.py",
    "line_range": [1, 3],
    "code_snapshot": "def fetch_data_${RUN_ID}(url, timeout=5.0):\n    return request_remote(url, timeout=timeout)\n",
    "intent": "create fetch helper ${RUN_ID}",
    "reasoning": "pressure test create path",
    "session_id": "pressure-${RUN_ID}"
  },
  {
    "action_type": "rename",
    "file_path": "data_processor_${RUN_ID}.py",
    "line_range": [1, 3],
    "code_snapshot": "def fetch_api_data_${RUN_ID}(url, timeout=5.0):\n    return request_remote(url, timeout=timeout)\n",
    "intent": "rename fetch helper ${RUN_ID}",
    "reasoning": "pressure test rename path",
    "session_id": "pressure-${RUN_ID}"
  }
]
```

### 3. mixed workload

更接近真实情况，建议大致按这个比例：

- `70%` explain
- `20%` events/batch
- `10%` entities / stats 查询

目标：看读写混合时是否出现抖动、锁竞争、解释变慢、缓存命中率下降。

默认实现细节：

- `70%` `POST /api/v1/explain`
- `20%` `POST /api/v1/events/batch`
- `10%` 查询请求：
  - `50%` `GET /api/v1/entities/search`
  - `50%` `GET /api/v1/admin/stats`
- 启动前会先写入唯一 seed 代码，构造 explain 目标池
- 会先清一次 cache，再对 explain 目标做 warmup
- explain 仍然走 `POST /api/v1/explain`，继续覆盖 `QueryRouter`

常用命令：

```bash
.\.venv\Scripts\python.exe scripts\loadtest.py --scenario mixed-workload --requests 100 --concurrency 10 --spawn-app --app-port 8882 --db-path .tmp/loadtest-mixed.db --output loadtest-results/mixed-baseline.json
```

```bash
.\.venv\Scripts\python.exe scripts\loadtest.py --scenario mixed-workload --requests 300 --concurrency 20 --spawn-app --app-port 8883 --db-path .tmp/loadtest-mixed-mid.db --output loadtest-results/mixed-mid.json
```

默认参数：

- `--mix 70,20,10`
- `--seed-count 10`
- `--random-seed 42`
- `--timeout 120`

这两轮本地验收已跑通，结果文件默认写到 `loadtest-results/`，该目录已在 `.gitignore` 中排除。

### 4. 冷缓存 explain

目标：测完整 explanation 链路，包括真实 LLM。

步骤：

1. `POST /api/v1/admin/cache/clear`
2. 并发 explain

注意：

- 这个阶段不要直接拉很高并发
- 先从 `1 / 2 / 5` 并发开始
- 如果使用真实 OpenRouter / Claude / Ollama，这一组更像容量验证，不是纯后端压测

### 压测方式建议

两种形态都要跑：

- 阶梯压测：
  - 1 分钟 `5` 并发
  - 1 分钟 `20`
  - 1 分钟 `50`
  - 1 分钟 `100`
- 稳态压测：
  - 固定并发跑 `10-30` 分钟

### 压测时要盯的指标

- 成功率 / 错误率
- P50 / P95 / P99 延迟
- 吞吐量
- `/api/v1/admin/stats`：
  - `event_count`
  - `relation_count`
  - `cache_hits`
  - `cache_misses`
- CPU / 内存
- 磁盘写入
- SQLite 文件和 WAL 增长
- 是否出现 `database is locked`、超时、连接错误

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
- `tests/test_e2e_smoke.py`

常用命令：

```bash
.\.venv\Scripts\python.exe -m pytest tests -q
```

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_explanation.py tests/test_e2e_smoke.py -q
```

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_integration.py -q
```

## 本地文件与 Git

以下内容是本地运行产物或阶段性文件，不应提交：

- `.env`
- `.venv/`
- `*.db`、`*.db-wal`、`*.db-shm`
- `.tmp/`
- `__pycache__/`
- `.pytest_cache/`
- `NEXT_PHASE_TASK.md`
- `regression_report.txt`
- `explanation_prompt_audit.md`
- `explanation_quality_check.md`
- `loadtest-results/`
- `k6-summary-*.json`
- `pressure-report-*.json`

## 参考

- `docs/requirements.md`
- `docs/system_design.md`
- `AGENTS.md`
- `CONTEXT.md`
