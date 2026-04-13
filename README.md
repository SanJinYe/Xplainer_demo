# TailEvents Coding Explanation Agent

当前仓库已完成：

- Phase 1：`models + config`
- Phase 2：`storage`
- Phase 3：`indexer + cache`

## 现在有什么

### 1. 数据模型

位于 `tailevents/models/`：

- `enums.py`：全部枚举类型
- `event.py`：`RawEvent`、`TailEvent`、`EntityRef`、`ExternalRef`
- `entity.py`：`CodeEntity`、`ParamInfo`、`EventRef`、`RenameRecord`
- `relation.py`：`Relation`
- `explanation.py`：`ExplanationRequest`、`EntityExplanation`、`ExplanationResponse`
- `protocols.py`：各模块依赖的 `Protocol` 契约
- `__init__.py`：统一导出公共类型

### 2. 配置层

位于 `tailevents/config/`：

- `defaults.py`：默认配置值
- `settings.py`：`Settings`
- `__init__.py`：导出 `Settings` 和 `get_settings()`

### 3. 存储层

位于 `tailevents/storage/`：

- `database.py`：SQLite 连接管理、`initialize_db()`、FastAPI `get_db()`
- `migrations.py`：`events` / `entities` / `relations` / `explanation_cache` schema
- `event_store.py`：`TailEvent` 持久化
- `entity_db.py`：`CodeEntity` 持久化 + FTS5 搜索
- `relation_store.py`：关系存储
- `exceptions.py`：存储层异常

### 4. 缓存层

位于 `tailevents/cache/`：

- `cache.py`：`ExplanationCache`
- `__init__.py`：缓存导出

### 5. 索引层

位于 `tailevents/indexer/`：

- `ast_analyzer.py`：Python AST 提取 entity / relation / imports
- `diff_parser.py`：unified diff 解析
- `rename_tracker.py`：基于 `body_hash` / 相似度的 rename 检测
- `entity_extractor.py`：`CodeEntity` 同步
- `relation_extractor.py`：关系刷新
- `pending_queue.py`：半成品代码暂存队列
- `indexer.py`：主 Indexer 编排

### 6. 测试与依赖

- `tests/test_storage.py`
- `tests/test_indexer.py`
- `.env.example`
- `requirements.txt`

## 当前状态

- 已完成：`models`、`config`、`storage`、`cache`、`indexer`
- 当前阶段：Phase 4（解释层）
- 下一步：实现 `explanation/`
  - `engine.py`
  - `context_assembler.py`
  - `llm_client.py`
  - `doc_retriever.py`
  - `prompts.py`
  - `formatter.py`

## 还没有什么

下面这些模块还没有开始实现：

- `explanation`
- `query`
- `api`
- `graph`（stub）
- `ingestion`

## 最小使用方式

安装依赖：

```bash
pip install -r requirements.txt
```

加载配置：

```python
from tailevents.config import get_settings

settings = get_settings()
print(settings.db_path)
```

初始化数据库：

```python
from tailevents.config import get_settings
from tailevents.storage import SQLiteConnectionManager, initialize_db

settings = get_settings()
database = SQLiteConnectionManager(settings.db_path)
await initialize_db(database)
```

创建索引器：

```python
from tailevents.cache import ExplanationCache
from tailevents.indexer import Indexer
from tailevents.storage import SQLiteEntityDB, SQLiteRelationStore

cache = ExplanationCache(database)
entity_db = SQLiteEntityDB(database)
relation_store = SQLiteRelationStore(database)
indexer = Indexer(entity_db=entity_db, relation_store=relation_store, cache=cache)
```

## 已验证

- `.\.venv\Scripts\python.exe -m pytest tests/test_storage.py tests/test_indexer.py -q`
- 当前测试在项目 `.venv` 中通过

## 参考文档

- `docs/requirements.md`
- `docs/system_design.md`
- `AGENTS.md`
- `CONTEXT.md`
