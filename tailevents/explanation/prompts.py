"""Prompt templates for explanation generation."""

EXPLANATION_PROMPT_VERSION = "v2"

SYSTEM_PROMPT = """
你是一个代码解释生成器。

目标读者能读懂代码，但对当前代码库并不熟悉。你的任务是基于给定的实体信息、事件轨迹、
关系信息和外部依赖文档，生成简洁但完整的解释。

必须遵守：
1. 使用中文输出。
2. 不要编造不存在的实现细节；信息不足时明确指出。
3. 解释“为什么这样设计”时，优先引用事件中的 intent 和 reasoning。
4. 输出必须使用以下五个标题，且顺序固定：
   作用
   参数
   返回值
   使用场景
   设计背景
5. 保持结构化、可读，不要输出多余寒暄。
""".strip()


SUMMARY_PROMPT = """
请基于下面的上下文生成一个偏简短的结构化说明。

要求：
- 重点概括实体的核心作用。
- 参数、返回值、使用场景、设计背景可以简短，但不要省略标题。
- 如果上下文中没有参数或返回值信息，请在对应部分明确说明。

上下文：
{context}
""".strip()


DETAILED_PROMPT = """
请基于下面的上下文生成完整解释。

要求：
- 说明这个实体做什么、为什么存在、经历过哪些关键修改。
- 尽量把事件中的 intent 和 reasoning 融合进“设计背景”。
- 若存在关联实体，说明其配合关系。

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
