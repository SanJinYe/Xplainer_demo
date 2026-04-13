# TailEvents Coding Explanation Agent

TailEvents 是一个面向 AI 编码会话的可解释性后端。它接收编码代理产生的结构化事件，按函数 / 类 / 方法建立索引，并在查询时生成“这段代码做什么、为什么这样写、和谁有关”的解释。

## 当前状态

- Requirement A 后端闭环已完成
- 已实现模块：`models`、`config`、`storage`、`cache`、`indexer`、`explanation`、`query`、`api`、`graph(stub)`、`ingestion`
- 当前可用入口：
  - 包内启动：`python -m tailevents.main`
  - Uvicorn：`uvicorn tailevents.main:app`

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

## 现在仓库里有什么

### 1. 核心数据模型

位于 `tailevents/models/`：

- `enums.py`：动作、实体、关系等枚举
- `event.py`：`RawEvent`、`TailEvent`、`EntityRef`、`ExternalRef`
- `entity.py`：`CodeEntity`、`ParamInfo`、`EventRef`、`RenameRecord`
- `relation.py`：`Relation`
- `explanation.py`：`ExplanationRequest`、`EntityExplanation`、`ExplanationResponse`
- `protocols.py`：各模块依赖的 Protocol 契约

### 2. 配置层

位于 `tailevents/config/`：

- `settings.py`：Pydantic `Settings`
- `defaults.py`：默认值
- `__init__.py`：`get_settings()`

### 3. 存储层

位于 `tailevents/storage/`：

- `database.py`：`aiosqlite` 连接管理、`initialize_db()`
- `migrations.py`：SQLite schema、索引、FTS5
- `event_store.py`：事件存储
- `entity_db.py`：实体存储和搜索
- `relation_store.py`：关系存储

### 4. 索引与缓存

位于 `tailevents/indexer/` 和 `tailevents/cache/`：

- AST 提取 entity / relation / import
- unified diff 解析
- rename 检测
- pending queue
- SQLite explanation cache

### 5. 解释层

位于 `tailevents/explanation/`：

- prompt 模板
- Ollama / Claude LLM client
- 本地 `pydoc` 文档检索
- context assembly
- LLM 输出格式化
- `ExplanationEngine`

### 6. 查询与 API

位于 `tailevents/query/` 和 `tailevents/api/`：

- `LocationResolver`
- `SymbolResolver`
- `QueryRouter`
- FastAPI routes:
  - `/api/v1/events`
  - `/api/v1/entities`
  - `/api/v1/explain`
  - `/api/v1/relations`
  - `/api/v1/admin`

### 7. 摄取与启动入口

位于 `tailevents/ingestion/` 和 `tailevents/main.py`：

- `RawEventValidator`
- `IngestionPipeline`
- `LoggingHook`
- `GraphUpdateHook`（no-op stub）
- `tailevents.main:app`

### 8. Graph Stub

位于 `tailevents/graph/`：

- `stub.py`：正式 stub 实现
- `graph_service_stub.py`：兼容转发层

## 最小运行方式

安装依赖：

```bash
pip install -r requirements.txt
```

启动服务：

```bash
python -m tailevents.main
```

或者：

```bash
uvicorn tailevents.main:app --host 0.0.0.0 --port 8766
```

带覆盖参数启动：

```bash
python -m tailevents.main --db-path ./tailevents.db --host 0.0.0.0 --port 8766
```

## 测试

当前测试覆盖：

- `tests/test_storage.py`
- `tests/test_indexer.py`
- `tests/test_explanation.py`
- `tests/test_api.py`
- `tests/test_ingestion.py`
- `tests/test_main.py`
- `tests/test_integration.py`

最近一次完整回归命令：

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_integration.py tests/test_ingestion.py tests/test_main.py tests/test_api.py tests/test_explanation.py tests/test_storage.py tests/test_indexer.py -q
```

结果：`33 passed`

## 参考

- `docs/requirements.md`
- `docs/system_design.md`
- `AGENTS.md`
- `CONTEXT.md`
