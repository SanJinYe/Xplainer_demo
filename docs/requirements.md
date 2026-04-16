# TailEvents-based Coding Explanation Agent

## 需求与设计总纲

---

## 一、项目定位

### 解决什么问题

Vibe coding 的核心矛盾：**agent 写出了代码，但没有人（包括用户）真正理解这些代码**。现有工具在这个问题上的覆盖如下：

| 领域 | 代表工具 | 做了什么 | 缺什么 |
|------|----------|----------|--------|
| Agent 可观测性 | LangSmith, Langfuse, MLflow | 记录 agent trace 用于调试/评估 | 不面向终端用户，不做代码实体级索引 |
| 代码知识图谱 | Code-Graph-RAG, Codebase-Memory, Pyan | 静态分析构建代码结构图 | 没有 agent 的意图和决策信息 |
| GraphRAG | Microsoft GraphRAG, Neo4j | 基于知识图谱增强检索生成 | 应用于自然语言文档，不针对代码 |

**本项目占据三者的交叉空白**：将 agent reasoning trace 与代码实体图桥接，实现面向用户的、带有"作者视角"的代码解释系统。

### 两个需求层次

- **需求 A（核心，首先实现）**：基于 TailEvents trace 的代码解释生成——用户查询某个函数，系统返回该函数的作用、上下文、输入输出、关联依赖的通俗解释。
- **需求 B（延伸，前向兼容）**：基于代码实体有向图的结构分析——冗余检测、单点依赖识别、拆分合并建议；GraphRAG 增强的跨实体语义查询。

设计原则：**需求 A 先落地，但架构从一开始就为需求 B 留足接口**。

---

## 二、核心架构

### 数据流

```
Coding Agent
    │
    ├── raw event ──► Event Store (append-only log, key = event_id)
    │
    └── raw event ──► Indexer (AST 解析 + entity 识别)
                          │
                          ├── upsert Entity DB (key = entity_id)
                          │     - 新 entity → 创建条目，填 signature 等
                          │     - 已有 entity → 追加 event_ref
                          │
                          └── upsert Relation Table
                                - source_entity, target_entity, type
                                - provenance: AST_DERIVED
                                - from_event: event_id
```

### 关键设计决策

**不拆分 events，而是建索引。** Event 的核心内容保持原样完整存储，仅允许 Indexer 对 `entity_refs` 做一次 enrichment 回填。Entity DB 以稳定的 `entity_id (UUID)` 为主键，`qualified_name` 是可变索引；每个 entity 下挂载指向相关 events 的引用列表。这避免了数据膨胀，同时精确到函数级的查询。

**Coding agent 零额外负担。** Agent 发射 raw event 时只需携带最轻量的信息（intent 一句话 + code_diff + file_path）。结构化的实体识别和关系提取由后处理 Indexer 完成，不改变 agent 的工作流。

**AST 分析与 agent 声明双信源。** 关系（edges）的 provenance 区分 `AST_DERIVED`（Indexer 静态分析产出，精确）和 `AGENT_DECLARED`（agent 主动声明，有噪声但包含语义信息）。需求 B 做冗余分析时优先信任前者，用后者做语义补充。

---

## 三、数据模型

### Event Store

```yaml
TailEvent:
  # 元信息
  event_id: str                  # 唯一 ID
  timestamp: datetime
  agent_step_id: str             # 关联 agent 的 reasoning step
  action_type: enum              # CREATE | MODIFY | DELETE | REFACTOR | MOVE
  file_path: str
  line_range: [int, int]         # 受影响的行范围
  code_snapshot: str             # diff 或完整代码片段

  # 意图
  intent: str                    # 一句话描述
  reasoning: str                 # agent CoT 摘要（非完整 trace）
  decision_alternatives: list    # 可选：考虑过但未选的方案

  # 实体引用（由 Indexer 填充）
  entity_refs: list[EntityRef]
    - entity_id: str
      role: enum                 # PRIMARY | MODIFIED | REFERENCED

  # 外部依赖引用
  external_refs: list[ExternalRef]
    - package: str               # e.g. "langchain"
      symbol: str                # e.g. "ChatOpenAI.__init__"
      version: str
      doc_uri: str               # 文档标识符
      usage_pattern: enum        # DIRECT_CALL | INHERITANCE | CONFIG | DECORATOR
```

### Entity DB

```yaml
CodeEntity:
  # 主键
  entity_id: str
  name: str                      # "retry_with_backoff"
  qualified_name: str            # "utils.network.retry_with_backoff"
  entity_type: enum              # FUNCTION | CLASS | METHOD | MODULE | CONSTANT
  file_path: str

  # 签名（需求 A 的核心查询目标）
  signature: str
  params: list[ParamInfo]
    - name: str
      type_hint: str
      default: str
      description: str           # 由 Annotator 或后处理填充
  return_type: str

  # 生命周期追踪
  created_by_event: str          # 首次出现的 event_id
  last_modified_event: str
  modification_count: int
  event_refs: list[EventRef]
    - event_id: str
      role: enum                 # CREATED | MODIFIED | REFERENCED
      timestamp: datetime

  # 外部实体标记
  is_external: bool
  package: str

  # 解释缓存
  cached_description: str        # Annotator/Explanation Engine 写回的结构化描述
  description_valid: bool

  # 图属性（需求 B 兼容）
  in_degree: int                 # 被多少实体依赖（延迟计算）
  out_degree: int                # 依赖多少实体
  tags: list[str]                # 语义标签
```

### Relation Table（需求 B 的图 edge 来源）

```yaml
Relation:
  source: str                    # entity_id
  target: str                    # entity_id
  relation_type: enum            # CALLS | IMPORTS | INHERITS | IMPLEMENTS
                                 # | INSTANTIATES | DECORATES | COMPOSED_OF
  provenance: enum               # AGENT_DECLARED | AST_DERIVED | INFERRED
  confidence: float              # 0-1
  from_event: str                # 产生此关系的 event_id
  context: str                   # 可选，关系语境描述
```

---

## 四、Indexer 设计

Indexer 是连接 Event Store 和 Entity DB 的桥梁。**确定性逻辑，不需要 LLM**。

### 输入

每个写入 Event Store 的 raw event。

### 处理流程

1. **解析 code_diff**：使用 Tree-sitter（Python grammar）或 `ast` 模块，识别本次操作涉及哪些 CodeEntity（新增/修改/删除了哪些函数、类、方法）。
2. **Upsert Entity DB**：新 entity 创建条目并填充 signature/params/return_type；已有 entity 追加 event_ref。
3. **提取调用关系**：AST 级别识别函数间的 CALLS/IMPORTS/INHERITS 关系，写入 Relation Table，标记 `provenance: AST_DERIVED`。
4. **处理半成品代码**：若 AST 解析失败（agent 写了一半），暂存 event 为 pending，等后续 event 使代码可解析后回溯处理。

### 技术选型

- **主要语言**：Python（项目当前的主要目标语言）
- **解析器**：Tree-sitter Python grammar（覆盖面广）或 Python 内置 `ast` 模块（更简单，仅限 Python）
- **增量策略**：每次只解析 diff 涉及的代码，不重新解析整个仓库

---

## 五、解释生成 Pipeline（需求 A）

### 查询流程

```
用户悬停/查询某个函数
    │
    ├── 1. QueryRouter 解析用户输入
    │      file_path + line_number / cursor_word / 自由文本
    │      ↓
    │      entity_id 列表
    │
    ├── 2. Entity DB 查找：返回 signature, params, return_type, event_refs
    │
    ├── 3. Event Store 回溯：取所有关联 events 的 intent + reasoning
    │      按时间排序，重点取 created_by_event
    │
    ├── 4. 外部依赖处理（if events.external_refs 非空）：
    │      按 external_refs 拉取 package docs（doc_uri）的相关段落
    │
    ├── 5. 上下文拼装 → LLM 生成解释
    │      输入：signature + intent 链 + reasoning + docs 片段
    │      输出：结构化的解释（作用、参数含义、使用场景、关联函数）
    │
    └── 6. 缓存：entity 未被新 event 修改前，解释可永久缓存
           entity 被修改 → invalidate 缓存
```

### Annotator（可选的语义补全）

- **触发条件**：用户首次查询某 entity 且缓存为空时
- **输入**：entity 的签名 + 关联 events 的 intent/reasoning + 代码片段
- **输出**：per-entity 的结构化描述（写入 Entity DB 的 `cached_description`，并设置 `description_valid = True`）
- **可用小模型处理**，任务窄、可并行

---

## 六、图分析能力（需求 B，前向兼容）

### 图的构建

- **Nodes**：Entity DB 的所有 entries
- **Edges**：Relation Table 的所有 records
- **Edge 属性**：relation_type, provenance, confidence, from_event

### 可支持的分析

| 分析类型 | 方法 | 复杂度 |
|----------|------|--------|
| 孤立定义检测 | `in_degree == 0` | 简单查询 |
| 单点依赖识别 | `in_degree == 1` | 简单查询 |
| 语义级冗余检测 | 两个 entity 的 creation events intent 相似度 | 需要 embedding 比较 |
| 调用链追踪 | 从某 entity 出发的 BFS/DFS | 图遍历 |
| 循环依赖检测 | 有向图环检测 | 标准算法 |
| Community detection | Louvain 等聚类算法 | 需求 B 后期 |

### GraphRAG 检索

当用户的查询涉及多个实体间的关系时（如"这几个函数之间是怎么配合工作的"），通过图遍历获取相关 entity 子图，将子图上的 intent/reasoning 信息作为 context 送入 LLM 生成整体性解释。这比逐个查询再拼接要好，因为图结构保留了实体间的关系拓扑。

---

## 七、前端交互设计

### 目标体验

**Wiki 式悬浮解释**：鼠标悬停 + 长按快捷键 → 浮现该函数的解释卡片。解释完全独立于 coding agent 上下文，不造成上下文污染。

### 技术路线：VSCode Extension（不需要做独立 IDE）

| 组件 | VSCode API | 用途 |
|------|-----------|------|
| 悬浮卡片 | `HoverProvider` | 轻量预览：签名 + 一句话 intent + 来源 event 数 |
| 侧边栏详情 | Sidebar `WebviewView` | 详细解释：完整 reasoning trace、调用链图、docs 摘要 |
| 快捷键触发 | Keybinding API | 条件分流：按住特定键时显示 TailEvent 解释，否则显示原生 hover |
| 图可视化 | Webview 内嵌 D3.js | 需求 B 的函数关系图展示 |

### 上下文隔离保证

VSCode extension 运行在 Extension Host 进程中，与 coding agent（Roo Code / Continue / Copilot 等）完全独立。Extension 查询 Entity DB 和 Event Store、调用 LLM 生成解释——全部发生在 extension 自己的上下文里。Coding agent 不知道这些交互的存在。**数据流是单向的**：读取 agent 产出的 TailEvents，不向 agent 写入任何东西。

### 交互层级

```
Layer 1: HoverProvider（快速预览）
    ┌─────────────────────────────────┐
    │ retry_with_backoff              │
    │ def (fn, max_retries=3) -> Any  │
    │ ─────────────────────────────── │
    │ 为 API 调用添加指数退避重试包装  │
    │ 📎 3 events · 外部依赖: tenacity│
    │ [查看详情 →]                     │
    └─────────────────────────────────┘

Layer 2: Sidebar Webview（详细解释）
    ┌─────────────────────────────────┐
    │ 📘 retry_with_backoff           │
    │ ═══════════════════════════════ │
    │                                 │
    │ ## 作用                         │
    │ 包装任意函数，在调用失败时按指数 │
    │ 退避策略重试...                  │
    │                                 │
    │ ## 参数                         │
    │ - fn: 要包装的目标函数           │
    │ - max_retries: 最大重试次数...   │
    │                                 │
    │ ## 创建历程                      │
    │ Event #003: 初始创建             │
    │ Event #007: 添加 jitter 参数     │
    │ Event #012: 被 api_client 引用   │
    │                                 │
    │ ## 调用关系图                    │
    │ [交互式 D3 图]                   │
    └─────────────────────────────────┘
```

---

## 八、验证路径

### Phase 1：核心 Pipeline 验证（Streamlit / CLI）

- 在现有 dual-agent 系统上让 coding agent 发射 TailEvents
- 实现 Indexer（AST 解析 + Entity DB upsert）
- 实现解释生成 pipeline（Entity 查询 → Event 回溯 → LLM 生成）
- 用 Streamlit 做简单 UI 验证解释质量
- **验证目标**：TailEvent 的 intent 质量是否足够支撑有意义的解释

### Phase 2：VSCode Extension 最小原型

- 实现 HoverProvider：按住快捷键悬停 → 返回 Entity DB 元数据（不调 LLM）
- **验证目标**：交互感受、延迟是否可接受

### Phase 3：完整 Extension

- 加入侧边栏 Webview + LLM 解释生成
- 引入缓存机制（entity 维度的 invalidation）
- 外部依赖的 docs RAG
- **交付物**：可用的产品原型

### Phase 4：图分析（需求 B）

- 侧边栏 Webview 中加入函数关系图可视化
- 实现基础图分析：孤立检测、单点依赖、调用链追踪
- 探索 GraphRAG 增强的跨实体语义查询

---

## 九、技术栈概览

| 组件 | 选型 | 备注 |
|------|------|------|
| Coding Agent | 现有 dual-agent 系统（LangGraph + FastAPI） | 已有基础设施 |
| Event Store | SQLite / ChromaDB | 初期 SQLite 足够，后期可换 |
| Entity DB | SQLite | 主键查询 + 倒排索引 |
| Relation Table | SQLite | 图的 edge list |
| AST 解析器 | Python `ast` 模块 / Tree-sitter | Python 优先用 `ast`，多语言扩展用 Tree-sitter |
| 解释生成 LLM | Qwen3:32b（本地）/ Claude API | 本地优先，fallback 到云端 |
| 前端调试 | Streamlit | Phase 1 快速验证 |
| 最终前端 | VSCode Extension（TypeScript） | HoverProvider + Sidebar Webview |
| 图可视化 | D3.js（Webview 内嵌） | Phase 4 |

---

## 十、已知风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| Agent intent 质量低（"fix bug"式模糊描述） | 解释生成质量差 | Prompt 工程优化 + Annotator 后处理补全 |
| AST 解析半成品代码失败 | Indexer 无法提取 entity | pending 队列 + 回溯处理 |
| 解释生成延迟过高 | Hover 体验差 | 缓存 + 预生成（entity 创建时即触发） |
| 多语言支持 | 初期仅 Python | 架构上用 Tree-sitter 预留，但 Phase 1-3 只做 Python |
| 图过于稀疏（小项目 entity 少） | 需求 B 价值不明显 | 先在中等规模项目上验证 |

---

## 十一、下一阶段产品化需求（新增）

### 目标变化

在 Requirement A 的“可解释后端”基础上，系统进入下一阶段产品化建设。新的目标不再只是“能生成 explanation”，而是：

1. explanation 要足够短、快、准，适合在编辑器内频繁阅读；
2. 系统要具备真实 `coding -> event -> explanation` 的闭环，而不只依赖手工构造的测试事件；
3. 系统要能接手已有代码仓库，在没有历史 coding trace 的情况下建立基础记忆；
4. 后续前端将从“只读 explanation 面板”扩展为“可发起 coding 任务的工作台”。

这些目标被拆解为三条产品线：

- **产品线 A：Explanation / RAG / 检索增强**
- **产品线 B：Coding 工作台**
- **产品线 C：仓库记忆 / 冷启动 Onboarding**

---

## 十二、三条产品线

### 产品线 A：Explanation / RAG / 检索增强

面向“理解代码”的主链路，目标是让 explanation 变成一个可以长期使用的日常工具，而不是一次性 demo。

### 产品线 B：Coding 工作台

面向“生成代码”的主链路，目标是让 TailEvents 不只消费事件，也能驱动真实 coding 任务并生成事件。

### 产品线 C：仓库记忆 / 冷启动 Onboarding

面向“接手已有仓库”的场景，目标是在没有历史 agent trace 的前提下，为现有仓库生成 baseline TailEvents，建立后续 explanation 与图分析的基础。

---

## 十三、七项新增需求

### 需求 A1：Explanation 必须受长度约束

当前 explanation 在短脚本上仍可能生成大段文字，带来三类问题：

- 用户阅读困难；
- 外部 API token 消耗高；
- explanation cache 与数据库体积膨胀。

因此，系统必须支持 explanation 的长度控制：

- `summary` 默认只允许极短输出；
- `detailed` 默认允许结构化说明，但必须限制总体长度；
- explanation 的输入上下文也必须裁剪，不能无界增长。

**验收目标：**

- hover summary 明显短于当前版本；
- panel explanation 在单实体场景下不再是一整页长文；
- explanation cache 单条记录的平均体积显著下降。

### 需求 A2：Explanation 必须支持提速与流式输出

当前 panel explanation 的等待时间偏长。下一阶段必须明确区分：

- hover summary：优先响应速度；
- panel explanation：允许更完整，但必须支持流式首屏返回。

双模型不是默认要求，而是条件决策：

- 先做 explanation 收缩与 prompt/input 裁剪；
- 如果本地模型在此基础上仍无法满足 hover 体验，再引入 summary/detailed 双模型。

**验收目标：**

- panel explanation 必须支持流式展示；
- hover summary 保持轻量；
- 是否启用双模型由实测结果决定，而不是预设。

### 需求 A3：Explanation 必须具备“上下文影响范围”

系统不只要回答“这个函数做什么”，还要逐步回答：

- 它影响了谁；
- 谁影响了它；
- 后续如何影响到最终结果。

但为了控制复杂度，首版只做高置信范围：

- 直接调用者；
- 直接被调用者。

更完整的全局路径、entrypoint/output 路径和 GraphRAG 查询，放入 Requirement B 的后续实现。

**设计原则：**

- 局部高置信范围先做；
- 全局图分析后做；
- 不引入低信噪比启发式范围，如“同文件近邻即相关”。

### 需求 B1：必须建立真实 `coding -> event` 最小闭环

当前系统已有 explanation 闭环，但真实 coding 事件主要来自手工 seed，不足以支持后续 explanation 优化。

因此必须前置一个最小 coding 切片，用来持续产出真实事件数据：

- 用户输入 prompt；
- 系统生成代码修改；
- 修改被应用；
- 立即写入 TailEvent；
- explanation 基于真实任务数据回读。

这个最小闭环的目标不是立刻变成完整 coding 产品，而是为产品线 A 提供真实数据来源。

### 需求 B2：系统最终要演进为 Coding 工作台

在最小闭环之上，后续 TailEvents 将演进为一个类 Roo Code 的本地 coding 工作台，至少包括：

- prompt 输入；
- 流式输出；
- 历史任务；
- 模型与 provider 选择；
- MCP / skills 开关；
- 用户在前端管理模型配置与 API key，而不是长期依赖 `.env`。

这属于完整产品化目标，不要求在当前阶段一次完成，但必须从架构上预留。

### 需求 A4：系统需要外部 docs Retriever

仅靠包的 help / pydoc 不足以支撑真实 explanation。系统后续需要支持多来源外部文档检索：

- 包 help / pydoc；
- README；
- 用户在前端明确授权读取的外部 docs 文件。

但该需求优先级低于 explanation 收缩、提速和真实 coding 闭环。也就是说：

- 外部 retriever 必须做；
- 但不应在 explanation 主链路尚未稳定时优先扩展输入噪声。

### 需求 C1：系统需要已有仓库的冷启动记忆

TailEvents 不能只适用于“agent 从零开始写代码”的场景。对于已有代码仓库，系统必须支持：

- 扫描现有代码；
- 生成 baseline TailEvents；
- 建立实体与关系；
- 在用户 hover / 打开 panel 时按需生成 explanation。

这里的关键约束是：

- 冷启动阶段只生成 baseline TailEvents；
- 不为整个仓库预生成 explanation；
- 不把 tailevents 注释写回源码；
- coding task 不等待全仓库 baseline 扫描完成才开始。

---

## 十四、阶段性路线

### 阶段 1

- B1 最小 `coding -> event` 闭环
- A1 explanation 收缩
- C1 baseline TailEvents 冷启动

### 阶段 2

- A2 panel 流式 explanation
- explanation 质量评估与埋点
- 条件性判断是否启用双模型

### 阶段 3

- A3 局部范围 explanation
- C1/C2 baseline-aware explanation
- C3 实体层级关系补齐

### 阶段 4

- B2 完整 Coding 工作台
- 模型选择、历史任务、MCP / skills
- API key 前端管理

### 阶段 5

- A4 外部 docs Retriever
- Requirement B 的全局路径与 GraphRAG

---

## 十五、默认假设

- 仍然保持单 SQLite；
- 当前阶段不引入向量数据库；
- 双模型是 checkpoint 决策，不是预设要求；
- 冷启动记忆不写回源码注释；
- 外部 docs 只读取用户明确授权的文件；
- 局部 explanation 范围先做 caller / callee，不做低置信启发式关系。