"""Unified diff parser."""

import re
from typing import Any, Optional


_HUNK_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@")


class DiffParser:
    """Parse unified diff content or treat input as a full source file."""

    def parse(self, content: str, file_path: Optional[str] = None) -> list[dict[str, Any]]:
        if "--- " not in content or "+++ " not in content:
            return [
                {
                    "file_path": file_path,
                    "added_lines": [],
                    "removed_lines": [],
                    "modified_lines": [],
                    "source": content,
                    "is_diff": False,
                }
            ]

        changes: list[dict[str, Any]] = []
        current: Optional[dict[str, Any]] = None
        pending_old_path: Optional[str] = None
        old_line = 0
        new_line = 0
        block_added: list[int] = []
        block_removed: list[int] = []

        def flush_modified_block() -> None:
            if current is None:
                return
            if block_added and block_removed:
                current["modified_lines"].extend(block_added)
            block_added.clear()
            block_removed.clear()

        def finalize_current() -> None:
            if current is None:
                return
            flush_modified_block()
            current["source"] = "\n".join(current.pop("_source_lines"))
            changes.append(current.copy())

        for line in content.splitlines():
            if line.startswith("--- "):
                pending_old_path = self._normalize_path(line[4:])
                continue

            if line.startswith("+++ "):
                if current is not None:
                    finalize_current()
                new_path = self._normalize_path(line[4:])
                current = {
                    "file_path": None if new_path == "/dev/null" else new_path or pending_old_path or file_path,
                    "old_file_path": pending_old_path,
                    "added_lines": [],
                    "removed_lines": [],
                    "modified_lines": [],
                    "_source_lines": [],
                    "is_diff": True,
                }
                continue

            if current is None:
                continue

            hunk = _HUNK_RE.match(line)
            if hunk:
                flush_modified_block()
                old_line = int(hunk.group("old_start"))
                new_line = int(hunk.group("new_start"))
                continue

            if line.startswith("+") and not line.startswith("+++"):
                current["added_lines"].append(new_line)
                current["_source_lines"].append(line[1:])
                block_added.append(new_line)
                new_line += 1
                continue

            if line.startswith("-") and not line.startswith("---"):
                current["removed_lines"].append(old_line)
                block_removed.append(old_line)
                old_line += 1
                continue

            if line.startswith(" "):
                flush_modified_block()
                current["_source_lines"].append(line[1:])
                old_line += 1
                new_line += 1
                continue

            if line == r"\ No newline at end of file":
                continue

        if current is not None:
            finalize_current()

        return changes

    def _normalize_path(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if stripped.startswith("a/") or stripped.startswith("b/"):
            return stripped[2:]
        return stripped


__all__ = ["DiffParser"]
