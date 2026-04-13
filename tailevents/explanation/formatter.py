"""Format raw LLM output into EntityExplanation models."""

import re
from typing import Optional

from tailevents.models.entity import CodeEntity
from tailevents.models.explanation import EntityExplanation


SECTION_ORDER = ["作用", "参数", "返回值", "使用场景", "设计背景"]
SECTION_PATTERN = re.compile(
    r"^\s{0,3}(?:[#>*-]+\s*)?(作用|参数|返回值|使用场景|设计背景)\s*[:：]?\s*(.*)$"
)


class ExplanationFormatter:
    """Parse structured LLM output with graceful fallback."""

    def format(self, entity: CodeEntity, raw_output: str) -> EntityExplanation:
        text = raw_output.strip()
        sections = self._extract_sections(text)

        if not sections:
            return EntityExplanation(
                entity_id=entity.entity_id,
                entity_name=entity.name,
                qualified_name=entity.qualified_name,
                entity_type=entity.entity_type,
                signature=entity.signature,
                summary=self._fallback_summary(text),
                detailed_explanation=text or None,
            )

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
            summary=self._summarize_effect(effect, text),
            detailed_explanation=text or None,
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

    def _parse_params(self, text: Optional[str]) -> Optional[dict[str, str]]:
        if not text:
            return None

        parsed: dict[str, str] = {}
        for line in text.splitlines():
            normalized = line.strip().lstrip("-*").strip()
            if not normalized:
                continue
            separator = "：" if "：" in normalized else ":"
            if separator not in normalized:
                continue
            name, description = normalized.split(separator, 1)
            name = name.strip()
            description = description.strip()
            if name and description:
                parsed[name] = description

        return parsed or None

    def _normalize_section(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        normalized = text.strip()
        return normalized or None

    def _summarize_effect(self, effect: Optional[str], fallback_text: str) -> str:
        if effect:
            first_line = next(
                (line.strip() for line in effect.splitlines() if line.strip()),
                "",
            )
            if first_line:
                return self._truncate(first_line)
        return self._fallback_summary(fallback_text)

    def _fallback_summary(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return "No explanation available."
        first_paragraph = stripped.split("\n\n", 1)[0].replace("\n", " ").strip()
        return self._truncate(first_paragraph)

    def _truncate(self, text: str, max_length: int = 160) -> str:
        if len(text) <= max_length:
            return text
        return f"{text[:max_length].rstrip()}..."


__all__ = ["ExplanationFormatter", "SECTION_ORDER"]
