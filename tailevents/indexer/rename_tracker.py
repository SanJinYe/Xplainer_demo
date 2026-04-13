"""Rename detection helpers."""

from difflib import SequenceMatcher
from typing import Any, Optional

from tailevents.models.entity import CodeEntity
from tailevents.models.enums import Provenance

BODY_HASH_TAG_PREFIX = "__body_hash__:"
BODY_TEXT_TAG_PREFIX = "__body_norm__:"


class RenameTracker:
    """Detect likely renames between disappeared and appeared entities."""

    def __init__(self, similarity_threshold: float = 0.8):
        self._similarity_threshold = similarity_threshold

    def detect_renames(
        self,
        disappeared: list[CodeEntity],
        appeared: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        used_old_ids: set[str] = set()
        used_new_qnames: set[str] = set()

        for old_entity in disappeared:
            old_hash = self._get_body_hash(old_entity)
            if old_hash is None:
                continue
            for new_entity in appeared:
                if new_entity["qualified_name"] in used_new_qnames:
                    continue
                if new_entity.get("body_hash") != old_hash:
                    continue
                matches.append(
                    {
                        "old_entity_id": old_entity.entity_id,
                        "old_qualified_name": old_entity.qualified_name,
                        "new_qualified_name": new_entity["qualified_name"],
                        "confidence": 1.0,
                        "provenance": Provenance.AST_DERIVED.value,
                    }
                )
                used_old_ids.add(old_entity.entity_id)
                used_new_qnames.add(new_entity["qualified_name"])
                break

        for old_entity in disappeared:
            if old_entity.entity_id in used_old_ids:
                continue

            old_body = self._get_normalized_body(old_entity)
            best_match: Optional[dict[str, Any]] = None
            best_similarity = 0.0

            for new_entity in appeared:
                if new_entity["qualified_name"] in used_new_qnames:
                    continue
                new_body = new_entity.get("normalized_body", "")
                similarity = self._similarity(old_body, new_body)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = new_entity

            if best_match is None or best_similarity < self._similarity_threshold:
                continue

            matches.append(
                {
                    "old_entity_id": old_entity.entity_id,
                    "old_qualified_name": old_entity.qualified_name,
                    "new_qualified_name": best_match["qualified_name"],
                    "confidence": best_similarity,
                    "provenance": Provenance.INFERRED.value,
                }
            )
            used_old_ids.add(old_entity.entity_id)
            used_new_qnames.add(best_match["qualified_name"])

        return matches

    def _similarity(self, left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        return SequenceMatcher(None, left, right).ratio()

    def _get_body_hash(self, entity: CodeEntity) -> Optional[str]:
        for tag in entity.tags:
            if tag.startswith(BODY_HASH_TAG_PREFIX):
                return tag[len(BODY_HASH_TAG_PREFIX) :]
        return None

    def _get_normalized_body(self, entity: CodeEntity) -> str:
        for tag in entity.tags:
            if tag.startswith(BODY_TEXT_TAG_PREFIX):
                return tag[len(BODY_TEXT_TAG_PREFIX) :]
        return "\n".join(
            part for part in [entity.signature or "", entity.docstring or ""] if part
        )


__all__ = ["BODY_HASH_TAG_PREFIX", "BODY_TEXT_TAG_PREFIX", "RenameTracker"]
