"""
KNX DPT Registry — Phase 3

Implementiert die gängigsten KNX Data Point Types direkt in Python (keine xknx-Abhängigkeit
für die Registry selbst). Der KNX-Adapter nutzt xknx für die Protokollverbindung, aber
Codierung/Dekodierung läuft über diesen Registry.

Unbekannte DPTs → UNKNOWN (kein Crash).

Implementierte DPTs:
  DPT1.x   — 1-Bit (BOOLEAN)
  DPT5.x   — 8-Bit unsigned (INTEGER / FLOAT)
  DPT6.x   — 8-Bit signed (INTEGER)
  DPT7.x   — 16-Bit unsigned (INTEGER)
  DPT8.x   — 16-Bit signed (INTEGER)
  DPT9.x   — 16-Bit float EIS5 (FLOAT) ← Temperatur, Feuchte, etc.
  DPT12.x  — 32-Bit unsigned (INTEGER)
  DPT13.x  — 32-Bit signed (INTEGER)
  DPT14.x  — 32-Bit IEEE float (FLOAT) ← Leistung, Spannung, etc.
  DPT16.x  — 14-Byte String (STRING)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Callable


# ---------------------------------------------------------------------------
# DPTDefinition
# ---------------------------------------------------------------------------

@dataclass
class DPTDefinition:
    dpt_id: str           # z.B. "DPT9.001"
    name: str             # "Temperature"
    data_type: str        # "FLOAT" | "INTEGER" | "BOOLEAN" | "STRING"
    unit: str             # "°C"
    size_bytes: int       # expected payload size
    encoder: Callable[[Any], bytes]     # Python value → KNX raw bytes
    decoder: Callable[[bytes], Any]     # KNX raw bytes → Python value


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class DPTRegistry:
    _dpts: dict[str, DPTDefinition] = {}

    @classmethod
    def register(cls, d: DPTDefinition) -> None:
        cls._dpts[d.dpt_id] = d

    @classmethod
    def get(cls, dpt_id: str) -> DPTDefinition:
        """Return DPT definition or UNKNOWN fallback (never raises)."""
        return cls._dpts.get(dpt_id, _UNKNOWN_DPT)

    @classmethod
    def all(cls) -> dict[str, DPTDefinition]:
        return dict(cls._dpts)

    @classmethod
    def by_data_type(cls, data_type: str) -> list[DPTDefinition]:
        return [d for d in cls._dpts.values() if d.data_type == data_type]


# ---------------------------------------------------------------------------
# Codec helpers
# ---------------------------------------------------------------------------

# --- DPT 1.x — 1-bit ---------------------------------------------------------
def _dpt1_decode(b: bytes) -> bool:
    return bool(b[0] & 0x01)

def _dpt1_encode(v: Any) -> bytes:
    return bytes([0x01 if v else 0x00])


# --- DPT 5.x — 8-bit unsigned ------------------------------------------------
def _dpt5_decode_percent(b: bytes) -> float:
    return round(b[0] * 100.0 / 255.0, 1)

def _dpt5_encode_percent(v: Any) -> bytes:
    return bytes([max(0, min(255, round(float(v) * 255.0 / 100.0)))])

def _dpt5_decode_raw(b: bytes) -> int:
    return b[0]

def _dpt5_encode_raw(v: Any) -> bytes:
    return bytes([max(0, min(255, int(v)))])


# --- DPT 6.x — 8-bit signed --------------------------------------------------
def _dpt6_decode(b: bytes) -> int:
    return struct.unpack(">b", b[:1])[0]

def _dpt6_encode(v: Any) -> bytes:
    return struct.pack(">b", max(-128, min(127, int(v))))


# --- DPT 7.x — 16-bit unsigned -----------------------------------------------
def _dpt7_decode(b: bytes) -> int:
    return struct.unpack(">H", b[:2])[0]

def _dpt7_encode(v: Any) -> bytes:
    return struct.pack(">H", max(0, min(65535, int(v))))


# --- DPT 8.x — 16-bit signed -------------------------------------------------
def _dpt8_decode(b: bytes) -> int:
    return struct.unpack(">h", b[:2])[0]

def _dpt8_encode(v: Any) -> bytes:
    return struct.pack(">h", max(-32768, min(32767, int(v))))


# --- DPT 9.x — 16-bit KNX float (EIS5) --------------------------------------
# Format: SEEEEMMM MMMMMMMM
# value = 0.01 × M × 2^E    (M is 11-bit two's complement)

def _dpt9_decode(b: bytes) -> float:
    word = (b[0] << 8) | b[1]
    sign  = (word >> 15) & 0x01
    exp   = (word >> 11) & 0x0F
    mant  =  word        & 0x07FF
    if sign:
        mant -= 2048
    return round(0.01 * mant * (2 ** exp), 4)

def _dpt9_encode(v: Any) -> bytes:
    fv = float(v)
    sign = 1 if fv < 0 else 0
    mant = round(fv / 0.01)
    exp = 0
    while mant > 2047 or mant < -2048:
        mant = mant // 2
        exp += 1
        if exp > 15:
            # Clamp to max representable value
            mant = 2047 if fv > 0 else -2048
            exp = 15
            break
    if mant < 0:
        mant &= 0x07FF
    word = (sign << 15) | (exp << 11) | (mant & 0x07FF)
    return bytes([word >> 8, word & 0xFF])


# --- DPT 12.x — 32-bit unsigned ----------------------------------------------
def _dpt12_decode(b: bytes) -> int:
    return struct.unpack(">I", b[:4])[0]

def _dpt12_encode(v: Any) -> bytes:
    return struct.pack(">I", max(0, min(0xFFFFFFFF, int(v))))


# --- DPT 13.x — 32-bit signed ------------------------------------------------
def _dpt13_decode(b: bytes) -> int:
    return struct.unpack(">i", b[:4])[0]

def _dpt13_encode(v: Any) -> bytes:
    return struct.pack(">i", max(-0x80000000, min(0x7FFFFFFF, int(v))))


# --- DPT 14.x — 32-bit IEEE 754 float ----------------------------------------
def _dpt14_decode(b: bytes) -> float:
    return round(struct.unpack(">f", b[:4])[0], 6)

def _dpt14_encode(v: Any) -> bytes:
    return struct.pack(">f", float(v))


# --- DPT 16.x — 14-byte ASCII string ----------------------------------------
def _dpt16_decode(b: bytes) -> str:
    return b[:14].rstrip(b"\x00").decode("ascii", errors="replace")

def _dpt16_encode(v: Any) -> bytes:
    s = str(v)[:14].encode("ascii", errors="replace")
    return s.ljust(14, b"\x00")


# ---------------------------------------------------------------------------
# UNKNOWN fallback
# ---------------------------------------------------------------------------

_UNKNOWN_DPT = DPTDefinition(
    dpt_id="UNKNOWN",
    name="Unknown",
    data_type="UNKNOWN",
    unit="",
    size_bytes=0,
    encoder=lambda v: v if isinstance(v, bytes) else str(v).encode(),
    decoder=lambda b: b,
)


# ---------------------------------------------------------------------------
# Built-in DPT registrations
# ---------------------------------------------------------------------------

def _register_builtin_dpts() -> None:
    defs = [
        # DPT 1 — 1-bit
        DPTDefinition("DPT1.001", "Switch",            "BOOLEAN", "",    1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.002", "Boolean",           "BOOLEAN", "",    1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.003", "Enable",            "BOOLEAN", "",    1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.008", "Up/Down",           "BOOLEAN", "",    1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.009", "Open/Close",        "BOOLEAN", "",    1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.010", "Start/Stop",        "BOOLEAN", "",    1, _dpt1_encode, _dpt1_decode),

        # DPT 5 — 8-bit unsigned
        DPTDefinition("DPT5.001", "Percentage (0-100%)",  "FLOAT",   "%",   1, _dpt5_encode_percent, _dpt5_decode_percent),
        DPTDefinition("DPT5.010", "Counter Pulses",       "INTEGER", "",    1, _dpt5_encode_raw,     _dpt5_decode_raw),

        # DPT 6 — 8-bit signed
        DPTDefinition("DPT6.010", "Counter Pulses (signed)", "INTEGER", "", 1, _dpt6_encode, _dpt6_decode),

        # DPT 7 — 16-bit unsigned
        DPTDefinition("DPT7.001", "Counter Pulses (2 bytes)", "INTEGER", "",      2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.012", "Time Period (ms)",         "INTEGER", "ms",    2, _dpt7_encode, _dpt7_decode),

        # DPT 8 — 16-bit signed
        DPTDefinition("DPT8.001", "Counter Pulses (signed, 2 bytes)", "INTEGER", "", 2, _dpt8_encode, _dpt8_decode),

        # DPT 9 — 16-bit float (EIS5) — Gebäudeautomation
        DPTDefinition("DPT9.001", "Temperature",          "FLOAT", "°C",    2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.002", "Temperature Difference","FLOAT", "K",    2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.004", "Illuminance",          "FLOAT", "lx",    2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.005", "Wind Speed",           "FLOAT", "m/s",   2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.006", "Air Pressure",         "FLOAT", "Pa",    2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.007", "Humidity",             "FLOAT", "%",     2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.008", "Air Quality (CO2)",    "FLOAT", "ppm",   2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.010", "Time (seconds)",       "FLOAT", "s",     2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.020", "Voltage",              "FLOAT", "mV",    2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.021", "Current",              "FLOAT", "mA",    2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.024", "Power Density",        "FLOAT", "W/m²",  2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.025", "Kelvin/Percent",       "FLOAT", "K/%",   2, _dpt9_encode, _dpt9_decode),

        # DPT 12 — 32-bit unsigned
        DPTDefinition("DPT12.001", "Counter (32-bit)",    "INTEGER", "",     4, _dpt12_encode, _dpt12_decode),

        # DPT 13 — 32-bit signed
        DPTDefinition("DPT13.001", "Counter (32-bit signed)", "INTEGER", "", 4, _dpt13_encode, _dpt13_decode),
        DPTDefinition("DPT13.010", "Active Energy (Wh)", "INTEGER", "Wh",   4, _dpt13_encode, _dpt13_decode),

        # DPT 14 — 32-bit IEEE float
        DPTDefinition("DPT14.019", "Electric Current",    "FLOAT", "A",     4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.031", "Frequency",           "FLOAT", "Hz",    4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.033", "Impedance",           "FLOAT", "Ω",     4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.054", "Power",               "FLOAT", "W",     4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.055", "Power Factor",        "FLOAT", "",      4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.056", "Active Power",        "FLOAT", "W",     4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.057", "Reactive Power",      "FLOAT", "var",   4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.067", "Voltage",             "FLOAT", "V",     4, _dpt14_encode, _dpt14_decode),

        # DPT 16 — 14-byte string
        DPTDefinition("DPT16.000", "ASCII String",        "STRING", "",     14, _dpt16_encode, _dpt16_decode),
        DPTDefinition("DPT16.001", "ISO 8859-1 String",   "STRING", "",     14, _dpt16_encode, _dpt16_decode),
    ]
    for d in defs:
        DPTRegistry.register(d)


_register_builtin_dpts()
