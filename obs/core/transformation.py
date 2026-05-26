"""Shared value-transformation helpers.

Extracted from the MQTT adapter so that the same coercion + mapping
logic can be reused by other adapters, the logic engine, etc.

Public API
----------
apply_source_type(raw, auto_value, source_data_type, json_key, xml_path, binding_id)
    Parse / coerce an incoming raw string payload to a Python value.

apply_value_map(value, value_map)
    Apply a string-keyed substitution map to an incoming or outgoing value.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _extract_nested(obj: Any, path: str) -> Any:
    """Extract a value from a nested dict/list using dot-notation path.

    Supports "key", "parent.child", "items.0.name", "a[0].b".
    Raises KeyError / IndexError / TypeError on missing or invalid paths.
    """
    path = re.sub(r"\[(\d+)\]", r".\1", path)
    parts = [p for p in path.split(".") if p]
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, (list, tuple)):
            current = current[int(part)]
        else:
            raise TypeError(f"Cannot traverse {type(current).__name__} with key '{part}'")
    return current


def apply_source_type(
    raw: str,
    auto_value: Any,
    source_data_type: str | None,
    json_key: str | None,
    xml_path: str | None,
    binding_id: Any = None,
) -> Any:
    """Coerce / extract *raw* (a decoded string payload) to a Python value.

    Parameters
    ----------
    raw:              The raw decoded string payload.
    auto_value:       Pre-parsed value via json.loads(raw) or raw itself.
    source_data_type: "string" | "int" | "float" | "bool" | "json" | "xml" | None
                      None / "auto" → use auto_value as-is.
    json_key:         Key to extract from JSON object (source_data_type == "json").
    xml_path:         ElementTree XPath (source_data_type == "xml").
    binding_id:       Used only in warning messages.

    Returns
    -------
    Coerced Python value.

    """
    pub_value = auto_value

    if source_data_type == "json":
        obj = auto_value if isinstance(auto_value, (dict, list)) else json.loads(raw)
        if json_key:
            try:
                pub_value = _extract_nested(obj, json_key)
            except (KeyError, IndexError, TypeError, ValueError):
                logger.warning(
                    "Transformation JSON: path %r not found in payload for binding %s",
                    json_key,
                    binding_id,
                )
        else:
            pub_value = obj

    elif source_data_type == "xml":
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(raw)
            if xml_path:
                el = root.find(xml_path)
                if el is not None:
                    text = (el.text or "").strip()
                    try:
                        pub_value = int(text)
                    except ValueError:
                        try:
                            pub_value = float(text)
                        except ValueError:
                            pub_value = text
                else:
                    logger.warning(
                        "Transformation XML: path %r not found in payload for binding %s",
                        xml_path,
                        binding_id,
                    )
            else:
                pub_value = (root.text or "").strip()
        except Exception as xml_exc:
            logger.warning(
                "Transformation XML: parse error for binding %s: %s",
                binding_id,
                xml_exc,
            )

    elif source_data_type == "int":
        try:
            pub_value = int(float(pub_value)) if isinstance(pub_value, str) else int(pub_value)
        except (ValueError, TypeError):
            logger.warning(
                "Transformation: cannot coerce %r to int for binding %s",
                pub_value,
                binding_id,
            )

    elif source_data_type == "float":
        try:
            pub_value = float(pub_value)
        except (ValueError, TypeError):
            logger.warning(
                "Transformation: cannot coerce %r to float for binding %s",
                pub_value,
                binding_id,
            )

    elif source_data_type == "bool":
        if isinstance(pub_value, bool):
            pass  # already bool
        elif isinstance(pub_value, str):
            pub_value = pub_value.lower() in ("true", "1", "on", "yes")
        else:
            pub_value = bool(pub_value)

    elif source_data_type == "string":
        pub_value = str(pub_value)

    # else None / "auto": use auto_value as-is

    return pub_value


def apply_value_map(value: Any, value_map: dict[str, Any] | None) -> Any:
    """Apply a string-keyed substitution map.

    The incoming *value* is converted to str for the lookup; if no entry
    is found the original *value* is returned unchanged.

    Key normalisation rules (applied in order):
    - bool  → lowercase "true"/"false"  (JSON convention)
    - float with no fractional part (e.g. 5.0) → int string "5"
    - all other types → str(value)

    This allows N-entry maps like {"0": "Aus", "1": "Init", …, "10": "Standby"}
    to work even when values arrive as floats from Modbus or similar adapters.

    Parameters
    ----------
    value:     The current value (any type).
    value_map: Dict mapping str(value) → replacement, or None.

    Returns
    -------
    Mapped value or original *value* if no match / no map.

    """
    if not value_map:
        return value
    if isinstance(value, bool):
        key = str(value).lower()  # "true" or "false"
        # Fall back to numeric "1"/"0" when the bool key is not in the map.
        # This allows numeric maps like {"0": "1", "1": "0"} to work with
        # boolean inputs (e.g. KNX DPT1.x decodes to Python bool).
        if key not in value_map:
            key = "1" if value else "0"
    elif isinstance(value, float) and value.is_integer():
        key = str(int(value))
    else:
        key = str(value)
    return value_map.get(key, value)
