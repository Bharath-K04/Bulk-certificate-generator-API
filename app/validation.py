from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.schemas import Recipient


@dataclass(frozen=True)
class RecipientResult:
    index: int
    recipient: Recipient | None  # None when invalid
    errors: list[str] = field(default_factory=list)
    # Best-effort copies of what was submitted, kept so invalid rows are still identifiable.
    raw_name: str | None = None
    raw_email: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.recipient is not None


def validate_recipients(raw_items: list[Any]) -> list[RecipientResult]:
    """Validate each recipient independently and return one result per input row."""
    results: list[RecipientResult] = []
    seen_emails: dict[str, int] = {}

    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            results.append(RecipientResult(index, None, ["recipient must be a JSON object"]))
            continue

        raw_name = _as_text(raw.get("name"), 200)
        raw_email = _as_text(raw.get("email"), 320)

        try:
            recipient = Recipient.model_validate(raw)
        except ValidationError as exc:
            errors = [_format_error(err) for err in exc.errors()]
            results.append(RecipientResult(index, None, errors, raw_name, raw_email))
            continue

        email_key = recipient.email.lower()
        if email_key in seen_emails:
            error = f"email: duplicate of recipient at index {seen_emails[email_key]}"
            results.append(RecipientResult(index, None, [error], raw_name, raw_email))
            continue

        seen_emails[email_key] = index
        results.append(
            RecipientResult(index, recipient, [], recipient.name, str(recipient.email))
        )

    return results


def _format_error(error: dict) -> str:
    location = ".".join(str(part) for part in error.get("loc", ())) or "recipient"
    return f"{location}: {error.get('msg', 'invalid value')}"


def _as_text(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    return str(value)[:max_length]
