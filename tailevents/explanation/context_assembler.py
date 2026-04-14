"""Assemble structured context for explanation prompts."""

from collections import defaultdict

from tailevents.explanation.exceptions import InvalidDetailLevelError
from tailevents.models.entity import CodeEntity
from tailevents.models.event import TailEvent


VALID_DETAIL_LEVELS = {"summary", "detailed", "trace"}


class ContextAssembler:
    """Convert entity metadata, events, relations, and docs into prompt context."""

    def assemble(
        self,
        entity: CodeEntity,
        events: list[TailEvent],
        related_entities: list[dict],
        doc_snippets: list[dict],
        detail_level: str,
    ) -> str:
        if detail_level not in VALID_DETAIL_LEVELS:
            raise InvalidDetailLevelError(f"Unsupported detail level: {detail_level}")

        ordered_events = sorted(events, key=lambda item: item.timestamp)
        sections = [
            self._format_target_entity(entity),
            self._format_creation_context(ordered_events),
        ]

        if detail_level in {"detailed", "trace"}:
            sections.append(self._format_modification_history(ordered_events))

        relation_section = self._format_relations(related_entities)
        if relation_section:
            sections.append(relation_section)

        if doc_snippets:
            sections.append(self._format_external_docs(doc_snippets))

        if detail_level == "trace":
            sections.append(self._format_event_trace(ordered_events))

        return "\n\n".join(section for section in sections if section)

    def _format_target_entity(self, entity: CodeEntity) -> str:
        lines = [
            "# Target Entity",
            f"Type: {entity.entity_type.value}",
            f"Qualified Name: {entity.qualified_name}",
            f"Name: {entity.name}",
            f"File: {entity.file_path}",
        ]
        if entity.line_range is not None:
            lines.append(f"Line Range: {entity.line_range[0]}-{entity.line_range[1]}")
        if entity.signature:
            lines.append(f"Signature: {entity.signature}")
        if entity.docstring:
            lines.append(f"Docstring: {entity.docstring}")
        return "\n".join(lines)

    def _format_creation_context(self, events: list[TailEvent]) -> str:
        if not events:
            return "# Creation Context\nCreation event not available."

        event = events[0]
        lines = [
            "# Creation Context",
            f"Event: {event.event_id}",
            f"Timestamp: {event.timestamp.isoformat()}",
            f"Action: {event.action_type.value}",
            f"Intent: {event.intent}",
        ]
        if event.reasoning:
            lines.append(f"Reasoning: {event.reasoning}")
        return "\n".join(lines)

    def _format_modification_history(self, events: list[TailEvent]) -> str:
        if len(events) <= 1:
            return "# Modification History\nNo later modifications recorded."

        lines = ["# Modification History"]
        for event in events[1:]:
            lines.extend(
                [
                    f"- Event {event.event_id} @ {event.timestamp.isoformat()}",
                    f"  Action: {event.action_type.value}",
                    f"  Intent: {event.intent}",
                ]
            )
            if event.reasoning:
                lines.append(f"  Reasoning: {event.reasoning}")
        return "\n".join(lines)

    def _format_relations(self, related_entities: list[dict]) -> str:
        if not related_entities:
            return ""

        grouped: dict[str, list[str]] = defaultdict(list)
        for relation in related_entities:
            label = self._relation_group_label(relation)
            item = self._relation_item(relation)
            if item not in grouped[label]:
                grouped[label].append(item)

        preferred_order = [
            "This entity is called by:",
            "This entity calls:",
            "Incoming relations:",
            "Outgoing relations:",
        ]
        lines = ["# Related Entities"]
        for label in preferred_order:
            items = grouped.get(label, [])
            if not items:
                continue
            lines.append(label)
            lines.extend(f"- {item}" for item in items)
        return "\n".join(lines)

    def _relation_group_label(self, relation: dict) -> str:
        direction = relation.get("direction")
        relation_type = relation.get("relation_type")
        if direction == "incoming" and relation_type == "calls":
            return "This entity is called by:"
        if direction == "outgoing" and relation_type == "calls":
            return "This entity calls:"
        if direction == "incoming":
            return "Incoming relations:"
        return "Outgoing relations:"

    def _relation_item(self, relation: dict) -> str:
        base = f"{relation['qualified_name']} ({relation['entity_type']})"
        relation_type = relation.get("relation_type")
        direction = relation.get("direction")
        if not (
            (direction == "incoming" and relation_type == "calls")
            or (direction == "outgoing" and relation_type == "calls")
        ):
            base = f"{relation_type}: {base}"
        context = relation.get("context")
        if context:
            return f"{base} - {context}"
        return base

    def _format_external_docs(self, doc_snippets: list[dict]) -> str:
        lines = ["# External Dependencies"]
        for snippet in doc_snippets:
            usage = snippet.get("usage_pattern")
            lines.append(
                f"- {snippet['package']}.{snippet['symbol']} ({usage})"
                if usage
                else f"- {snippet['package']}.{snippet['symbol']}"
            )
            lines.append(f"  Doc: {snippet['snippet']}")
        return "\n".join(lines)

    def _format_event_trace(self, events: list[TailEvent]) -> str:
        if not events:
            return "# Event Trace\nNo event trace available."

        lines = ["# Event Trace"]
        for event in events:
            lines.extend(
                [
                    f"- Event {event.event_id}",
                    f"  Timestamp: {event.timestamp.isoformat()}",
                    f"  Action: {event.action_type.value}",
                    f"  Intent: {event.intent}",
                ]
            )
            if event.reasoning:
                lines.append(f"  Reasoning: {event.reasoning}")
            if event.decision_alternatives:
                alternatives = ", ".join(event.decision_alternatives)
                lines.append(f"  Alternatives: {alternatives}")
        return "\n".join(lines)


__all__ = ["ContextAssembler", "VALID_DETAIL_LEVELS"]
