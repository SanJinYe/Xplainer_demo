# TailEvents Coding Explanation Agent

当前仓库已完成 Phase 1 的基础层：`models + config`。

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
- `settings.py`：`Settings`，基于 `pydantic-settings`
- `__init__.py`：导出 `Settings` 和 `get_settings()`

### 3. 环境与依赖

- `.env.example`：环境变量模板
- `requirements.txt`：当前项目依赖

## 当前状态

- 已完成：`models`、`config`
- 当前阶段：Phase 2（存储层）
- 下一步：实现 `storage/`
  - `database.py`
  - `migrations.py`
  - `event_store.py`
  - `entity_db.py`
  - `relation_store.py`

## 还没有什么

下面这些模块还没有开始实现：

- `storage`
- `indexer`
- `cache`
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

导入模型：

```python
from tailevents.models import TailEvent, CodeEntity, Relation
```

## 参考文档

- `docs/requirements.md`
- `docs/system_design.md`
- `AGENTS.md`
- `CONTEXT.md`
