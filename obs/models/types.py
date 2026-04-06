"""
DataTypeRegistry — Phase 1

Defines the 8 built-in data types and the registry they live in.
New types (e.g. from adapters) are added via DataTypeRegistry.register().
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Definition
# ---------------------------------------------------------------------------

@dataclass
class DataTypeDefinition:
    name: str
    python_type: type
    mqtt_serializer: Callable[[Any], str]    # value → JSON string
    mqtt_deserializer: Callable[[str], Any]  # JSON string → value
    description: str = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class DataTypeRegistry:
    """Global registry for DataTypeDefinitions. Thread-safe for reads."""

    _types: dict[str, DataTypeDefinition] = {}

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
