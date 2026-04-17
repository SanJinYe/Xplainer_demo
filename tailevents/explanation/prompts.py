"""Prompt templates for explanation generation."""

EXPLANATION_PROMPT_VERSION = "v4"

SYSTEM_PROMPT = """
你是一个代码解释生成器。
目标读者能读懂代码，但对当前代码库并不熟悉。你的任务是基于给定的实体信息、事件轨迹、
关系信息和外部依赖文档，生成简洁但完整的解释。
必须遵守：
1. 使用中文输出。
2. 不要编造不存在的实现细节；信息不足时明确指出。
3. 解释“为什么这样设计”时，优先引用事件中的 intent 和 reasoning。
4. 如果上下文中只有 baseline 事件，且其中没有 reasoning 或 decision alternatives，
   不要猜测创建动机、设计初衷或替代方案，只描述代码当前结构、行为和直接上下文角色。
5. 保持结构化、可读，不要输出多余客套话。
""".strip()


SUMMARY_PROMPT = """
请基于下面的上下文生成一个极短摘要。
要求：
- 不要使用任何标题。
- 最多 2 句。
- 最多 120 中文字。
- 只回答“它做什么”和“对上下文的直接作用”。
- 如果上下文里没有明确设计动机，不要猜测。
上下文：
{context}
""".strip()


DETAILED_PROMPT = """
请基于下面的上下文生成默认详细解释。
要求：
- 只能使用以下三个标题，且顺序固定：
  核心作用
  关键上下文
  关键事件
- 不要输出参数、返回值、使用场景、设计背景、关联实体等额外标题。
- `关键事件` 最多 3 条 bullet，每条只概括一条事件。
- 如果上下文中只有 baseline 事件且没有 reasoning，不要猜测设计初衷，
  只说明当前代码结构、行为和直接上下文。
上下文：
{context}
""".strip()


TRACE_PROMPT = """
请基于下面的上下文生成带时间线感的完整解释。
要求：
- 在“设计背景”中按时间顺序概括关键 event 的演变。
- 如果存在 decision alternatives，请说明最终选择的原因。
- 保持五段结构，不要额外添加标题。
上下文：
{context}
""".strip()


EXTERNAL_DOC_PROMPT = """
以下是该实体相关外部依赖的参考文档片段，仅在与当前实现直接相关时使用。
不要逐字复述文档，而是提炼出对解释有帮助的结论。
外部依赖上下文：
{external_context}
""".strip()


PROMPT_TEMPLATES = {
    "summary": SUMMARY_PROMPT,
    "detailed": DETAILED_PROMPT,
    "trace": TRACE_PROMPT,
}


__all__ = [
    "DETAILED_PROMPT",
    "EXPLANATION_PROMPT_VERSION",
    "EXTERNAL_DOC_PROMPT",
    "PROMPT_TEMPLATES",
    "SUMMARY_PROMPT",
    "SYSTEM_PROMPT",
    "TRACE_PROMPT",
]
