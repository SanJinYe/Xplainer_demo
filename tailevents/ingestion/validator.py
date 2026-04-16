"""Validation helpers for raw ingestion events."""

from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from tailevents.models.event import RawEvent


class ValidationIssue(BaseModel):
    """Structured validation issue."""

    field: str
    code: str
    message: str


class IngestionValidationError(ValueError):
    """Raised when a raw event fails ingestion validation."""

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("RawEvent validation failed")


class RawEventValidator:
    """Validate raw ingestion payloads before persistence."""

    def validate(self, raw_event: RawEvent | dict[str, Any]) -> list[ValidationIssue]:
        payload = (
            raw_event.model_dump(mode="python")
            if isinstance(raw_event, RawEvent)
            else dict(raw_event)
        )
        issues: list[ValidationIssue] = []

        if not self._as_text(payload.get("file_path")):
            issues.append(
                ValidationIssue(
                    field="file_path",
                    code="empty",
                    message="file_path must be a non-empty string",
                )
            )

        if not self._as_text(payload.get("intent")):
            issues.append(
                ValidationIssue(
                    field="intent",
                    code="empty",
                    message="intent must be a non-empty string",
                )
            )

        if not isinstance(raw_event, RawEvent):
            issues.extend(self._collect_model_issues(payload))

        return self._dedupe(issues)

    def normalize(self, raw_event: RawEvent | dict[str, Any]) -> RawEvent:
        """Return a validated RawEvent or raise a structured validation error."""

        issues = self.validate(raw_event)
        if issues:
            raise IngestionValidationError(issues)
        if isinstance(raw_event, RawEvent):
            return raw_event
        return RawEvent.model_validate(raw_event)

    def _collect_model_issues(self, payload: dict[str, Any]) -> list[ValidationIssue]:
        try:
            RawEvent.model_validate(payload)
        except ValidationError as exc:
            return [self._issue_from_error(error) for error in exc.errors()]
        return []

    def _issue_from_error(self, error: dict[str, Any]) -> ValidationIssue:
        location = error.get("loc") or ["payload"]
        field = ".".join(str(part) for part in location)
        return ValidationIssue(
            field=field,
            code=str(error.get("type", "invalid")),
            message=str(error.get("msg", "invalid value")),
        )

    def _dedupe(self, issues: list[ValidationIssue]) -> list[ValidationIssue]:
        unique: dict[tuple[str, str, str], ValidationIssue] = {}
        for issue in issues:
            unique[(issue.field, issue.code, issue.message)] = issue
        return list(unique.values())

    def _as_text(self, value: Optional[Any]) -> str:
        if value is None:
            return ""
        return str(value).strip()


__all__ = [
    "IngestionValidationError",
    "RawEventValidator",
    "ValidationIssue",
]
