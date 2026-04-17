"""Format raw LLM output into EntityExplanation models."""

import re
from typing import Optional

from tailevents.models.entity import CodeEntity
from tailevents.models.explanation import EntityExplanation


SUMMARY_MAX_CHARS = 120
SUMMARY_MAX_SENTENCES = 2
DETAILED_MAX_CHARS = 1200
DETAILED_SECTION_ORDER = ["核心作用", "关键上下文", "关键事件"]
DETAILED_SECTION_LIMITS = {
    "核心作用": 220,
    "关键上下文": 320,
    "关键事件": 520,
}
LEGACY_SECTION_ORDER = ["作用", "参数", "返回值", "使用场景", "设计背景"]
ALL_SECTION_HEADERS = DETAILED_SECTION_ORDER + ["关联实体"] + LEGACY_SECTION_ORDER
SECTION_PATTERN = re.compile(
    r"^\s{0,3}(?:(?:#{1,6}|>)\s*)?"
    r"(核心作用|关键上下文|关键事件|关联实体|作用|参数|返回值|使用场景|设计背景)"
    r"\s*[:：]?\s*(.*)$"
)
SENTENCE_PATTERN = re.compile(r"[^。！？!?]+[。！？!?]?")


class ExplanationFormatter:
    """Parse structured LLM output with graceful fallback."""

    def format(
        self,
        entity: CodeEntity,
        raw_output: str,
        detail_level: str = "detailed",
    ) -> EntityExplanation:
        if detail_level == "summary":
            return self._format_summary(entity, raw_output)
        if detail_level == "trace":
            return self._format_trace(entity, raw_output)
        return self._format_detailed(entity, raw_output)

    def _format_summary(self, entity: CodeEntity, raw_output: str) -> EntityExplanation:
        text = raw_output.strip()
        sections = self._extract_sections(text)
        summary_source = sections.get("核心作用") or sections.get("作用") or text
        return EntityExplanation(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type,
            signature=entity.signature,
            summary=self._format_summary_text(summary_source),
            detailed_explanation=None,
        )

    def _format_detailed(self, entity: CodeEntity, raw_output: str) -> EntityExplanation:
        text = raw_output.strip()
        sections = self._extract_sections(text)
        if self._has_new_sections(sections):
            normalized_sections = self._normalize_detailed_sections(sections)
            detailed_explanation = self._build_detailed_explanation(normalized_sections)
            return EntityExplanation(
                entity_id=entity.entity_id,
                entity_name=entity.name,
                qualified_name=entity.qualified_name,
                entity_type=entity.entity_type,
                signature=entity.signature,
                summary=self._format_summary_text(normalized_sections["核心作用"]),
                detailed_explanation=detailed_explanation,
            )

        if self._has_legacy_sections(sections):
            return self._format_legacy_detailed(entity, text, sections)

        normalized_text = self._normalize_inline_text(text)
        return EntityExplanation(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type,
            signature=entity.signature,
            summary=self._format_summary_text(normalized_text),
            detailed_explanation=self._truncate_with_ellipsis(
                normalized_text,
                DETAILED_MAX_CHARS,
            )
            or None,
        )

    def _format_trace(self, entity: CodeEntity, raw_output: str) -> EntityExplanation:
        text = raw_output.strip()
        sections = self._extract_sections(text)
        if self._has_legacy_sections(sections):
            effect = self._normalize_section(sections.get("作用"))
            usage_context = self._normalize_section(sections.get("使用场景"))
            design_background = self._normalize_section(sections.get("设计背景"))
            return_explanation = self._normalize_section(sections.get("返回值"))
            params = self._parse_params(self._normalize_section(sections.get("参数")))

            return EntityExplanation(
                entity_id=entity.entity_id,
                entity_name=entity.name,
                qualified_name=entity.qualified_name,
                entity_type=entity.entity_type,
                signature=entity.signature,
                summary=self._format_summary_text(effect or text),
                detailed_explanation=self._truncate_with_ellipsis(text, DETAILED_MAX_CHARS)
                or None,
                param_explanations=params,
                return_explanation=return_explanation,
                usage_context=usage_context,
                creation_intent=design_background,
            )

        return EntityExplanation(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type,
            signature=entity.signature,
            summary=self._format_summary_text(text),
            detailed_explanation=self._truncate_with_ellipsis(text, DETAILED_MAX_CHARS)
            or None,
        )

    def _format_legacy_detailed(
        self,
        entity: CodeEntity,
        raw_output: str,
        sections: dict[str, str],
    ) -> EntityExplanation:
        effect = self._normalize_section(sections.get("作用"))
        usage_context = self._normalize_section(sections.get("使用场景"))
        design_background = self._normalize_section(sections.get("设计背景"))
        return_explanation = self._normalize_section(sections.get("返回值"))
        params = self._parse_params(self._normalize_section(sections.get("参数")))

        normalized_sections = {
            "核心作用": self._truncate_with_ellipsis(effect or "未提供。", 220),
            "关键上下文": self._truncate_with_ellipsis(usage_context or "未提供。", 320),
            "关键事件": self._normalize_bullet_section(design_background, 3, 520),
        }
        detailed_explanation = self._build_detailed_explanation(normalized_sections)

        return EntityExplanation(
            entity_id=entity.entity_id,
            entity_name=entity.name,
            qualified_name=entity.qualified_name,
            entity_type=entity.entity_type,
            signature=entity.signature,
            summary=self._format_summary_text(effect or raw_output),
            detailed_explanation=detailed_explanation,
            param_explanations=params,
            return_explanation=return_explanation,
            usage_context=usage_context,
            creation_intent=design_background,
        )

    def _extract_sections(self, text: str) -> dict[str, str]:
        sections: dict[str, list[str]] = {}
        current_key: Optional[str] = None

        for line in text.splitlines():
            match = SECTION_PATTERN.match(line)
            if match:
                current_key = match.group(1)
                sections.setdefault(current_key, [])
                inline_content = match.group(2).strip()
                if inline_content:
                    sections[current_key].append(inline_content)
                continue

            if current_key is not None:
                sections[current_key].append(line)

        return {
            key: "\n".join(value).strip()
            for key, value in sections.items()
            if "\n".join(value).strip()
        }

    def _has_new_sections(self, sections: dict[str, str]) -> bool:
        return any(header in sections for header in DETAILED_SECTION_ORDER)

    def _has_legacy_sections(self, sections: dict[str, str]) -> bool:
        return any(header in sections for header in LEGACY_SECTION_ORDER)

    def _normalize_detailed_sections(self, sections: dict[str, str]) -> dict[str, str]:
        return {
            "核心作用": self._truncate_with_ellipsis(
                self._normalize_inline_text(sections.get("核心作用") or "未提供。"),
                DETAILED_SECTION_LIMITS["核心作用"],
            ),
            "关键上下文": self._truncate_with_ellipsis(
                self._normalize_inline_text(sections.get("关键上下文") or "未提供。"),
                DETAILED_SECTION_LIMITS["关键上下文"],
            ),
            "关键事件": self._normalize_bullet_section(
                sections.get("关键事件"),
                max_items=3,
                max_chars=DETAILED_SECTION_LIMITS["关键事件"],
            ),
        }

    def _build_detailed_explanation(self, sections: dict[str, str]) -> str:
        normalized = dict(sections)
        text = self._join_detailed_sections(normalized)
        if len(text) <= DETAILED_MAX_CHARS:
            return text

        overflow = len(text) - DETAILED_MAX_CHARS
        for header in reversed(DETAILED_SECTION_ORDER):
            body = normalized.get(header, "")
            if not body:
                continue
            new_limit = max(len(body) - overflow, 4)
            normalized[header] = self._truncate_with_ellipsis(body, new_limit)
            break
        return self._truncate_with_ellipsis(
            self._join_detailed_sections(normalized),
            DETAILED_MAX_CHARS,
        )

    def _join_detailed_sections(self, sections: dict[str, str]) -> str:
        blocks = []
        for header in DETAILED_SECTION_ORDER:
            body = sections.get(header) or "未提供。"
            blocks.append(f"{header}\n{body}")
        return "\n\n".join(blocks)

    def _normalize_bullet_section(
        self,
        text: Optional[str],
        max_items: int,
        max_chars: int,
    ) -> str:
        items = self._extract_bullet_items(text)
        if not items:
            return "未提供。"
        lines = [f"- {item}" for item in items[:max_items]]
        return self._truncate_with_ellipsis("\n".join(lines), max_chars)

    def _extract_bullet_items(self, text: Optional[str]) -> list[str]:
        if not text:
            return []

        items: list[str] = []
        for line in text.splitlines():
            normalized = self._normalize_inline_text(line)
            if not normalized:
                continue
            normalized = normalized.lstrip("-*").strip()
            if not normalized:
                continue
            items.append(normalized)

        if items:
            return items

        paragraph = self._normalize_inline_text(text)
        if not paragraph:
            return []
        return [paragraph]

    def _parse_params(self, text: Optional[str]) -> Optional[dict[str, str]]:
        if not text:
            return None

        parsed: dict[str, str] = {}
        current_name: Optional[str] = None
        current_parts: list[str] = []

        def flush_current() -> None:
            nonlocal current_name, current_parts
            if current_name and current_parts:
                parsed[current_name] = " ".join(part for part in current_parts if part).strip()
            current_name = None
            current_parts = []

        for line in text.splitlines():
            normalized = line.strip().lstrip("-*").strip()
            if not normalized:
                continue

            separator = "：" if "：" in normalized else ":"
            if separator in normalized:
                name, description = normalized.split(separator, 1)
                name = self._normalize_param_name(name)
                description = description.strip()
                if name in {"类型", "作用", "说明", "含义"} and current_name:
                    current_parts.append(f"{name}：{description}")
                    continue
                flush_current()
                if name and description:
                    parsed[name] = description
                continue

            candidate_name = self._normalize_param_name(normalized)
            if candidate_name:
                flush_current()
                current_name = candidate_name
                continue

            if current_name:
                current_parts.append(normalized)

        flush_current()
        return parsed or None

    def _normalize_param_name(self, name: str) -> str:
        normalized = name.strip()
        while normalized.startswith("`") and normalized.endswith("`") and len(normalized) >= 2:
            normalized = normalized[1:-1].strip()
        return normalized

    def _normalize_section(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        normalized = text.strip()
        return normalized or None

    def _format_summary_text(self, text: str) -> str:
        normalized = self._normalize_inline_text(text)
        if not normalized:
            return "No explanation available."

        sentences = self._split_sentences(normalized)
        candidate = "".join(sentences[:SUMMARY_MAX_SENTENCES]) if sentences else normalized
        return self._truncate_with_ellipsis(candidate, SUMMARY_MAX_CHARS)

    def _split_sentences(self, text: str) -> list[str]:
        matches = [item.strip() for item in SENTENCE_PATTERN.findall(text) if item.strip()]
        if matches:
            return matches
        return [text]

    def _normalize_inline_text(self, text: Optional[str]) -> str:
        if not text:
            return ""
        return re.sub(r"\s+", " ", text).strip()

    def _truncate_with_ellipsis(self, text: str, max_length: int) -> str:
        normalized = text.strip()
        if len(normalized) <= max_length:
            return normalized
        if max_length <= 3:
            return normalized[:max_length]
        return f"{normalized[: max_length - 3].rstrip()}..."


__all__ = [
    "DETAILED_SECTION_ORDER",
    "ExplanationFormatter",
    "LEGACY_SECTION_ORDER",
]
