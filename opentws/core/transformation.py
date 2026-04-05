"""
Shared value-transformation helpers.

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
from typing import Any

logger = logging.getLogger(__name__)


def apply_source_type(
    raw: str,
    auto_value: Any,
    source_data_type: str | None,
    json_key: str | None,
    xml_path: str | None,
    binding_id: Any = None,
) -> Any:
    """
    Coerce / extract *raw* (a decoded string payload) to a Python value.

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
        obj = auto_value if isinstance(auto_value, dict) else json.loads(raw)
        if json_key:
            pub_value = obj.get(json_key, pub_value) if isinstance(obj, dict) else pub_value
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
                        xml_path, binding_id,
                    )
            else:
                pub_value = (root.text or "").strip()
        except Exception as xml_exc:
            logger.warning("Transformation XML: parse error for binding %s: %s", binding_id, xml_exc)

    elif source_data_type == "int":
        try:
            pub_value = int(float(pub_value)) if isinstance(pub_value, str) else int(pub_value)
        except (ValueError, TypeError):
            logger.warning(
                "Transformation: cannot coerce %r to int for binding %s", pub_value, binding_id
            )

    elif source_data_type == "float":
        try:
            pub_value = float(pub_value)
        except (ValueError, TypeError):
            logger.warning(
                "Transformation: cannot coerce %r to float for binding %s", pub_value, binding_id
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


def apply_value_map(value: Any, value_map: dict[str, str] | None) -> Any:
    """
    Apply a string-keyed substitution map.

    The incoming *value* is converted to str for the lookup; if no entry
    is found the original *value* is returned unchanged.

    Parameters
    ----------
    value:     The current value (any type).
    value_map: Dict mapping str(value) → replacement str, or None.

    Returns
    -------
    Mapped value (str) or original *value* if no match / no map.
    """
    if not value_map:
        return value
    # Booleans: str(True) → "True" but JSON keys are "true"/"false" — normalise to lowercase.
    key = str(value).lower() if isinstance(value, bool) else str(value)
    return value_map.get(key, value)
