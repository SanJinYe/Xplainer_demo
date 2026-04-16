# NEXT_PHASE_TASK：TailEvents 三条产品线修订版任务书

## 当前基线

- 当前 Requirement A 后端与 VSCode Extension MVP 已完成，现有主链路可复用。
- 当前可复用链路为：`ingestion -> event store -> indexer -> entity/relation store -> query router -> explanation engine -> cache -> api`。
- `tailevents/graph/` 仍然是 stub，本任务书不要求重做 Requirement A 基础，也不要求提前实现完整图分析。
- 本任务书描述的是下一阶段产品化执行顺序，规划来源以 `docs/requirements.md` 第 12-15 节和 `docs/system_design.md` 第 18 章为准。

## 当前执行状态（2026-04-16）

- 当前仍处于 `Phase 1`。
- `B-next` 已完成并稳定可用：
  - backend task session
  - `view -> edit -> verify -> Apply`
  - VS Code `Code` 模式三块输出
- `C1` 已完成并稳定可用：
  - `POST /api/v1/baseline/onboard-file`
  - `TailEvents: Onboard Repository`
  - baseline 去重与 traced-history 跳过
- `A1` 第一轮工程收缩已完成：
  - `summary` 独立 prompt
  - default detailed 四段结构
  - `ContextAssembler` 输入预算收紧
  - `Formatter` 长度硬限制
  - targeted tests 已通过
- `Phase 1` 当前剩余重点：
  - A1 的小样本人工评测
  - 根据人工反馈微调 prompt / formatter
  - 再决定是否进入 `A2` 的 panel 流式 explanation

## 修订原则

- 接受 `R1`：前置一个最小真实 `coding -> event` 闭环，不再只依赖手工 seed 数据打磨 explanation。
- 接受 `R2`：`双模型` 不再预设实施，改为 `A1` 完成后的条件决策；`panel 流式` 仍然是必做项。
- 接受 `R3`：成功标准必须包含质量维度，不能只看长度、速度、体积。
- 接受 `R4`：`A3` 首版只做高置信关系，只保留 `caller / callee`，不做“同文件近邻”等低信噪比范围。
- 接受对 `C1` 的澄清：`C1` 只生成 `baseline TailEvents`，不预生成 explanation；`C1` 与 `A1/B-next` 并行启动，但不阻塞 coding 任务。

## 三条产品线

### 产品线 A：Explanation / RAG / 解释质量

目标是把 explanation 做成可长期使用的理解工具，先解决“短、稳、准、快”，再逐步引入流式、外部文档检索和全局影响路径能力。

### 产品线 B：Coding 工作台

目标是从“只读 explanation 面板”演进到“可发起 coding 任务的工作台”。当前第一步不再是旧的单轮文件改写器，而是一个 backend 编排、extension 执行本地观察工具的最小真实 coding agent，用 `view -> edit -> verify -> Apply -> event` 建立真实数据闭环。

### 产品线 C：仓库记忆 / 冷启动 Onboarding

目标是让系统能接手已有仓库，在没有历史 agent trace 的前提下，先建立 baseline TailEvents、实体和关系，再按需生成 explanation。

## 阶段 1：B-next + A1 + C1 并行

### B-next：最小真实 Coding Agent 切片

目标：
- 为产品线 A 提供真实任务数据，不再只依赖手工 seed。
- 先做出一个可用的最小真实 coding agent，而不是单轮文件改写器。

固定范围：
- 单轮任务。
- 单文件。
- 当前目标文件外最多 `2` 个显式只读上下文文件。
- 人工 `Apply`。
- 无任务历史页面。
- 无多模型 profile UI。
- 无 MCP / skills UI。
- 无 SecretStorage 改造。
- 不做 repo 级自主搜索。
- 不做多文件编辑。
- 配置继续沿用当前后端 settings / `.env`。

实现约束：
- 后端持有 task session 与 agent loop，接口固定为：
  - `POST /api/v1/coding/tasks`
  - `GET /api/v1/coding/tasks/{task_id}/stream`
  - `POST /api/v1/coding/tasks/{task_id}/tool-result`
  - `POST /api/v1/coding/tasks/{task_id}/cancel`
- extension 只暴露最小本地观察工具：
  - `view_file(path)`
- backend loop 固定为：
  - `view`
  - `edit`
  - `verify`
- `edit` 必须建立在至少一次成功 `view` 之后。
- `verify` 通过前，不允许出现可 `Apply` 的 draft。
- `verify` v1 只做：
  - Python 语法校验
  - 目标文件版本 / 内容漂移检查
- `Code` 模式固定包含：
  - prompt 输入
  - 目标文件显示
  - 最多 `2` 个只读 context files
  - `Run`
  - `Cancel`
  - `Step Transcript`
  - `Model Output`
  - `Verified Draft`
  - `Apply`
- 任务步骤必须落成 `TaskStepEvent`，至少覆盖：
  - `view`
  - `edit`
  - `verify`
- `TaskStepEvent` 只服务 coding workflow transcript 与后续 task history，不进入现有 explanation history。
- 最终只有用户点击 `Apply` 后，才写入一条真实 `RawEvent`。
- 最终 `RawEvent` 继续复用现有 `/events`，并进入当前 ingestion / explanation 链路。

验收：
- 单轮 prompt 可以建立 task session，并驱动一次完整 `view -> edit -> verify` 流程。
- `Step Transcript` 中可以看到真实步骤，而不是只有最终 `Apply`。
- `Model Output` 可以显示模型 token 流。
- 只有 verified draft 出来后 `Apply` 才可点击。
- `Apply` 后文件被更新，并写入 `1` 条真实 `RawEvent`。
- `Cancel` 不会写入额外 `modify` 历史。
- 后续 hover / panel 能解释这次被接受的修改。

### A1：Explanation 收缩与质量基线

目标：
- 先把 explanation 做到“短、稳、准”，并验证样本同时来自真实 coding 任务与 baseline 仓库实体。

当前状态：
- 工程实现第一轮已完成。
- 已落地的内容包括：
  - `summary` / `detailed` prompt 拆分
  - default detailed 四段结构
  - `ContextAssembler` 事件与 caller/callee 预算
  - `Formatter` 长度硬限制
  - baseline-only anti-hallucination prompt guardrail
- 当前未完成的是人工 `20` 样本评测与后续调参，不是基础工程缺失。

固定范围：
- `summary` 与 `detailed` 是默认 UI 路径。
- `trace` 保留，但不作为 hover 或 panel 默认路径。
- 当前阶段不引入 README / 用户 docs。

实现约束：
- `summary` 改成独立 prompt，不再复用 5 段式结构。
- `summary` 硬限制为：
  - 最多 `2` 句。
  - 最多 `120` 中文字。
  - 只回答“它做什么”和“对上下文的直接作用”。
- `detailed` 默认限制为：
  - 最多 `1200` 中文字。
  - 只保留 `核心作用`、`关键上下文`、`关键事件`、`关联实体` 四个区块。
- `trace` 保留，但不作为默认 UI 路径。
- `ContextAssembler` 输入裁剪固定为：
  - `summary`：目标实体 + 创建事件 + 最多 `1` 条最新修改。
  - `detailed`：目标实体 + 最多 `3` 条事件 + 最多 `2` 个 caller + 最多 `2` 个 callee。
- `Formatter` 必须承担最终截断责任，避免模型失控时把超长 explanation 落库。

验收：
- 样本集必须同时包含：
  - 至少 `10` 个 `B-next` 真实 coding 任务产出的 explanation。
  - 至少 `10` 个 `C1` baseline 仓库实体 explanation。
- 人工评测维度固定为：
  - `准确性`
  - `信息密度`
  - `可读性`
- 评分标准固定为 `1-5`。
- A1 通过门槛固定为：
  - 平均分 `>= 4.0`
  - 严重幻觉样本 `<= 1 / 20`
  - summary 超长率 `< 5%`
  - detailed 超长率 `< 10%`

### C1：Baseline TailEvents 生成

目标：
- 为已有仓库建立静态基线记忆，但不预生成 explanation。

固定范围：
- `C1` 不调用 LLM。
- `C1` 不预生成 explanation。
- `C1` 只做文件扫描、baseline event 写入、索引更新。
- 粒度固定为 `一文件一事件`，不是 `一实体一事件`。

实现约束：
- 事件模型必须新增：`ActionType.BASELINE = "baseline"`。
- baseline event 内容固定为：
  - `file_path`
  - `code_snapshot = 文件完整内容`
  - `intent = "Bootstrap existing repository file"`
  - `reasoning = null`
  - `decision_alternatives = null`
- 去重规则固定为：`file_path + content_hash`。
- 相同文件内容不重复写 baseline event。
- 执行方式固定为：
  - 显式命令触发 `TailEvents: Onboard Repository`
  - 后台 file-by-file 流式扫描
  - 每处理完一个文件立刻写入
  - 不等待全仓库扫描完再落库

验收：
- onboarding 不阻塞 `B-next` coding task。
- baseline event 按文件落库。
- 相同内容文件不会重复写 baseline。

## 阶段 2：A2 + 公共基础设施

### A2.1：Panel 流式 explanation

目标：
- 让 panel 不再等待完整 detailed explanation 才显示内容。

实现约束：
- 新增 `GET /api/v1/explain/{entity_id}/stream`。
- 协议固定为 `SSE`。
- Panel 行为固定为：
  - 先展示元数据与 summary。
  - 再流式追加 detailed explanation。
  - 中途失败时保留 summary 和已收到的片段。
- Hover 不走流式，仍然只取 summary。

验收：
- Panel 首 token `< 2s`。
- Panel 在流式过程中可以渐进显示内容，不再整段阻塞。

### A2.2：条件式双模型

目标：
- 在不预设双模型的前提下，根据真实性能结果决定是否拆分 summary / detailed 模型。

实现约束：
- `A1` 完成后做一次性能 checkpoint。
- 判定规则固定为：
  - 如果 `summary p95 > 2s`，或
  - `summary` 在长度受控后仍然明显拖慢 hover 体验，
  - 则启用双模型。
- 如果本地模型已经满足阈值，则 `不做双模型`。
- 一旦启用双模型，配置必须固定增加：
  - `summary_backend`
  - `summary_model`
  - `summary_max_tokens`
  - `summary_timeout_ms`
  - `detailed_backend`
  - `detailed_model`
  - `detailed_max_tokens`
  - `detailed_timeout_ms`

验收：
- 如果 summary p95 已达标，则不启用双模型。
- 如果未达标，则启用双模型并验证 hover latency 改善。

### 缓存失效

目标：
- 让 explanation cache 能正确反映 prompt、模型配置和 baseline onboarding 的变化。

实现约束：
- explanation cache key 必须包含：
  - `prompt_version`
  - `model_profile`
  - `entity_id`
  - `detail_level`
  - `include_relations`
- 代码变更触发的 entity invalidation 继续保留。
- model / profile 变更时必须整体失效对应前缀。
- onboarding 产生 baseline event 时，也要失效涉及文件内实体的 explanation。

验收：
- cache key 已纳入 model profile。
- model/profile 变化后不会命中旧 explanation。

### 并发控制

目标：
- 降低重复 explanation 请求带来的 LLM 浪费和并发压力。

实现约束：
- 后端必须补两项控制：
  - 同一 `entity + detail_level` 的 in-flight explanation 请求去重。
  - explanation LLM 调用增加全局并发上限。
- 当前阶段不做复杂优先级调度。
- 当前阶段不做分布式队列。

验收：
- 同实体并发 explanation 不会重复打 LLM。
- 在并发压力下 explanation 仍保持可控。

### 质量闭环

目标：
- 用最小闭环先观察 explanation 的质量、延迟和缓存表现。

实现约束：
- 第一版只做：
  - 离线人工评测集
  - explanation latency / length / cache hit telemetry
  - admin 或本地日志级别可观测性
- 第一版不做：
  - 前端 thumbs up/down
  - 用户在线反馈回写

验收：
- explanation 的长度、延迟、cache hit 具备可观测性。
- 人工评测样本可以稳定复用。

### 长期存储压力控制

目标：
- 在保持单 SQLite 的前提下，控制后续产品化阶段的存储膨胀。

实现约束：
- 单 SQLite 保持不变。
- baseline event 按 `file_path + content_hash` 去重。
- 不新增 archive 系统。
- explanation cache 继续独立表存储。
- 通过缩短 explanation 和减少重复 baseline event 控制体积。

验收：
- baseline 去重有效。
- explanation cache 体积增长受控。

## 阶段 3：C2/C3 + A3

### C2：Baseline-aware explanation

目标：
- 让用户能明确区分 baseline 来源解释与真实 agent 历史解释。

实现约束：
- explanation 在命中 baseline 事件时必须显式标注：
  - `此解释基于已有代码的基线扫描生成`
  - `不是 agent 会话中的真实创建/修改历史`
- 这条标签只影响 UI 和 explanation 元数据，不改变 A 线主流程。

验收：
- baseline explanation 有明确来源标识。

### C3：图关系补齐

目标：
- 为后续全局路径和 GraphRAG 提供稳定的层级关系基础。

实现约束：
- 必须新增：
  - `module -> class/function`
  - `class -> method`
- 关系类型固定使用：`composed_of`
- 补齐后：
  - `A3` 仍然只用 caller/callee
  - `A5` 才使用这些层级关系做全局路径分析

验收：
- `composed_of` 关系落库正确。

### A3：范围化 explanation v1

目标：
- 增加长期保留的本地快速关系层，但首版只做高置信 caller/callee。

实现约束：
- 首版只允许两类范围：
  - `who calls this`
  - `what this calls`
- 明确不做：
  - 同文件近邻
  - 兄弟方法
  - 低置信启发式“语义相近”
- UI 呈现固定为：
  - 新增一个简短区块：`上下文影响`
  - 只展示 caller / callee
  - 每侧最多 `2` 个实体

验收：
- A3 只展示 caller/callee。
- 不出现同文件噪声实体。

## 阶段 4：B1-B4 完整 Coding 工作台

目标：
- 在 `B-next` 的最小真实 coding agent 基座之上，演进为完整本地 coding 工作台。

能力清单：
- 多文件编辑与更完整的任务规划。
- repo 级搜索 / 观察能力。
- 任务历史
- 多模型 / 多 provider profile
- API key 走 VSCode `SecretStorage`
- MCP / skills 开关
- 多轮任务
- 更完整的 task stream / task cancel / task replay

验收：
- profile 选择可用。
- API key 走 `SecretStorage`。
- task history 可回看。
- MCP / skills 开关可配置。

## 阶段 5：A4 + A5

### A4：外部 Retriever v1

目标：
- 在 A1/A2 稳定后，再引入受控的外部文档检索能力。

实现约束：
- 不引入向量数据库。
- 检索机制固定为：
  - `pydoc/help`：继续保留，走符号直查
  - `README` 与用户 allowlist docs：切 chunk 后进 `SQLite FTS5`
- 每次 explanation 只允许取回：
  - 最多 `2` 个 chunk
  - 每个 chunk 最多 `800` 字
- 不允许整份 README 或整份 docs 直接塞进 prompt。
- 必须新增类型名：
  - `ExternalDocSource`
  - `ExternalDocChunk`
  - `ExternalDocMatch`
- 用户 docs allowlist 由前端维护，后端只接收已授权文件列表。

验收：
- README / allowlist docs 走 FTS5 chunk 检索。
- 每次 explanation 最多带入 `2` 个 chunk。

### A5：GraphRAG / 全局影响路径

目标：
- 在补齐层级关系后，引入全局影响链与子图查询能力。

实现约束：
- A5 依赖 `C3` 完成后再做。
- 首版范围固定为：
  - `entity -> entrypoint/output` 的前 `N` 条最短路径
  - `subgraph` 查询
  - explanation 中新增 `全局影响链` 段落
- A5 不替代 A3。
- 二者边界固定为：
  - `A3`：局部、高置信、快速 caller/callee
  - `A5`：全局、图级、最短路径和子图分析

验收：
- 全局影响链能返回前 `N` 条最短路径。
- 子图查询可用。

## 分阶段测试与验收

### 阶段 1：B-next + A1 + C1

- `B-next`
  - 单轮 prompt 可以建立 task session，并完成 `view -> edit -> verify`。
  - transcript 中可见真实 `view/edit/verify` 步骤。
  - `Model Output` 中可见模型 token 流。
  - 只有 verified draft 才允许 `Apply`。
  - `Apply` 后文件被更新，并写入 `1` 条真实 `RawEvent`。
  - `Cancel` 不会增加额外 `modify` 历史。
  - 后续 hover / panel 能解释这次被接受的修改。
- `A1`
  - summary / detailed 长度控制达标。
  - `20` 条 explanation 人工评测达标。
  - 样本固定为至少 `10` 条 `B-next` 真实任务 explanation + 至少 `10` 条 `C1` baseline 实体 explanation。
  - 维度固定为 `准确性`、`信息密度`、`可读性`。
  - 评分标准固定为 `1-5`。
  - 门槛固定为平均分 `>= 4.0`、严重幻觉 `<= 1/20`、summary 超长率 `< 5%`、detailed 超长率 `< 10%`。
- `C1`
  - onboarding 不阻塞 `B-next` coding task。
  - baseline event 按文件落库。
  - 相同内容文件不会重复写 baseline。

### 阶段 2：A2 + 公共基础设施

- Panel 首 token `< 2s`。
- 如果 summary p95 已达标，则不启用双模型。
- 如果未达标，再落双模型，并验证 hover latency 改善。
- cache key 已纳入 model profile。
- 同实体并发 explanation 不会重复打 LLM。

### 阶段 3：C2/C3 + A3

- baseline explanation 有明确来源标识。
- `composed_of` 关系落库正确。
- A3 只展示 caller/callee，不出现同文件噪声实体。

### 阶段 4：B1-B4

- 在 `B-next` 的最小真实 agent 能力之上继续扩展。
- profile 选择可用。
- API key 走 `SecretStorage`。
- task history 可回看。
- MCP / skills 开关可配置。

### 阶段 5：A4 + A5

- README / allowlist docs 走 FTS5 chunk 检索。
- 每次 explanation 最多带入 `2` 个 chunk。
- 全局影响链能返回前 `N` 条最短路径。

## 默认假设

- 当前后端和前端 MVP 已经是现有基础，不重做。
- `B-next` 替代原 `B0`，成为当前实际执行的产品线 B 起点。
- `B-next` 前置只为生成真实任务数据并验证最小真实 agent 闭环，不要求直接达到 Roo Code 完整体验。
- `C1` 只做结构化 baseline events，不做 LLM explanation 预烘焙。
- `双模型` 是 checkpoint 决策，不是默认实施项。
- `A3` 首版只保留 caller/callee，高噪声范围一律不做。
- `A4` 采用 `SQLite FTS5`，不引入向量数据库。
- 当前阶段不新增文档反馈 UI，先用人工评测和埋点做质量闭环。

## 本阶段明确不做

- 不重做现有 Requirement A 后端。
- 不重做现有 VSCode Extension MVP。
- 不把双模型作为默认方案前置落地。
- 不在 `C1` 预生成 explanation。
- 不在 `A4` 引入向量数据库。
- 不做前端 thumbs up/down 或其他在线反馈 UI。
