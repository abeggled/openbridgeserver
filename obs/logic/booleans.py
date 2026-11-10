"""The boolean rule every logic block applies to its input values.

Kept in its own dependency-free module so the executor and engine modules it
calls (``hems_surplus``) share one definition instead of copies that can drift.
"""

from __future__ import annotations

from typing import Any

FALSE_WORDS = frozenset({"0", "false", "no", "off", ""})


def to_bool(value: Any) -> bool:
    """Coerce any value to bool. ``None`` and the strings in FALSE_WORDS are False."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in FALSE_WORDS
    return bool(value)
