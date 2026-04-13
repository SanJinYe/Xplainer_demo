# TailEvents Coding Explanation Agent — 系统设计

## 自上而下的完整产品架构

---

## 〇、阅读指南

本文档是最终产品的完整设计，不是 MVP。前端（VSCode Extension）和需求 B（图分析/GraphRAG）不在本期实现范围内，但所有接口已为它们预留。

文档结构：
- 第一章：系统全景和模块划分
- 第二章：数据模型（所有模块共享的 schema 定义）
- 第三章~第十章：逐模块详细设计
- 第十一章：模块间接口契约
- 第十二章：实现顺序和依赖关系
- 第十三章：为需求 B 和前端预留的接口

每个模块设计为**可独立生成代码的单元**，模块间通过明确的 Python Protocol 接口通信。

---

## 一、系统全景

### 模块总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        外部消费者                                │
│   VSCode Extension (未实现)    CLI/Streamlit (调试用)            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  API Server │  ← Module 8: 统一对外接口
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼──────┐  ┌─────▼──────┐  ┌──────▼──────┐
   │ Explanation  │  │   Query    │  │   Graph     │
   │   Engine     │  │   Router   │  │   Service   │
   │  Module 6    │  │  Module 7  │  │  Module 9   │
   └──────┬──────┘  └─────┬──────┘  └──────┬──────┘
          │                │                │
          │         ┌──────▼──────┐         │
          │         │    Cache    │         │
          │         │  Module 5   │         │
          │         └──────┬──────┘         │
          │                │                │
   ┌──────▼────────────────▼────────────────▼──────┐
   │              Storage Layer                     │
   │  ┌───────────┐ ┌───────────┐ ┌──────────────┐ │
   │  │Event Store│ │ Entity DB │ │Relation Store│ │
   │  │ Module 3  │ │ Module 4a │ │  Module 4b   │ │
   │  └───────────┘ └───────────┘ └──────────────┘ │
   └──────────────────────▲────────────────────────┘
                          │
                   ┌──────┴──────┐
                   │   Indexer   │  ← Module 2: AST 解析 + 索引更新
                   └──────┬──────┘
                          │
                   ┌──────┴──────┐
                   │  Ingestion  │  ← Module 1: 接收 raw events
                   │  Pipeline   │
                   └──────┬──────┘
                          │
              ┌───────────┴───────────┐
              │    Coding Agent       │  ← 现有 dual-agent 系统
              │   (外部，不在本项目)    │
              └───────────────────────┘
```

### 模块清单

| # | 模块名 | 职责 | 可独立生成 | 依赖 |
|---|--------|------|-----------|------|
| 0 | `models` | 共享数据模型（Pydantic schemas） | ✅ 最先生成 | 无 |
| 1 | `ingestion` | 接收 coding agent 的 raw events，校验，写入 Event Store | ✅ | models, storage |
| 2 | `indexer` | AST 解析 diff，提取 entities 和 relations，更新 Entity DB | ✅ | models, storage |
| 3 | `storage.event_store` | Event Store 的持久化实现 | ✅ | models |
| 4a | `storage.entity_db` | Entity DB 的持久化实现 | ✅ | models |
| 4b | `storage.relation_store` | Relation Store 的持久化实现 | ✅ | models |
| 5 | `cache` | 解释缓存，entity 维度的 invalidation | ✅ | models |
| 6 | `explanation` | 解释生成引擎（context assembly + LLM 调用） | ✅ | models, storage, cache |
| 7 | `query` | 查询路由（解析用户查询，分发到对应处理器） | ✅ | models, storage, explanation |
| 8 | `api` | FastAPI server，统一对外接口 | ✅ | 所有模块 |
| 9 | `graph` | 图构建与分析（需求 B 接口预留，延迟实现） | 🔜 | models, storage |

---

## 二、数据模型 — Module 0: `models`

这是所有模块共享的基础，**必须最先生成**。

### 文件结构

```
models/
├── __init__.py
├── event.py          # TailEvent 及其子结构
├── entity.py         # CodeEntity 及其子结构
├── relation.py       # Relation 及其子结构
├── explanation.py    # 解释请求/响应结构
├── enums.py          # 所有枚举类型
└── protocols.py      # 模块间接口契约（Python Protocol）
```

### enums.py

```python
from enum import Enum

class ActionType(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    REFACTOR = "refactor"
    MOVE = "move"
    RENAME = "rename"

class EntityType(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    MODULE = "module"
    CONSTANT = "constant"
    GLOBAL_VAR = "global_var"

class EntityRole(str, Enum):
    PRIMARY = "primary"       # 本次事件的主要操作对象
    MODIFIED = "modified"     # 被顺带修改
    REFERENCED = "referenced" # 被提及/调用但未修改

class RelationType(str, Enum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    INSTANTIATES = "instantiates"
    DECORATES = "decorates"
    COMPOSED_OF = "composed_of"
    OVERRIDES = "overrides"

class Provenance(str, Enum):
    AGENT_DECLARED = "agent_declared"
    AST_DERIVED = "ast_derived"
    INFERRED = "inferred"

class UsagePattern(str, Enum):
    DIRECT_CALL = "direct_call"
    INHERITANCE = "inheritance"
    CONFIG = "config"
    DECORATOR = "decorator"
    CONTEXT_MANAGER = "context_manager"
```

### event.py

```python
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import uuid4
from typing import Optional

class ExternalRef(BaseModel):
    package: str
    symbol: str                    # e.g. "ChatOpenAI.__init__"
    version: Optional[str] = None
    doc_uri: Optional[str] = None
    usage_pattern: UsagePattern

class EntityRef(BaseModel):
    entity_id: str
    role: EntityRole

class TailEvent(BaseModel):
    # 元信息
    event_id: str = Field(default_factory=lambda: f"te_{uuid4().hex[:12]}")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent_step_id: Optional[str] = None
    session_id: Optional[str] = None    # 关联到一次完整的 coding session

    # 操作
    action_type: ActionType
    file_path: str
    line_range: Optional[tuple[int, int]] = None
    code_snapshot: str                   # diff 或完整代码片段

    # 意图
    intent: str                          # 一句话
    reasoning: Optional[str] = None      # agent CoT 摘要
    decision_alternatives: Optional[list[str]] = None

    # 由 Indexer 填充（ingestion 时为空）
    entity_refs: list[EntityRef] = Field(default_factory=list)
    external_refs: list[ExternalRef] = Field(default_factory=list)

class RawEvent(BaseModel):
    """Coding agent 直接发射的最小结构，由 Ingestion 转为 TailEvent"""
    action_type: ActionType
    file_path: str
    code_snapshot: str
    intent: str
    reasoning: Optional[str] = None
    decision_alternatives: Optional[list[str]] = None
    agent_step_id: Optional[str] = None
    session_id: Optional[str] = None
    line_range: Optional[tuple[int, int]] = None
    external_refs: list[ExternalRef] = Field(default_factory=list)
```

### entity.py

```python
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import uuid4
from typing import Optional

class ParamInfo(BaseModel):
    name: str
    type_hint: Optional[str] = None
    default: Optional[str] = None
    description: Optional[str] = None    # 由 Annotator 填充

class EventRef(BaseModel):
    event_id: str
    role: EntityRole
    timestamp: datetime

class RenameRecord(BaseModel):
    old_qualified_name: str
    new_qualified_name: str
    event_id: str                        # 触发重命名的 event
    timestamp: datetime

class CodeEntity(BaseModel):
    # 主键：UUID，不随重命名变化
    entity_id: str = Field(default_factory=lambda: f"ent_{uuid4().hex[:12]}")

    # 可变的标识信息
    name: str                            # "retry_with_backoff"
    qualified_name: str                  # "utils.network.retry_with_backoff"
    entity_type: EntityType
    file_path: str
    line_range: Optional[tuple[int, int]] = None

    # 签名信息
    signature: Optional[str] = None
    params: list[ParamInfo] = Field(default_factory=list)
    return_type: Optional[str] = None
    docstring: Optional[str] = None      # 代码中的 docstring（如果有）

    # 生命周期
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_event: Optional[str] = None
    last_modified_event: Optional[str] = None
    last_modified_at: Optional[datetime] = None
    modification_count: int = 0
    is_deleted: bool = False             # 软删除标记
    deleted_by_event: Optional[str] = None

    # 事件关联
    event_refs: list[EventRef] = Field(default_factory=list)

    # 重命名追踪
    rename_history: list[RenameRecord] = Field(default_factory=list)

    # 外部实体标记
    is_external: bool = False
    package: Optional[str] = None

    # 描述（由 Explanation Engine 缓存填充）
    cached_description: Optional[str] = None
    description_valid: bool = False      # 修改后 invalidate

    # 图属性（需求 B 兼容，延迟计算）
    in_degree: int = 0
    out_degree: int = 0
    tags: list[str] = Field(default_factory=list)
```

### relation.py

```python
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import uuid4
from typing import Optional

class Relation(BaseModel):
    relation_id: str = Field(default_factory=lambda: f"rel_{uuid4().hex[:12]}")
    source: str                          # entity_id
    target: str                          # entity_id
    relation_type: RelationType
    provenance: Provenance
    confidence: float = 1.0
    from_event: Optional[str] = None     # 产生此关系的 event_id
    context: Optional[str] = None        # 关系语境描述
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True               # 软删除（代码修改后关系可能失效）
```

### explanation.py

```python
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class ExplanationRequest(BaseModel):
    """用户发起的解释请求"""
    query: str                           # 用户的原始查询或函数名
    file_path: Optional[str] = None      # 当前打开的文件
    line_number: Optional[int] = None    # 光标位置
    cursor_word: Optional[str] = None    # 光标所在的 symbol
    detail_level: str = "summary"        # "summary" | "detailed" | "trace"
    include_relations: bool = False      # 是否包含关联函数信息

class EntityExplanation(BaseModel):
    """单个 entity 的解释结果"""
    entity_id: str
    entity_name: str
    qualified_name: str
    entity_type: EntityType
    signature: Optional[str] = None

    # 解释内容
    summary: str                         # 一句话概括
    detailed_explanation: Optional[str] = None
    param_explanations: Optional[dict[str, str]] = None
    return_explanation: Optional[str] = None
    usage_context: Optional[str] = None  # 在项目中的使用场景

    # 来源追踪
    creation_intent: Optional[str] = None
    modification_history: list[dict] = Field(default_factory=list)
    related_entities: list[dict] = Field(default_factory=list)
    external_doc_snippets: list[dict] = Field(default_factory=list)

    # 元信息
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    from_cache: bool = False
    confidence: float = 1.0

class ExplanationResponse(BaseModel):
    """解释请求的完整响应"""
    request: ExplanationRequest
    explanations: list[EntityExplanation]
    graph_context: Optional[dict] = None  # 需求 B 预留：子图信息
```

### protocols.py — 模块间接口契约

```python
from typing import Protocol, Optional, runtime_checkable

# ──────────── Storage Protocols ────────────

@runtime_checkable
class EventStoreProtocol(Protocol):
    async def put(self, event: "TailEvent") -> str: ...
    async def get(self, event_id: str) -> Optional["TailEvent"]: ...
    async def get_batch(self, event_ids: list[str]) -> list["TailEvent"]: ...
    async def get_by_session(self, session_id: str) -> list["TailEvent"]: ...
    async def get_by_file(self, file_path: str) -> list["TailEvent"]: ...
    async def get_recent(self, limit: int = 50) -> list["TailEvent"]: ...
    async def count(self) -> int: ...

@runtime_checkable
class EntityDBProtocol(Protocol):
    async def upsert(self, entity: "CodeEntity") -> str: ...
    async def get(self, entity_id: str) -> Optional["CodeEntity"]: ...
    async def get_by_qualified_name(self, qname: str) -> Optional["CodeEntity"]: ...
    async def get_by_name(self, name: str) -> list["CodeEntity"]: ...
    async def get_by_file(self, file_path: str) -> list["CodeEntity"]: ...
    async def search(self, query: str) -> list["CodeEntity"]: ...
    async def get_all(self) -> list["CodeEntity"]: ...
    async def mark_deleted(self, entity_id: str, event_id: str) -> None: ...
    async def update_description(self, entity_id: str, desc: str) -> None: ...
    async def invalidate_description(self, entity_id: str) -> None: ...
    async def count(self) -> int: ...

@runtime_checkable
class RelationStoreProtocol(Protocol):
    async def put(self, relation: "Relation") -> str: ...
    async def get_outgoing(self, entity_id: str) -> list["Relation"]: ...
    async def get_incoming(self, entity_id: str) -> list["Relation"]: ...
    async def get_between(self, source: str, target: str) -> list["Relation"]: ...
    async def get_by_event(self, event_id: str) -> list["Relation"]: ...
    async def deactivate_by_source(self, entity_id: str) -> None: ...
    async def get_all_active(self) -> list["Relation"]: ...
    async def count(self) -> int: ...

# ──────────── Processing Protocols ────────────

@runtime_checkable
class IndexerProtocol(Protocol):
    async def process_event(self, event: "TailEvent") -> "IndexerResult": ...

class IndexerResult(Protocol):
    entities_created: list[str]          # entity_ids
    entities_modified: list[str]
    entities_deleted: list[str]
    relations_created: list[str]         # relation_ids
    pending: bool                        # AST 解析是否失败（半成品代码）

@runtime_checkable
class ExplanationEngineProtocol(Protocol):
    async def explain_entity(
        self, entity_id: str, detail_level: str = "summary"
    ) -> "EntityExplanation": ...
    async def explain_query(
        self, request: "ExplanationRequest"
    ) -> "ExplanationResponse": ...

@runtime_checkable
class CacheProtocol(Protocol):
    async def get(self, key: str) -> Optional[str]: ...
    async def put(self, key: str, value: str, ttl: Optional[int] = None) -> None: ...
    async def invalidate(self, key: str) -> None: ...
    async def invalidate_prefix(self, prefix: str) -> None: ...

# ──────────── 需求 B 预留接口 ────────────

@runtime_checkable
class GraphServiceProtocol(Protocol):
    """需求 B 的图分析服务接口，延迟实现"""
    async def get_subgraph(
        self, entity_id: str, depth: int = 2
    ) -> dict: ...
    async def get_isolated_entities(self) -> list[str]: ...
    async def get_single_dependency_entities(self) -> list[str]: ...
    async def detect_cycles(self) -> list[list[str]]: ...
    async def get_communities(self) -> list[list[str]]: ...
    async def get_entity_importance(self, entity_id: str) -> dict: ...
```

---

## 三、Module 1: `ingestion` — 事件接收

### 职责

接收 coding agent 发射的 RawEvent，校验、转换为 TailEvent，写入 Event Store，触发 Indexer。

### 文件结构

```
ingestion/
├── __init__.py
├── pipeline.py       # 主 pipeline 逻辑
├── validator.py      # RawEvent 校验
└── hooks.py          # 可扩展的 post-ingestion hooks
```

### 核心逻辑

```python
class IngestionPipeline:
    def __init__(
        self,
        event_store: EventStoreProtocol,
        indexer: IndexerProtocol,
        hooks: list[IngestionHook] = [],
    ): ...

    async def ingest(self, raw: RawEvent) -> TailEvent:
        """
        1. 校验 RawEvent（file_path 是否存在、code_snapshot 是否非空）
        2. 转换为 TailEvent（生成 event_id、timestamp）
        3. 写入 Event Store
        4. 触发 Indexer.process_event()
        5. 用 Indexer 结果更新 TailEvent 的 entity_refs
        6. 再次写入 Event Store（更新 entity_refs）
        7. 执行 post-ingestion hooks
        8. 返回完整的 TailEvent
        """

    async def ingest_batch(self, raws: list[RawEvent]) -> list[TailEvent]:
        """批量 ingestion，用于回放历史 trace"""
```

### Hooks 机制（扩展点）

```python
class IngestionHook(Protocol):
    async def on_event_ingested(self, event: TailEvent, result: IndexerResult) -> None: ...

# 示例 hook：需求 B 的图更新
class GraphUpdateHook:
    """当新 event 产生新的 relations 时，通知 GraphService 更新"""
    async def on_event_ingested(self, event, result):
        # 延迟实现，接口先预留
        pass
```

---

## 四、Module 2: `indexer` — AST 解析与索引

### 职责

解析 TailEvent 的 code_snapshot（diff），识别涉及的 CodeEntity，提取 relations，更新 Entity DB 和 Relation Store。

### 文件结构

```
indexer/
├── __init__.py
├── indexer.py            # 主 Indexer 类
├── ast_analyzer.py       # Python AST 解析器
├── entity_extractor.py   # 从 AST 提取 entity 信息
├── relation_extractor.py # 从 AST 提取调用/继承关系
├── diff_parser.py        # 解析 unified diff 格式
├── rename_tracker.py     # 重命名检测
└── pending_queue.py      # 半成品代码的暂存队列
```

### 核心逻辑

```python
class Indexer:
    def __init__(
        self,
        entity_db: EntityDBProtocol,
        relation_store: RelationStoreProtocol,
    ): ...

    async def process_event(self, event: TailEvent) -> IndexerResult:
        """
        1. 解析 code_snapshot
           - 如果是 diff 格式 → DiffParser 提取变更文件和行范围
           - 如果是完整代码 → 直接解析
        2. AST 解析
           - 尝试用 ast.parse() 解析
           - 失败 → 存入 pending_queue，返回 pending=True
        3. Entity 提取
           - 识别所有 def/class 定义
           - 与 Entity DB 现有记录比对
           - 新 entity → 创建 CodeEntity
           - 已有 entity → 追加 event_ref，更新 signature（如有变化）
           - 被删除的 entity → mark_deleted
        4. 重命名检测
           - 如果同一 event 中有 entity 消失 + 新 entity 出现
           - 且 function body 相似度 > 阈值
           - → 视为重命名，更新 qualified_name，记录 rename_history
        5. Relation 提取
           - 遍历 AST 找 function calls, imports, class bases
           - 每个调用/继承关系 → 写入 Relation Store
           - provenance = AST_DERIVED, confidence = 1.0
        6. 更新 event.entity_refs
        7. Invalidate 受影响 entity 的缓存描述
        8. 尝试处理 pending_queue 中的历史 events
        """

    async def reindex_file(self, file_path: str, content: str) -> IndexerResult:
        """完整重建某个文件的所有 entities 和 relations"""

    async def reindex_all(self, project_root: str) -> list[IndexerResult]:
        """全量重建（首次启动或数据不一致时）"""
```

### AST 分析器

```python
class ASTAnalyzer:
    """基于 Python ast 模块的代码分析器"""

    def extract_entities(self, source: str, file_path: str) -> list[ExtractedEntity]:
        """
        解析源代码，返回所有 entity 定义
        ExtractedEntity 包含：
        - name, qualified_name, entity_type
        - signature, params, return_type, docstring
        - line_range
        - body_hash (用于重命名检测的内容指纹)
        """

    def extract_relations(
        self, source: str, file_path: str, known_entities: dict[str, str]
    ) -> list[ExtractedRelation]:
        """
        解析源代码中的调用/继承/导入关系
        known_entities: qualified_name → entity_id 的映射
        用于将 AST 中的名称解析为已知 entity_id
        """

    def extract_imports(self, source: str) -> list[ImportInfo]:
        """提取 import 语句，用于识别外部依赖"""
```

### 重命名追踪器

```python
class RenameTracker:
    def detect_rename(
        self,
        disappeared: list[CodeEntity],   # 本次 event 后不再存在的 entities
        appeared: list[ExtractedEntity],  # 本次 event 新出现的 entities
    ) -> list[tuple[str, str]]:          # (old_entity_id, new_qualified_name) pairs
        """
        基于 body_hash 相似度检测重命名
        阈值：body_hash 完全一致 → 确定是重命名
               body 编辑距离 < 20% → 可能是重命名（标记为 INFERRED）
        """
```

---

## 五、Module 3: `storage.event_store` — Event Store

### 职责

TailEvent 的持久化存储。Append-only log 语义。

### 文件结构

```
storage/
├── __init__.py
├── event_store.py         # EventStore 实现
├── entity_db.py           # EntityDB 实现 (Module 4a)
├── relation_store.py      # RelationStore 实现 (Module 4b)
├── database.py            # SQLite 连接管理
└── migrations.py          # Schema 版本管理
```

### SQLite Schema

```sql
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    session_id TEXT,
    agent_step_id TEXT,
    action_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line_range_start INTEGER,
    line_range_end INTEGER,
    code_snapshot TEXT NOT NULL,
    intent TEXT NOT NULL,
    reasoning TEXT,
    decision_alternatives TEXT,       -- JSON array
    entity_refs TEXT,                 -- JSON array of EntityRef
    external_refs TEXT                -- JSON array of ExternalRef
);

CREATE INDEX idx_events_session ON events(session_id);
CREATE INDEX idx_events_file ON events(file_path);
CREATE INDEX idx_events_timestamp ON events(timestamp);
```

### 实现要点

- 所有 list/dict 字段序列化为 JSON 存储
- `get_batch` 使用 `WHERE event_id IN (...)` 一次查询
- 全部 async（使用 `aiosqlite`）
- 单个 SQLite 文件，路径可配置

---

## 六、Module 4a: `storage.entity_db` — Entity DB

### SQLite Schema

```sql
CREATE TABLE entities (
    entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line_range_start INTEGER,
    line_range_end INTEGER,
    signature TEXT,
    params TEXT,                       -- JSON array of ParamInfo
    return_type TEXT,
    docstring TEXT,
    created_at TEXT NOT NULL,
    created_by_event TEXT,
    last_modified_event TEXT,
    last_modified_at TEXT,
    modification_count INTEGER DEFAULT 0,
    is_deleted INTEGER DEFAULT 0,
    deleted_by_event TEXT,
    event_refs TEXT,                   -- JSON array of EventRef
    rename_history TEXT,               -- JSON array of RenameRecord
    is_external INTEGER DEFAULT 0,
    package TEXT,
    cached_description TEXT,
    description_valid INTEGER DEFAULT 0,
    in_degree INTEGER DEFAULT 0,
    out_degree INTEGER DEFAULT 0,
    tags TEXT                          -- JSON array
);

CREATE INDEX idx_entities_qname ON entities(qualified_name);
CREATE INDEX idx_entities_name ON entities(name);
CREATE INDEX idx_entities_file ON entities(file_path);
CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE INDEX idx_entities_active ON entities(is_deleted);
```

### 实现要点

- `search(query)` 使用 SQLite FTS5 全文索引（对 name + qualified_name + cached_description）
- `get_by_qualified_name` 需要同时检查 rename_history 中的旧名称
- `invalidate_description` 仅设置 `description_valid = 0`，不删除缓存内容（允许 stale 展示）
- `upsert` 基于 entity_id 做 INSERT OR REPLACE

---

## 七、Module 4b: `storage.relation_store` — Relation Store

### SQLite Schema

```sql
CREATE TABLE relations (
    relation_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,              -- entity_id
    target TEXT NOT NULL,              -- entity_id
    relation_type TEXT NOT NULL,
    provenance TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    from_event TEXT,
    context TEXT,
    created_at TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (source) REFERENCES entities(entity_id),
    FOREIGN KEY (target) REFERENCES entities(entity_id)
);

CREATE INDEX idx_relations_source ON relations(source);
CREATE INDEX idx_relations_target ON relations(target);
CREATE INDEX idx_relations_event ON relations(from_event);
CREATE INDEX idx_relations_active ON relations(is_active);
```

### 实现要点

- `deactivate_by_source` 将某 entity 所有出边标记为 inactive（entity 被重大修改后，旧的调用关系可能失效，由 Indexer 重新提取）
- 需求 B 的图构建直接查询 `SELECT * FROM relations WHERE is_active = 1`

---

## 八、Module 5: `cache` — 解释缓存

### 文件结构

```
cache/
├── __init__.py
└── cache.py
```

### 实现

```python
class ExplanationCache:
    """
    缓存策略：
    - key = f"explanation:{entity_id}:{detail_level}"
    - 存储在 SQLite（和 Storage Layer 共享同一个 db 文件）
    - Invalidation 触发条件：
      1. entity 被新 event 修改 → Indexer 调用 invalidate
      2. TTL 过期（可选，默认不过期）
      3. 手动清除
    - 支持 stale-while-revalidate：返回旧缓存同时后台更新
    """
```

### SQLite Schema

```sql
CREATE TABLE explanation_cache (
    cache_key TEXT PRIMARY KEY,
    value TEXT NOT NULL,               -- JSON serialized EntityExplanation
    created_at TEXT NOT NULL,
    expires_at TEXT,                    -- NULL = 不过期
    is_valid INTEGER DEFAULT 1
);
```

---

## 九、Module 6: `explanation` — 解释生成引擎

### 文件结构

```
explanation/
├── __init__.py
├── engine.py              # 主引擎
├── context_assembler.py   # 上下文拼装
├── llm_client.py          # LLM 调用抽象
├── doc_retriever.py       # 外部依赖文档检索
├── prompts.py             # prompt 模板
└── formatter.py           # 输出格式化
```

### 核心逻辑

```python
class ExplanationEngine:
    def __init__(
        self,
        entity_db: EntityDBProtocol,
        event_store: EventStoreProtocol,
        relation_store: RelationStoreProtocol,
        cache: CacheProtocol,
        llm_client: LLMClientProtocol,
        doc_retriever: DocRetrieverProtocol,
    ): ...

    async def explain_entity(
        self, entity_id: str, detail_level: str = "summary"
    ) -> EntityExplanation:
        """
        1. 检查缓存 → 命中则返回
        2. 从 Entity DB 获取 entity 元信息
        3. 从 Event Store 获取关联 events（按时间排序）
        4. 上下文拼装（ContextAssembler）
           - summary: signature + creation intent
           - detailed: + all modification intents + reasoning
           - trace: + 完整的 event 链 + decision_alternatives
        5. 如果 is_external → DocRetriever 拉取 package docs
        6. 如果 include_relations → RelationStore 获取关联 entities
        7. LLM 调用生成解释
        8. 格式化为 EntityExplanation
        9. 写入缓存
        """

    async def explain_query(self, request: ExplanationRequest) -> ExplanationResponse:
        """
        处理用户的自由文本查询：
        1. 如果提供了 file_path + line_number → 定位 entity
        2. 如果提供了 cursor_word → Entity DB 搜索
        3. 如果只有自由文本 query → Entity DB 全文搜索
        4. 对每个匹配的 entity 调用 explain_entity
        5. 汇总为 ExplanationResponse
        """
```

### Context Assembler

```python
class ContextAssembler:
    """
    将 entity 信息 + event 链 + 关联 entities + docs 拼装为
    LLM 可消费的结构化上下文。

    输出格式（示例）：

    # Target Entity
    Function: utils.network.retry_with_backoff
    Signature: def retry_with_backoff(fn, max_retries=3, base_delay=1.0) -> Any
    File: utils/network.py, lines 45-78

    # Creation Context
    Event te_003 (2024-01-15 14:30):
      Intent: 为 API 调用添加指数退避重试包装
      Action: CREATE

    # Modification History
    Event te_007 (2024-01-15 15:10):
      Intent: 添加 jitter 参数避免雷鸣群效应
      Action: MODIFY

    # Relations
    Called by: api_client.make_request (3 times in events)
    Calls: time.sleep, random.uniform

    # External Dependencies
    Pattern: Similar to tenacity.retry decorator
    Doc snippet: [from package docs]
    """
```

### LLM Client 抽象

```python
class LLMClientProtocol(Protocol):
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str: ...

class OllamaLLMClient:
    """本地 Ollama (Qwen3:32b) 客户端"""
    def __init__(self, base_url: str, model: str, proxy_bypass: bool = True): ...

class ClaudeLLMClient:
    """Claude API 客户端（fallback）"""
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"): ...

class LLMClientFactory:
    """根据配置选择 LLM 后端"""
    @staticmethod
    def create(config: dict) -> LLMClientProtocol: ...
```

### Doc Retriever

```python
class DocRetrieverProtocol(Protocol):
    async def retrieve(self, package: str, symbol: str) -> Optional[str]: ...

class DocRetriever:
    """
    外部包文档检索。分层策略：
    1. 本地缓存（已检索过的文档片段）
    2. 本地 pydoc（已安装的包）
    3. 在线文档抓取（预留接口，延迟实现）
    """
    async def retrieve(self, package: str, symbol: str) -> Optional[str]:
        # 先查缓存
        # 再尝试 pydoc
        # 最后返回 None（在线抓取延迟实现）
```

---

## 十、Module 7: `query` — 查询路由

### 文件结构

```
query/
├── __init__.py
├── router.py              # 查询路由主逻辑
├── symbol_resolver.py     # 将用户输入的 symbol 解析为 entity_id
└── location_resolver.py   # 将 file_path + line_number 解析为 entity_id
```

### 核心逻辑

```python
class QueryRouter:
    def __init__(
        self,
        entity_db: EntityDBProtocol,
        explanation_engine: ExplanationEngineProtocol,
    ): ...

    async def route(self, request: ExplanationRequest) -> ExplanationResponse:
        """
        路由策略：
        1. file_path + line_number → LocationResolver
           精确定位到某行所在的 entity
        2. cursor_word → SymbolResolver
           模糊匹配 entity name/qualified_name
        3. 自由文本 query → Entity DB 全文搜索
           返回 top-k 匹配的 entities
        4. 将解析出的 entity_ids 传给 ExplanationEngine
        """

class SymbolResolver:
    """
    将一个 symbol 名称解析为 entity_id。
    解析优先级：
    1. qualified_name 精确匹配
    2. name 精确匹配（可能有多个）
    3. rename_history 中的旧名匹配
    4. 模糊搜索
    """

class LocationResolver:
    """
    将 (file_path, line_number) 解析为 entity_id。
    查询 Entity DB 中 file_path 匹配且 line_range 包含该行号的 entity。
    如果某行属于多个嵌套 entity（e.g. 方法在类内），返回最内层的。
    """
```

---

## 十一、Module 8: `api` — 统一对外接口

### 文件结构

```
api/
├── __init__.py
├── server.py              # FastAPI app
├── routes/
│   ├── __init__.py
│   ├── events.py          # event 相关端点
│   ├── entities.py        # entity 相关端点
│   ├── explanations.py    # 解释相关端点
│   ├── relations.py       # 关系相关端点
│   └── admin.py           # 管理端点（reindex、stats）
└── dependencies.py        # FastAPI 依赖注入
```

### API 端点设计

```
# ── Ingestion（coding agent 调用）──
POST   /api/v1/events                    # 接收 RawEvent
POST   /api/v1/events/batch              # 批量接收

# ── Entity 查询（前端调用）──
GET    /api/v1/entities                   # 列出所有 entities（支持分页、过滤）
GET    /api/v1/entities/{entity_id}       # 获取单个 entity 详情
GET    /api/v1/entities/search?q=...      # 搜索 entities
GET    /api/v1/entities/by-location?file=...&line=...  # 按位置查找

# ── 解释生成（前端调用）──
POST   /api/v1/explain                    # 提交 ExplanationRequest，返回 ExplanationResponse
GET    /api/v1/explain/{entity_id}        # 快捷：解释某个 entity
GET    /api/v1/explain/{entity_id}/summary    # 只返回摘要（用于 hover）

# ── 关系查询（前端 + 需求 B）──
GET    /api/v1/relations/{entity_id}/outgoing  # 某 entity 的出边
GET    /api/v1/relations/{entity_id}/incoming  # 某 entity 的入边
GET    /api/v1/relations/{entity_id}/subgraph?depth=2  # 子图（需求 B）

# ── Event 查询（调试 + 审计）──
GET    /api/v1/events/{event_id}          # 获取单个 event
GET    /api/v1/events?session=...         # 按 session 查询
GET    /api/v1/events/for-entity/{entity_id}  # 某 entity 的所有关联 events

# ── 管理（调试用）──
POST   /api/v1/admin/reindex              # 全量重建索引
GET    /api/v1/admin/stats                # 系统统计（entity 数、event 数、缓存命中率）
POST   /api/v1/admin/cache/clear          # 清除所有缓存
GET    /api/v1/admin/health               # 健康检查
```

### WebSocket 端点（需求 B 预留）

```
# 需求 B 可能需要实时推送（entity 更新、图变化）
WS     /ws/v1/updates                     # 实时推送 entity/event 变更
```

---

## 十二、Module 9: `graph` — 图服务（需求 B，接口预留）

本期只写接口和 stub 实现，不做真正的图分析。

```python
class GraphServiceStub:
    """
    需求 B 的占位实现。所有方法返回空结果或抛出 NotImplementedError。
    真正的实现将在 Phase 4 填充。
    """

    async def get_subgraph(self, entity_id: str, depth: int = 2) -> dict:
        """
        未来实现：
        从 RelationStore 获取 entity 的 n 跳邻居
        返回 { nodes: [...], edges: [...] } 格式
        """
        return {"nodes": [], "edges": [], "implemented": False}

    async def get_isolated_entities(self) -> list[str]:
        """未来实现：查询 in_degree == 0 且 out_degree == 0 的 entities"""
        raise NotImplementedError("Graph analysis not yet implemented")

    # ... 其他方法类似
```

---

## 十三、配置与启动

### 文件结构

```
config/
├── __init__.py
├── settings.py            # Pydantic Settings（环境变量 + .env）
└── defaults.py            # 默认值
```

### 配置项

```python
class Settings(BaseSettings):
    # Database
    db_path: str = "./tailevents.db"

    # LLM
    llm_backend: str = "ollama"        # "ollama" | "claude" | "openrouter"
    ollama_base_url: str = "http://100.115.45.10:11434"
    ollama_model: str = "qwen3:32b"
    claude_api_key: Optional[str] = None
    claude_model: str = "claude-sonnet-4-20250514"

    # Proxy
    proxy_url: Optional[str] = "http://127.0.0.1:7897"
    no_proxy_hosts: list[str] = ["100.115.45.10"]

    # API Server
    api_host: str = "0.0.0.0"
    api_port: int = 8766

    # Indexer
    rename_similarity_threshold: float = 0.8
    ast_parser: str = "python_ast"     # "python_ast" | "tree_sitter"

    # Cache
    cache_enabled: bool = True
    cache_default_ttl: Optional[int] = None  # None = 不过期

    # Explanation
    explanation_max_events: int = 20    # 上下文拼装时最多回溯多少 events
    explanation_temperature: float = 0.3

    class Config:
        env_file = ".env"
        env_prefix = "TAILEVENTS_"
```

---

## 十四、项目目录总览

```
tailevents/
├── config/
│   ├── __init__.py
│   ├── settings.py
│   └── defaults.py
├── models/
│   ├── __init__.py
│   ├── enums.py
│   ├── event.py
│   ├── entity.py
│   ├── relation.py
│   ├── explanation.py
│   └── protocols.py
├── ingestion/
│   ├── __init__.py
│   ├── pipeline.py
│   ├── validator.py
│   └── hooks.py
├── indexer/
│   ├── __init__.py
│   ├── indexer.py
│   ├── ast_analyzer.py
│   ├── entity_extractor.py
│   ├── relation_extractor.py
│   ├── diff_parser.py
│   ├── rename_tracker.py
│   └── pending_queue.py
├── storage/
│   ├── __init__.py
│   ├── database.py
│   ├── event_store.py
│   ├── entity_db.py
│   ├── relation_store.py
│   └── migrations.py
├── cache/
│   ├── __init__.py
│   └── cache.py
├── explanation/
│   ├── __init__.py
│   ├── engine.py
│   ├── context_assembler.py
│   ├── llm_client.py
│   ├── doc_retriever.py
│   ├── prompts.py
│   └── formatter.py
├── query/
│   ├── __init__.py
│   ├── router.py
│   ├── symbol_resolver.py
│   └── location_resolver.py
├── graph/
│   ├── __init__.py
│   └── stub.py
├── api/
│   ├── __init__.py
│   ├── server.py
│   ├── dependencies.py
│   └── routes/
│       ├── __init__.py
│       ├── events.py
│       ├── entities.py
│       ├── explanations.py
│       ├── relations.py
│       └── admin.py
├── main.py                    # 入口：启动 API server
├── CONTEXT.md                 # 跨 session 交接文档
├── requirements.txt
└── .env.example
```

---

## 十五、实现顺序与模块依赖

```
Phase 1: 基础层（可在一个 session 生成）
  ① models        → 无依赖，最先生成
  ② config        → 依赖 models

Phase 2: 存储层（可在一个 session 生成）
  ③ storage       → 依赖 models
     含 database.py, event_store.py, entity_db.py, relation_store.py

Phase 3: 处理层（建议分两个 session）
  ④ indexer       → 依赖 models, storage      # Session A
  ⑤ cache         → 依赖 models               # Session A

Phase 4: 解释层（建议一个 session）
  ⑥ explanation   → 依赖 models, storage, cache

Phase 5: 查询与接口层（建议一个 session）
  ⑦ query         → 依赖 models, storage, explanation
  ⑧ api           → 依赖所有模块
  ⑨ graph/stub    → 依赖 models（stub 很小，随 api 一起）

Phase 6: 集成（一个 session）
  ⑩ ingestion     → 依赖 models, storage, indexer
  ⑪ main.py       → 组装所有模块

依赖图：

  models ──► config
    │
    ├──► storage ──┬──► indexer ──► ingestion
    │              │
    │              ├──► explanation ──► query ──► api
    │              │        │
    │              │        ▼
    │              │      cache
    │              │
    │              └──► graph/stub
    │
    └──► protocols (被所有模块引用)
```

---

## 十六、跨 Session 交接规范

每个 code generation session 结束时更新 CONTEXT.md：

```markdown
# CONTEXT.md

## 项目状态
- 当前 Phase: [1-6]
- 已完成模块: [列表]
- 下一步: [下一个要生成的模块]

## 已做的设计决策
- Entity DB 主键: UUID (entity_id)，qualified_name 为可变索引
- LLM 后端: 优先 Ollama (Qwen3:32b)，fallback Claude API
- 数据库: 单个 SQLite 文件
- 异步: 全部 async (aiosqlite)

## 环境信息
- OS: Windows 11
- Python: venv at C:\Users\16089\agent\.venv
- Ollama: 100.115.45.10:11434 (Tailscale)
- Proxy: 127.0.0.1:7897 (NO_PROXY for Tailscale IP)

## 注意事项
- [在开发过程中积累的坑和经验]
```

---

## 十七、为前端预留的接口清单

VSCode Extension 需要调用的 API 端点和预期行为：

| 前端交互 | 调用端点 | 预期延迟 |
|----------|----------|----------|
| HoverProvider（快速预览） | `GET /api/v1/explain/{entity_id}/summary` | < 200ms（缓存命中）|
| 侧边栏详细解释 | `POST /api/v1/explain` with detail_level="detailed" | < 3s |
| 按位置查找 entity | `GET /api/v1/entities/by-location?file=...&line=...` | < 100ms |
| 搜索 entity | `GET /api/v1/entities/search?q=...` | < 200ms |
| 子图可视化（需求 B） | `GET /api/v1/relations/{id}/subgraph?depth=2` | < 500ms |
| 实时更新推送（需求 B） | `WS /ws/v1/updates` | 实时 |

前端不需要知道后端的实现细节，只需要按 API 契约调用。API 返回的数据结构就是 `models/explanation.py` 中定义的 `ExplanationResponse`。
