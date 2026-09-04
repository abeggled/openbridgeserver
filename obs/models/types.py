"""DataTypeRegistry — Phase 1

Defines the 8 built-in data types and the registry they live in.
New types (e.g. from adapters) are added via DataTypeRegistry.register().
"""

from __future__ import annotations

import datetime
import decimal
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

# ---------------------------------------------------------------------------
# Definition
# ---------------------------------------------------------------------------


@dataclass
class DataTypeDefinition:
    name: str
    python_type: type
    mqtt_serializer: Callable[[Any], str]  # value → JSON string
    mqtt_deserializer: Callable[[str], Any]  # JSON string → value
    description: str = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class DataTypeRegistry:
    """Global registry for DataTypeDefinitions. Thread-safe for reads."""

    _types: ClassVar[dict[str, DataTypeDefinition]] = {}

    @classmethod
    def register(cls, definition: DataTypeDefinition) -> None:
        """Register a DataTypeDefinition. Overwrites if name already exists."""
        cls._types[definition.name] = definition

    @classmethod
    def get(cls, name: str) -> DataTypeDefinition:
        """Return the definition for *name*, falling back to UNKNOWN."""
        return cls._types.get(name, cls._types["UNKNOWN"])

    @classmethod
    def all(cls) -> dict[str, DataTypeDefinition]:
        """Return a snapshot of all registered types."""
        return dict(cls._types)

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._types.keys())

    @classmethod
    def is_registered(cls, name: str) -> bool:
        return name in cls._types


# ---------------------------------------------------------------------------
# Built-in type registrations
# ---------------------------------------------------------------------------


def _register_builtin_types() -> None:
    defs: list[DataTypeDefinition] = [
        # UNKNOWN — raw bytes fallback, must be first (used as fallback in get())
        DataTypeDefinition(
            name="UNKNOWN",
            python_type=bytes,
            mqtt_serializer=lambda v: v.hex() if isinstance(v, bytes) else str(v),
            mqtt_deserializer=lambda s: bytes.fromhex(s) if _is_hex(s) else s.encode(),
            description="Fallback for unknown types, stores raw bytes",
        ),
        # BOOLEAN
        DataTypeDefinition(
            name="BOOLEAN",
            python_type=bool,
            mqtt_serializer=lambda v: json.dumps(bool(v)),
            mqtt_deserializer=lambda s: bool(json.loads(s)),
            description="Boolean value",
        ),
        # INTEGER
        DataTypeDefinition(
            name="INTEGER",
            python_type=int,
            mqtt_serializer=lambda v: json.dumps(int(v)),
            mqtt_deserializer=lambda s: int(json.loads(s)),
            description="Integer value",
        ),
        # FLOAT
        DataTypeDefinition(
            name="FLOAT",
            python_type=float,
            mqtt_serializer=lambda v: json.dumps(float(v)),
            mqtt_deserializer=lambda s: float(json.loads(s)),
            description="Floating point value",
        ),
        # STRING
        DataTypeDefinition(
            name="STRING",
            python_type=str,
            mqtt_serializer=lambda v: json.dumps(str(v)),
            mqtt_deserializer=lambda s: str(json.loads(s)),
            description="String value",
        ),
        # DATE — ISO 8601
        DataTypeDefinition(
            name="DATE",
            python_type=datetime.date,
            mqtt_serializer=lambda v: json.dumps(v.isoformat()),
            mqtt_deserializer=lambda s: datetime.date.fromisoformat(json.loads(s)),
            description="Date value (ISO 8601, e.g. 2025-03-26)",
        ),
        # TIME — ISO 8601
        DataTypeDefinition(
            name="TIME",
            python_type=datetime.time,
            mqtt_serializer=lambda v: json.dumps(v.isoformat()),
            mqtt_deserializer=lambda s: datetime.time.fromisoformat(json.loads(s)),
            description="Time value (ISO 8601, e.g. 10:23:41)",
        ),
        # DATETIME — ISO 8601 with timezone
        DataTypeDefinition(
            name="DATETIME",
            python_type=datetime.datetime,
            mqtt_serializer=lambda v: json.dumps(v.isoformat()),
            mqtt_deserializer=lambda s: datetime.datetime.fromisoformat(json.loads(s)),
            description="Datetime with timezone (ISO 8601, e.g. 2025-03-26T10:23:41.123Z)",
        ),
    ]

    for d in defs:
        DataTypeRegistry.register(d)


def _is_hex(s: str) -> bool:
    return all(c in "0123456789abcdefABCDEF" for c in s) and len(s) % 2 == 0


# Register at import time
_register_builtin_types()


# ---------------------------------------------------------------------------
# Value coercion helpers (shared by API and adapters — issue #1008)
# ---------------------------------------------------------------------------

#: Text literals accepted as boolean ``True`` for an explicitly typed target.
TRUE_LITERALS: frozenset[str] = frozenset({"true", "1", "on", "ein", "yes", "ja"})
#: Text literals accepted as boolean ``False`` for an explicitly typed target.
FALSE_LITERALS: frozenset[str] = frozenset({"false", "0", "off", "aus", "no", "nein"})

#: Literals the type-less heuristic folds into a boolean. Deliberately the pre-#1008
#: set, without ``yes``/``ja``/``no``/``nein``: an UNKNOWN datapoint has no declared
#: type to justify the reinterpretation, so a timer that has always sent the command
#: text ``yes`` keeps sending the string rather than silently becoming ``True`` for
#: its downstream MQTT/protocol consumers after an upgrade.
HEURISTIC_TRUE_LITERALS: frozenset[str] = frozenset({"true", "1", "on", "ein"})
#: Counterpart of :data:`HEURISTIC_TRUE_LITERALS` for boolean ``False``.
HEURISTIC_FALSE_LITERALS: frozenset[str] = frozenset({"false", "0", "off", "aus"})


def coerce_value_for_type(value: Any, data_type: str) -> Any:
    """Coerce an already-typed *value* to the Python type declared for *data_type*.

    Raises ValueError when the value is incompatible so callers can return 422.
    UNKNOWN datapoints accept any value unchanged.
    """
    defn = DataTypeRegistry.get(data_type)
    if defn.name == "UNKNOWN":
        return value

    py_type = defn.python_type

    if isinstance(value, py_type) and not (py_type is int and isinstance(value, bool)):
        return value
    if py_type is int and isinstance(value, bool):
        return int(value)
    if py_type is float and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if py_type is int and isinstance(value, float) and not isinstance(value, bool) and value == int(value):
        return int(value)
    if py_type is bool and isinstance(value, int) and not isinstance(value, bool):
        return bool(value)
    if py_type is datetime.date and isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    if py_type is datetime.time and isinstance(value, str):
        try:
            return datetime.time.fromisoformat(value)
        except ValueError:
            pass
    if py_type is datetime.datetime and isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"Value {value!r} ({type(value).__name__}) is not compatible with data_type '{data_type}'")


def parse_text_value_heuristic(raw: str) -> Any:
    """Best-effort parse of a free-text value without a known target type.

    Order: boolean literals → int → float → the stripped string itself.
    Used for UNKNOWN datapoints, which accept any Python type. Only the narrower
    :data:`HEURISTIC_TRUE_LITERALS` / :data:`HEURISTIC_FALSE_LITERALS` are folded
    into a boolean here — see the note on those sets.
    """
    stripped = raw.strip()
    lowered = stripped.lower()
    if lowered in HEURISTIC_TRUE_LITERALS:
        return True
    if lowered in HEURISTIC_FALSE_LITERALS:
        return False
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    return stripped


def coerce_text_value_for_type(raw: str, data_type: str) -> Any:
    """Parse a free-text value *raw* into the Python type declared for *data_type*.

    Unlike :func:`coerce_value_for_type` the input is always a string (e.g. the
    Zeitschaltuhr switching value), so numeric/boolean/ISO literals have to be
    parsed rather than merely converted.

    * ``UNKNOWN``  → :func:`parse_text_value_heuristic`
    * ``BOOLEAN``  → ``1/true/on/ein/yes/ja`` → ``True``; ``0/false/off/aus/no/nein`` → ``False``
    * ``INTEGER``  → ``int``; integral decimals and boolean literals (→ ``1``/``0``) are
      accepted, integrality being judged exactly (``1.0000000000000001`` is rejected)
      and arbitrary precision preserved (a 400-digit literal keeps every digit)
    * ``FLOAT``    → ``float``; boolean literals map to ``1.0``/``0.0``
    * ``STRING``   → the value verbatim, never interpreted as boolean or number
    * ``DATE`` / ``TIME`` / ``DATETIME`` → ISO 8601 via ``fromisoformat``

    Raises ValueError when *raw* cannot be represented in *data_type*.
    """
    defn = DataTypeRegistry.get(data_type)
    name = defn.name

    if name == "UNKNOWN":
        return parse_text_value_heuristic(raw)
    if name == "STRING":
        return raw

    stripped = raw.strip()
    lowered = stripped.lower()

    if name == "BOOLEAN":
        if lowered in TRUE_LITERALS:
            return True
        if lowered in FALSE_LITERALS:
            return False
        raise ValueError(f"Value {raw!r} is not a valid BOOLEAN literal (expected one of 1/0, true/false, on/off, ein/aus)")

    if name in ("INTEGER", "FLOAT"):
        numeric = _parse_number(stripped, lowered)
        if numeric is None:
            raise ValueError(f"Value {raw!r} is not a valid {name} literal")
        if name == "FLOAT":
            return _to_float(raw, numeric)
        if _has_fractional_part(numeric):
            raise ValueError(f"Value {raw!r} is not a valid INTEGER literal (fractional part would be lost)")
        return int(numeric)

    parser = _ISO_PARSERS.get(name)
    if parser is not None:
        try:
            return parser(stripped)
        except ValueError as exc:
            raise ValueError(f"Value {raw!r} is not a valid ISO 8601 {name} literal") from exc

    # Custom types registered by adapters: fall back to the generic coercion.
    return coerce_value_for_type(stripped, data_type)


_ISO_PARSERS: dict[str, Callable[[str], Any]] = {
    "DATE": datetime.date.fromisoformat,
    "TIME": datetime.time.fromisoformat,
    "DATETIME": datetime.datetime.fromisoformat,
}


def _to_float(raw: str, numeric: decimal.Decimal | float) -> float:
    """Convert a parsed number to ``float`` for a FLOAT target.

    Only this path needs the range guard: ``float(huge_int)`` raises OverflowError
    while ``float(huge_decimal)`` merely returns ``inf``, and both would reach the
    MQTT topic as the invalid JSON literal ``Infinity``. INTEGER deliberately does
    **not** go through here — a Python ``int`` is arbitrary-precision and both the
    WriteRouter and the JSON serializer carry it fine, so a 400-digit INTEGER value
    must survive even though no ``float`` could hold it.
    """
    try:
        as_float = float(numeric)
    except OverflowError:
        raise ValueError(f"Value {raw!r} is out of range for a FLOAT literal") from None
    if not math.isfinite(as_float):
        raise ValueError(f"Value {raw!r} is out of range for a FLOAT literal")
    return as_float


def _has_fractional_part(numeric: decimal.Decimal | float) -> bool:
    """Would converting *numeric* to ``int`` lose anything?

    ``Decimal`` is compared against its own integral value so the check stays exact
    for literals binary ``float`` would have rounded (``1.0000000000000001``); the
    ``float`` case only arises for the exotic spellings ``Decimal`` cannot encode.
    """
    if isinstance(numeric, decimal.Decimal):
        return numeric != numeric.to_integral_value()
    if isinstance(numeric, float):
        return numeric != int(numeric)
    return False


def _parse_number(stripped: str, lowered: str) -> int | decimal.Decimal | float | None:
    """Parse *stripped* as a number, mapping boolean literals to 1/0.

    Three parsers in descending exactness, because each covers spellings the next
    one does not:

    1. ``int()`` — exact and arbitrary-precision, so a 400-digit INTEGER value
       keeps every digit.
    2. ``Decimal()`` — exact for fractional literals, which lets the INTEGER caller
       judge integrality on what the user actually typed: binary ``float`` rounds
       ``1.0000000000000001`` to ``1.0`` and ``9007199254740993.0`` to ``...992``,
       turning a lossy conversion into an apparently exact one.
    3. ``float()`` — the fallback for the exotic spellings ``Decimal`` rejects. Its
       exponent field is bounded, so ``0e99999999999999999999`` raises
       ``InvalidOperation`` there while ``float()`` reads the exactly representable
       ``0.0`` the schedule point has always published.

    Only genuinely non-numeric results are rejected here: ``nan`` and ``inf`` in
    every spelling, which would reach the MQTT value topic as the invalid JSON
    literals ``NaN`` / ``Infinity``. A value that is merely too large for ``float``
    is **not** rejected at this level — that is the FLOAT caller's concern, see
    :func:`_to_float`.
    """
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        parsed = decimal.Decimal(stripped)
    except decimal.InvalidOperation:
        pass
    else:
        return parsed if parsed.is_finite() else None
    try:
        as_float = float(stripped)
    except ValueError:
        pass
    else:
        return as_float if math.isfinite(as_float) else None
    if lowered in TRUE_LITERALS:
        return 1
    if lowered in FALSE_LITERALS:
        return 0
    return None
