"""KNX group addresses — the one internal notation (#1296).

ETS knows three notations for the same 16-bit group address: three-level
(``1/0/234``), two-level (``1/234``) and free (``2282``). OBS stores, compares
and keys group addresses only in the internal three-level notation; every
entrance normalizes with :func:`normalize_ga`, and display goes through
:func:`format_ga` in the project's style. The contract is documented in
``docs/architecture/knx-group-addresses.md``.

Pure Python on purpose: the result must not depend on xknx's process-wide
``GroupAddress.address_format``, and the import path uses this module without
pulling in the adapter.
"""

from __future__ import annotations

from typing import Any

THREE_LEVEL = "ThreeLevel"
TWO_LEVEL = "TwoLevel"
FREE = "Free"
# Same vocabulary as xknxproject's ``info.group_address_style``.
GROUP_ADDRESS_STYLES = (THREE_LEVEL, TWO_LEVEL, FREE)
DEFAULT_GROUP_ADDRESS_STYLE = THREE_LEVEL

# Upper bound per part, by number of parts in the notation.
_PART_LIMITS = {
    3: (31, 7, 255),
    2: (31, 2047),
    1: (65535,),
}


class InvalidGroupAddress(ValueError):
    """Raised for text that is no group address in any of the three notations."""


def _to_raw(value: Any) -> int:
    if not isinstance(value, str):
        raise InvalidGroupAddress(f"Ungültige Gruppenadresse: {value!r} (Text erwartet)")
    parts = value.strip().split("/")
    limits = _PART_LIMITS.get(len(parts))
    if limits is None or not all(part.isdigit() and part.isascii() for part in parts):
        raise InvalidGroupAddress(f"Ungültige Gruppenadresse: {value!r}")
    numbers = [int(part) for part in parts]
    if any(number > limit for number, limit in zip(numbers, limits, strict=True)):
        raise InvalidGroupAddress(f"Ungültige Gruppenadresse: {value!r} (Wertebereich überschritten)")
    if len(numbers) == 3:
        return (numbers[0] << 11) | (numbers[1] << 8) | numbers[2]
    if len(numbers) == 2:
        return (numbers[0] << 11) | numbers[1]
    return numbers[0]


def normalize_ga(value: Any) -> str:
    """Return the internal three-level notation of a group address in any notation.

    Raises :class:`InvalidGroupAddress` (a ``ValueError``) for anything else.
    """
    raw = _to_raw(value)
    return f"{raw >> 11}/{(raw >> 8) & 0x7}/{raw & 0xFF}"


def try_normalize_ga(value: Any) -> str | None:
    """Like :func:`normalize_ga`, but ``None`` for values that are no group address.

    For tolerant readers of already-stored data, where one broken entry must not
    abort the whole operation.
    """
    try:
        return normalize_ga(value)
    except InvalidGroupAddress:
        return None


def format_ga(address: str, style: str) -> str:
    """Render a group address in a project's style (``ThreeLevel``/``TwoLevel``/``Free``)."""
    if style not in GROUP_ADDRESS_STYLES:
        raise ValueError(f"Unbekannter Gruppenadressstil: {style!r}")
    raw = _to_raw(address)
    if style == THREE_LEVEL:
        return normalize_ga(address)
    if style == TWO_LEVEL:
        return f"{raw >> 11}/{raw & 0x7FF}"
    return str(raw)
