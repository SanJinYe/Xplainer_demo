# Test Tutorial

本文档用于在 `http://127.0.0.1:8766/docs` 中手动调试 TailEvents，按 5 个事件一步步模拟一整个 coding session。

## 准备

1. 启动服务：

```bash
python -m tailevents.main --host 127.0.0.1 --port 8766
```

2. 打开 Swagger：

```text
http://127.0.0.1:8766/docs
```

3. 建议固定使用同一个 `session_id`：

```text
session_tutorial_1
```

4. 推荐调试顺序：

```text
POST /api/v1/events
GET  /api/v1/entities
POST /api/v1/explain
GET  /api/v1/relations/{entity_id}/outgoing
GET  /api/v1/admin/stats
```

---

## Session Goal

我们模拟这个演化过程：

1. 在 `api.py` 中创建 `fetch_data`
2. 在 `processor.py` 中创建 `DataProcessor`
3. 修改 `DataProcessor`，让它调用 `fetch_data`
4. 创建 `main`，实例化 `DataProcessor`
5. 把 `fetch_data` 重命名为 `fetch_api_data`

期望看到：

- entities 被正确建立
- `DataProcessor.process` 到 `fetch_data/fetch_api_data` 的 relation 被提取
- rename 被记录在 `rename_history`
- explanation 能引用 creation intent
- 第二次 explanation 命中 cache

---

## Event 1

接口：

```text
POST /api/v1/events
```

JSON：

```json
{
  "action_type": "create",
  "file_path": "api.py",
  "code_snapshot": "def fetch_data(url):\n    return url\n",
  "intent": "create a reusable HTTP fetch helper",
  "reasoning": "start from the smallest callable unit",
  "decision_alternatives": [
    "put logic inside main directly",
    "extract helper first"
  ],
  "agent_step_id": "step_1",
  "session_id": "session_tutorial_1",
  "line_range": [
    1,
    2
  ],
  "external_refs": []
}
```

检查点：

- 返回 `201`
- 记录下返回里的 `event_id`
- `entity_refs` 里应出现 `primary`

---

## Event 2

接口：

```text
POST /api/v1/events
```

JSON：

```json
{
  "action_type": "create",
  "file_path": "processor.py",
  "code_snapshot": "class DataProcessor:\n    def process(self, url):\n        return url.upper()\n",
  "intent": "create a processor class around incoming data",
  "reasoning": "separate processing responsibility from fetching",
  "agent_step_id": "step_2",
  "session_id": "session_tutorial_1",
  "line_range": [
    1,
    3
  ],
  "external_refs": []
}
```

检查点：

- 返回 `201`
- 应新增 `DataProcessor`
- 通常还会有 `DataProcessor.process`

---

## Event 3

接口：

```text
POST /api/v1/events
```

JSON：

```json
{
  "action_type": "modify",
  "file_path": "processor.py",
  "code_snapshot": "from api import fetch_data\n\nclass DataProcessor:\n    def process(self, url):\n        data = fetch_data(url)\n        return data.upper()\n",
  "intent": "wire DataProcessor to call the fetch helper",
  "reasoning": "centralize retrieval before transforming data",
  "decision_alternatives": [
    "let process accept ready-made data",
    "call helper inside the method"
  ],
  "agent_step_id": "step_3",
  "session_id": "session_tutorial_1",
  "line_range": [
    1,
    6
  ],
  "external_refs": []
}
```

检查点：

- 返回 `201`
- `DataProcessor.process` 会被重新索引
- 后面查询 relation 时应能看到它调用 `fetch_data`

---

## Event 4

接口：

```text
POST /api/v1/events
```

JSON：

```json
{
  "action_type": "create",
  "file_path": "app.py",
  "code_snapshot": "from processor import DataProcessor\n\ndef main(url):\n    processor = DataProcessor()\n    return processor.process(url)\n",
  "intent": "add an executable entry point for the processor workflow",
  "reasoning": "make the flow callable from a single place",
  "agent_step_id": "step_4",
  "session_id": "session_tutorial_1",
  "line_range": [
    1,
    5
  ],
  "external_refs": []
}
```

检查点：

- 返回 `201`
- 应新增 `main`
- 后面可观察 `main` 和 `DataProcessor` 之间的关系

---

## Event 5

接口：

```text
POST /api/v1/events
```

JSON：

```json
{
  "action_type": "rename",
  "file_path": "api.py",
  "code_snapshot": "def fetch_api_data(url):\n    return url\n",
  "intent": "rename fetch_data to better reflect API responsibility",
  "reasoning": "make the helper name more explicit before more callers appear",
  "agent_step_id": "step_5",
  "session_id": "session_tutorial_1",
  "line_range": [
    1,
    2
  ],
  "external_refs": []
}
```

检查点：

- 返回 `201`
- 旧的 `fetch_data` 不应再作为 active entity 出现
- `fetch_api_data` 应该保留原来的 `entity_id`
- `rename_history` 里应看到 `fetch_data -> fetch_api_data`

---

## Query 1: List Entities

接口：

```text
GET /api/v1/entities
```

参数：

```text
skip = 0
limit = 50
```

你应该能找到这些实体：

- `fetch_api_data`
- `DataProcessor`
- `DataProcessor.process`
- `main`

此时请记录：

- `fetch_api_data.entity_id`
- `DataProcessor.entity_id`
- `DataProcessor.process.entity_id`

---

## Query 2: Inspect Rename

接口：

```text
GET /api/v1/entities/{entity_id}
```

把 `fetch_api_data.entity_id` 填进去。

重点看响应里的：

```json
"rename_history"
```

应该能看到类似：

```json
[
  {
    "old_qualified_name": "fetch_data",
    "new_qualified_name": "fetch_api_data"
  }
]
```

---

## Query 3: Inspect Relations

接口：

```text
GET /api/v1/relations/{entity_id}/outgoing
```

把 `DataProcessor.process.entity_id` 填进去。

期望看到：

- `calls`
- target 指向 `fetch_api_data` 的 `entity_id`

如果你想反向看：

```text
GET /api/v1/relations/{entity_id}/incoming
```

把 `fetch_api_data.entity_id` 填进去。

---

## Query 4: Explain by Symbol

接口：

```text
POST /api/v1/explain
```

JSON：

```json
{
  "query": "DataProcessor",
  "detail_level": "detailed",
  "include_relations": true
}
```

检查点：

- `explanations` 不为空
- `explanations[0].creation_intent` 应接近：

```text
create a processor class around incoming data
```

- `from_cache` 第一次应是 `false`

---

## Query 5: Cache Hit

再次执行上一条完全相同的请求：

```json
{
  "query": "DataProcessor",
  "detail_level": "detailed",
  "include_relations": true
}
```

第二次的检查点：

- `from_cache` 应为 `true`

---

## Query 6: Explain by Location

接口：

```text
POST /api/v1/explain
```

JSON：

```json
{
  "query": "fallback text",
  "file_path": "processor.py",
  "line_number": 4,
  "cursor_word": "process",
  "detail_level": "summary",
  "include_relations": false
}
```

说明：

- 当前路由优先级是 `location > cursor_word > query`
- 所以这里应该优先命中 `DataProcessor.process`

---

## Query 7: Session Events

接口：

```text
GET /api/v1/events
```

参数：

```text
session = session_tutorial_1
```

期望看到：

- 一共 5 条事件
- 顺序是 Event 1 到 Event 5

---

## Query 8: Admin Stats

接口：

```text
GET /api/v1/admin/stats
```

重点看这些字段：

- `entity_count`
- `event_count`
- `relation_count`
- `cache_hits`
- `cache_misses`

在完成上面的 explain 测试后，应该至少看到：

- `event_count >= 5`
- `cache_hits >= 1`
- `cache_misses >= 1`

---

## Fast Failure Checklist

如果结果不符合预期，按这个顺序查：

1. `POST /api/v1/events` 是否都返回 `201`
2. `GET /api/v1/entities` 是否已经出现目标实体
3. `GET /api/v1/relations/...` 是否已经出现调用关系
4. `/api/v1/explain` 返回为空时，优先检查：
   - `.env` 里的 LLM backend
   - API key / model 是否正确
   - 目标实体是否真实存在
5. rename 不生效时，确认 Event 5 的函数体没有改，只改了名字

---

## One More Shortcut

如果你只想快速验证最核心链路，最少做这 4 步：

1. 发 Event 1
2. 发 Event 2
3. 发 Event 3
4. 调这个 explain 请求：

```json
{
  "query": "DataProcessor",
  "detail_level": "detailed",
  "include_relations": true
}
```

这样已经能覆盖：

- ingestion
- indexer
- relation extraction
- explanation
- cache
