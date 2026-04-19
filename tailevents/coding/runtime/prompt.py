"""Prompt construction for coding tasks."""

from typing import Optional

from tailevents.coding.context.model import CodingContextBundle
from tailevents.models.task import CodingTaskCreateRequest


SYSTEM_PROMPT = """
You are a coding agent for one or two editable project files.

You already have the exact observed contents of:
- one primary editable target file
- zero or one additional editable files
- zero to three read-only context files

You must return exactly one JSON object and nothing else.

The JSON object must contain exactly:
- edits
- intent
- reasoning

Rules:
- edits must be an array of exact-match replacements.
- Each edit must contain exactly file_path, old_text, and new_text.
- file_path must refer to one of the explicitly editable files.
- old_text must match exactly once inside the referenced editable file.
- Do not modify context files.
- Keep edits as small and local as possible.
- Preserve indentation, spacing, and blank lines.
- Every changed Python file must remain valid Python.
""".strip()

USER_PROMPT_TEMPLATE = """
Task goal:
{user_prompt}

Primary target file:
{target_file_path}

Editable files:
{editable_block}

Readonly context files:
{context_block}

Previous failure to fix:
{failure_hint}
""".strip()


class CodingPromptBuilder:
    """Render the current file-oriented coding prompt."""

    def build_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_user_prompt(
        self,
        request: CodingTaskCreateRequest,
        bundle: CodingContextBundle,
        failure_hint: Optional[str],
    ) -> str:
        editable_block = "\n\n".join(
            [
                (
                    f"<editable_file path=\"{view.file_path}\">\n"
                    f"{view.content}\n"
                    f"</editable_file>"
                )
                for view in bundle.editable_views.values()
            ]
        )
        if bundle.readonly_views:
            context_block = "\n\n".join(
                [
                    (
                        f"<context_file path=\"{item.file_path}\">\n"
                        f"{item.content}\n"
                        f"</context_file>"
                    )
                    for item in bundle.readonly_views
                ]
            )
        else:
            context_block = "<none />"

        return USER_PROMPT_TEMPLATE.format(
            user_prompt=request.user_prompt,
            target_file_path=request.target_file_path,
            editable_block=editable_block,
            context_block=context_block,
            failure_hint=failure_hint or "None",
        )


__all__ = [
    "CodingPromptBuilder",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
]
