"""
Type Converter — Phase 1

Converts values between DataTypes.
- Conversion losses are silently accepted at runtime (no exception, no log).
- loss / loss_description are for configuration-time GUI warnings only.
- STRING → anything is always marked as lossy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ConversionResult:
    value: Any
    loss: bool = False
    loss_description: str = ""  # Used only at config time, never logged at runtime


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert(value: Any, from_type: str, to_type: str) -> ConversionResult:
    """Convert *value* from *from_type* to *to_type*.

    Always returns a ConversionResult — never raises.
    """
    if from_type == to_type:
        return ConversionResult(value=value)

    fn = _CONVERTERS.get((from_type, to_type))
    if fn is not None:
        return fn(value)

    # Generic fallback via string representation
    return ConversionResult(
        value=str(value),
        loss=True,
        loss_description=(
            f"No direct conversion from {from_type} to {to_type}; "
            "used string representation"
        ),
    )


def can_convert(from_type: str, to_type: str) -> bool:
    """Return True if a direct (non-fallback) conversion exists."""
    return from_type == to_type or (from_type, to_type) in _CONVERTERS


def conversion_has_loss(from_type: str, to_type: str) -> bool:
    """Return True if the conversion is known to be potentially lossy.

    Used by the GUI configurator to warn the user.
    """
    if from_type == to_type:
        return False
    result = convert(_SAMPLE_VALUES.get(from_type, b""), from_type, to_type)
    # Only check the loss flag, ignore sample-specific results
    return result.loss


# ---------------------------------------------------------------------------
# Individual converters
# ---------------------------------------------------------------------------

def _float_to_int(value: Any) -> ConversionResult:
    f = float(value)
    i = int(f)
    loss = f != float(i)
    return ConversionResult(
        value=i,
        loss=loss,
        loss_description="Fractional part truncated" if loss else "",
    )


def _float_to_bool(value: Any) -> ConversionResult:
    f = float(value)
    loss = f not in (0.0, 1.0)
    return ConversionResult(
        value=bool(f),
        loss=loss,
        loss_description=f"Non-binary float {f} coerced to bool" if loss else "",
    )


def _float_to_string(value: Any) -> ConversionResult:
    return ConversionResult(
        value=str(float(value)),
        loss=True,
        loss_description="Converted to string — roundtrip may not preserve precision",
    )


def _int_to_float(value: Any) -> ConversionResult:
    return ConversionResult(value=float(int(value)))


def _int_to_bool(value: Any) -> ConversionResult:
    i = int(value)
    loss = i not in (0, 1)
    return ConversionResult(
        value=bool(i),
        loss=loss,
        loss_description=f"Non-binary integer {i} coerced to bool" if loss else "",
    )


def _int_to_string(value: Any) -> ConversionResult:
    return ConversionResult(
        value=str(int(value)),
        loss=True,
        loss_description="Converted to string",
    )


def _bool_to_int(value: Any) -> ConversionResult:
    return ConversionResult(value=int(bool(value)))


def _bool_to_float(value: Any) -> ConversionResult:
    return ConversionResult(value=float(bool(value)))


def _bool_to_string(value: Any) -> ConversionResult:
    return ConversionResult(
        value=str(bool(value)),
        loss=True,
        loss_description="Converted to string",
    )


def _string_to_float(value: Any) -> ConversionResult:
    try:
        return ConversionResult(
            value=float(str(value)),
            loss=True,
            loss_description="Parsed from string",
        )
    except ValueError:
        return ConversionResult(
            value=0.0,
            loss=True,
            loss_description=f"Cannot parse '{value}' as float — defaulted to 0.0",
        )


def _string_to_int(value: Any) -> ConversionResult:
    try:
        return ConversionResult(
            value=int(str(value)),
            loss=True,
            loss_description="Parsed from string",
        )
    except ValueError:
        return ConversionResult(
            value=0,
            loss=True,
            loss_description=f"Cannot parse '{value}' as int — defaulted to 0",
        )


def _string_to_bool(value: Any) -> ConversionResult:
    s = str(value).lower().strip()
    if s in ("true", "1", "yes", "on"):
        return ConversionResult(value=True, loss=True, loss_description="Parsed from string")
    if s in ("false", "0", "no", "off"):
        return ConversionResult(value=False, loss=True, loss_description="Parsed from string")
    return ConversionResult(
        value=bool(s),
        loss=True,
        loss_description=f"Ambiguous string '{value}' coerced to bool",
    )


# ---------------------------------------------------------------------------
# Conversion matrix
# ---------------------------------------------------------------------------

_Converter = Callable[[Any], ConversionResult]

_CONVERTERS: dict[tuple[str, str], _Converter] = {
    ("FLOAT",   "INTEGER"): _float_to_int,
    ("FLOAT",   "BOOLEAN"): _float_to_bool,
    ("FLOAT",   "STRING"):  _float_to_string,
    ("INTEGER", "FLOAT"):   _int_to_float,
    ("INTEGER", "BOOLEAN"): _int_to_bool,
    ("INTEGER", "STRING"):  _int_to_string,
    ("BOOLEAN", "INTEGER"): _bool_to_int,
    ("BOOLEAN", "FLOAT"):   _bool_to_float,
    ("BOOLEAN", "STRING"):  _bool_to_string,
    ("STRING",  "FLOAT"):   _string_to_float,
    ("STRING",  "INTEGER"): _string_to_int,
    ("STRING",  "BOOLEAN"): _string_to_bool,
}

# Representative sample values used only for loss-detection queries
_SAMPLE_VALUES: dict[str, Any] = {
    "FLOAT":   1.5,
    "INTEGER": 1,
    "BOOLEAN": True,
    "STRING":  "1",
    "UNKNOWN": b"",
}
