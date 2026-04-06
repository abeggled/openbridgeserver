"""
Modbus Shared Logic — Phase 3

Gemeinsame Datenformate und Hilfsklassen für TCP- und RTU-Adapter.
"""
from __future__ import annotations

import struct
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Binding Config (shared TCP + RTU)
# ---------------------------------------------------------------------------

class ModbusBindingConfig(BaseModel):
    unit_id: int = 1
    register_type: Literal["holding", "input", "coil", "discrete_input"] = "holding"
    address: int = 0
    count: int = 1                           # number of registers
    data_format: Literal[
        "uint16", "int16",
        "uint32", "int32",
        "float32",
        "uint64", "int64",
    ] = "uint16"
    scale_factor: float = 1.0               # raw_value × scale_factor = engineering value
    byte_order: Literal["big", "little"] = "big"
    word_order: Literal["big", "little"] = "big"
    poll_interval: float = 1.0              # seconds (SOURCE / BOTH bindings)


# ---------------------------------------------------------------------------
# Register decode / encode
# ---------------------------------------------------------------------------

def decode_registers(
    registers: list[int],
    data_format: str,
    byte_order: str = "big",
    word_order: str = "big",
    scale_factor: float = 1.0,
) -> Any:
    """Convert a list of 16-bit Modbus register values to a Python value."""
    bo = ">" if byte_order == "big" else "<"
    wo = ">" if word_order == "big" else "<"

    if data_format == "uint16":
        raw = registers[0]
        return int(raw * scale_factor) if scale_factor != 1.0 else raw

    elif data_format == "int16":
        raw = struct.unpack(bo + "h", struct.pack(bo + "H", registers[0]))[0]
        return raw * scale_factor if scale_factor != 1.0 else raw

    elif data_format in ("uint32", "int32", "float32"):
        if len(registers) < 2:
            return 0
        r = registers[:2] if wo == ">" else registers[:2][::-1]
        word_bytes = struct.pack(">HH", r[0], r[1])
        if data_format == "uint32":
            raw = struct.unpack(">I", word_bytes)[0]
        elif data_format == "int32":
            raw = struct.unpack(">i", word_bytes)[0]
        else:  # float32
            raw = struct.unpack(">f", word_bytes)[0]
        return round(raw * scale_factor, 6) if scale_factor != 1.0 else raw

    elif data_format in ("uint64", "int64"):
        if len(registers) < 4:
            return 0
        word_bytes = struct.pack(">HHHH", *registers[:4])
        fmt = ">Q" if data_format == "uint64" else ">q"
        raw = struct.unpack(fmt, word_bytes)[0]
        return raw * scale_factor if scale_factor != 1.0 else raw

    return registers[0]  # fallback


def encode_value(
    value: Any,
    data_format: str,
    byte_order: str = "big",
    word_order: str = "big",
    scale_factor: float = 1.0,
) -> list[int]:
    """Convert a Python value to a list of 16-bit Modbus register values."""
    bo = ">" if byte_order == "big" else "<"
    scaled = float(value) / scale_factor if scale_factor != 1.0 else float(value)

    if data_format == "uint16":
        return [int(scaled) & 0xFFFF]

    elif data_format == "int16":
        return list(struct.unpack(">H", struct.pack(bo + "h", int(scaled))))

    elif data_format in ("uint32", "int32", "float32"):
        if data_format == "uint32":
            word_bytes = struct.pack(">I", int(scaled) & 0xFFFFFFFF)
        elif data_format == "int32":
            word_bytes = struct.pack(">i", int(scaled))
        else:
            word_bytes = struct.pack(">f", scaled)
        r0, r1 = struct.unpack(">HH", word_bytes)
        return [r0, r1] if word_order == "big" else [r1, r0]

    elif data_format in ("uint64", "int64"):
        fmt = ">Q" if data_format == "uint64" else ">q"
        word_bytes = struct.pack(fmt, int(scaled))
        return list(struct.unpack(">HHHH", word_bytes))

    return [int(scaled) & 0xFFFF]


_FORMAT_REGISTER_COUNT: dict[str, int] = {
    "uint16": 1, "int16": 1,
    "uint32": 2, "int32": 2, "float32": 2,
    "uint64": 4, "int64": 4,
}


def register_count(data_format: str) -> int:
    return _FORMAT_REGISTER_COUNT.get(data_format, 1)
