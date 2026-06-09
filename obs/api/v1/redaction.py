"""Helpers for redacting sensitive fields in API responses."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "[redacted]"


def redact_sensitive_fields(
    data: Mapping[str, Any],
    sensitive_fields: Iterable[str],
    replacement: str = REDACTED,
) -> dict[str, Any]:
    """Return a copy with non-empty sensitive fields replaced."""
    redacted = dict(data)
    for field in sensitive_fields:
        value = redacted.get(field)
        if isinstance(value, str) and value:
            redacted[field] = replacement
        elif value is not None and value != "":
            redacted[field] = replacement
    return redacted
