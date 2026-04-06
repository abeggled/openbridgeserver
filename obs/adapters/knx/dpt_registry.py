"""
KNX DPT Registry — Phase 3

Implementiert die gängigsten KNX Data Point Types direkt in Python (keine xknx-Abhängigkeit
für die Registry selbst). Der KNX-Adapter nutzt xknx für die Protokollverbindung, aber
Codierung/Dekodierung läuft über diesen Registry.

Unbekannte DPTs → UNKNOWN (kein Crash).

Implementierte DPTs:
  DPT1.x   — 1-Bit (BOOLEAN) — vollständig (1.001–1.024)
  DPT3.x   — 4-Bit relative control (INTEGER) — 3.007/3.008
  DPT5.x   — 8-Bit unsigned (INTEGER / FLOAT) — vollständig
  DPT6.x   — 8-Bit signed (INTEGER) — 6.001/6.010
  DPT7.x   — 16-Bit unsigned (INTEGER) — vollständig inkl. 7.600
  DPT8.x   — 16-Bit signed (INTEGER) — vollständig
  DPT9.x   — 16-Bit float EIS5 (FLOAT) — vollständig (9.001–9.030)
  DPT10.x  — Time of Day (TIME)
  DPT11.x  — Date (DATE)
  DPT12.x  — 32-Bit unsigned (INTEGER)
  DPT13.x  — 32-Bit signed (INTEGER)
  DPT14.x  — 32-Bit IEEE float (FLOAT) ← Leistung, Spannung, etc.
  DPT16.x  — 14-Byte String (STRING)
  DPT18.x  — Scene Control (INTEGER)
  DPT19.x  — Date and Time (STRING ISO)
  DPT20.x  — 1-Byte Enum/Mode (INTEGER) — vollständig (20.001–20.604)
  DPT29.x  — 64-Bit signed (INTEGER) ← Smart Metering Energie
  DPT219.x — AlarmInfo (INTEGER)
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

def _dpt5_decode_angle(b: bytes) -> float:
    """DPT5.003: 0…255 → 0°…360°"""
    return round(b[0] * 360.0 / 255.0, 1)

def _dpt5_encode_angle(v: Any) -> bytes:
    """DPT5.003: 0°…360° → 0…255"""
    return bytes([max(0, min(255, round(float(v) * 255.0 / 360.0)))])


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


# --- DPT 3.x — 4-bit relative control ----------------------------------------
# Lower nibble: bit3=direction (0=decrease/1=increase), bits2-0=speed (0=stop,1-7)
def _dpt3_decode(b: bytes) -> int:
    return b[0] & 0x0F

def _dpt3_encode(v: Any) -> bytes:
    return bytes([int(v) & 0x0F])


# --- DPT 10.x — Time of Day (3 bytes) ----------------------------------------
# Byte0: DoW(7..5)|Hour(4..0)  Byte1: Minutes(5..0)  Byte2: Seconds(5..0)
# DoW: 1=Mon…7=Sun, 0=any day
# Rückgabe als ISO-String "HH:MM:SS" (JSON-serialisierbar)
def _dpt10_decode(b: bytes) -> str:
    import datetime
    try:
        hour   = b[0] & 0x1F
        minute = b[1] & 0x3F
        second = b[2] & 0x3F
        return datetime.time(hour, minute, second).isoformat()
    except Exception:
        return "00:00:00"

def _dpt10_encode(v: Any) -> bytes:
    import datetime
    try:
        if isinstance(v, datetime.time):
            t = v
        elif isinstance(v, str):
            t = datetime.time.fromisoformat(v)
        elif isinstance(v, (int, float)):
            total = int(v)
            t = datetime.time(total // 3600 % 24, total // 60 % 60, total % 60)
        else:
            t = datetime.datetime.now().time()
        return bytes([t.hour & 0x1F, t.minute & 0x3F, t.second & 0x3F])
    except Exception:
        return bytes(3)


# --- DPT 11.x — Date (3 bytes) -----------------------------------------------
# Byte0: Day(4..0)  Byte1: Month(3..0)  Byte2: Year(6..0)
# Jahr 0..89 → 2000+Y,  Jahr 90..99 → 1900+Y  (KNX-Spec)
# Rückgabe als ISO-String "YYYY-MM-DD" (JSON-serialisierbar)
def _dpt11_decode(b: bytes) -> str:
    import datetime
    try:
        day   = b[0] & 0x1F
        month = b[1] & 0x0F
        yr    = b[2] & 0x7F
        year  = 2000 + yr if yr < 90 else 1900 + yr
        return datetime.date(year, month, day).isoformat()
    except Exception:
        return "2000-01-01"

def _dpt11_encode(v: Any) -> bytes:
    import datetime
    try:
        if isinstance(v, datetime.date):
            d = v
        elif isinstance(v, str):
            d = datetime.date.fromisoformat(v)
        elif isinstance(v, (int, float)):
            d = datetime.date.fromtimestamp(float(v))
        else:
            d = datetime.date.today()
        yr = d.year % 100          # 2025 → 25, 1990 → 90
        return bytes([d.day & 0x1F, d.month & 0x0F, yr & 0x7F])
    except Exception:
        return bytes(3)


# --- DPT 20.x — Generic 1-Byte Enum (für nicht-20.102 Subtypen) --------------
def _dpt20_decode(b: bytes) -> int:
    return b[0] & 0xFF

def _dpt20_encode(v: Any) -> bytes:
    return bytes([max(0, min(255, int(v)))])


# --- DPT 29.x — 64-bit signed (V64) -----------------------------------------
def _dpt29_decode(b: bytes) -> int:
    return struct.unpack(">q", b[:8])[0]

def _dpt29_encode(v: Any) -> bytes:
    return struct.pack(">q", max(-9223372036854775808, min(9223372036854775807, int(v))))


# --- DPT 2.x — 2-bit controlled value ----------------------------------------
# Bit 1: Control (priority/override), Bit 0: Value
# Returns integer 0-3; caller interprets control + value semantics
def _dpt2_decode(b: bytes) -> int:
    return b[0] & 0x03

def _dpt2_encode(v: Any) -> bytes:
    return bytes([max(0, min(3, int(v))) & 0x03])


# --- DPT 4.x — 1-byte character -----------------------------------------------
def _dpt4_001_decode(b: bytes) -> str:
    return b[:1].decode("ascii", errors="replace")

def _dpt4_001_encode(v: Any) -> bytes:
    return str(v)[:1].encode("ascii", errors="replace").ljust(1, b"\x00")

def _dpt4_002_decode(b: bytes) -> str:
    return b[:1].decode("latin-1", errors="replace")

def _dpt4_002_encode(v: Any) -> bytes:
    return str(v)[:1].encode("latin-1", errors="replace").ljust(1, b"\x00")


# --- DPT 17.x — Scene Number (1 byte) ----------------------------------------
# Bits 5..0: Scene number 0-63 (= ETS scenes 1-64)
def _dpt17_decode(b: bytes) -> int:
    return b[0] & 0x3F

def _dpt17_encode(v: Any) -> bytes:
    return bytes([max(0, min(63, int(v))) & 0x3F])


# --- DPT 18.x — Scene Control (1 byte) ---------------------------------------
# Bit 7: 0=Activate, 1=Learn  |  Bits 5..0: Scene number (0..63)
# Wert = Szenennummer (0-63); negativ = Lern-Modus (z.B. -1 → Szene 0 lernen)
def _dpt18_decode(b: bytes) -> int:
    learn  = bool(b[0] & 0x80)
    scene  = b[0] & 0x3F
    return -(scene + 1) if learn else scene   # negativ = Lern-Modus

def _dpt18_encode(v: Any) -> bytes:
    iv = int(v)
    if iv < 0:                                # Lern-Modus
        return bytes([0x80 | ((-iv - 1) & 0x3F)])
    return bytes([iv & 0x3F])                 # Aktivieren


# --- DPT 19.x — Date and Time (8 bytes) --------------------------------------
# Byte0: Jahr-1900  Byte1: Monat  Byte2: Tag
# Byte3: DoW(7..5) | Stunde(4..0)  Byte4: Minute  Byte5: Sekunde
# Bytes 6-7: Qualitäts-/Status-Flags
def _dpt19_decode(b: bytes) -> str:
    import datetime
    try:
        year   = 1900 + b[0]
        month  = b[1] & 0x0F
        day    = b[2] & 0x1F
        hour   = b[3] & 0x1F
        minute = b[4] & 0x3F
        second = b[5] & 0x3F
        return datetime.datetime(year, month, day, hour, minute, second).isoformat()
    except Exception:
        return ""

def _dpt19_encode(v: Any) -> bytes:
    import datetime
    try:
        if isinstance(v, str):
            dt = datetime.datetime.fromisoformat(v)
        elif isinstance(v, (int, float)):
            dt = datetime.datetime.fromtimestamp(float(v))
        else:
            dt = datetime.datetime.now()
        dow = dt.isoweekday()   # 1=Mo … 7=So
        return bytes([
            dt.year - 1900,
            dt.month,
            dt.day,
            (dow << 5) | (dt.hour & 0x1F),
            dt.minute & 0x3F,
            dt.second & 0x3F,
            0x00,
            0x00,
        ])
    except Exception:
        return bytes(8)


# --- DPT 20.x — 1-Byte Enum/Mode ------------------------------------------------
# DPT20.102 HVACMode: 0=Auto, 1=Comfort, 2=Standby, 3=Economy, 4=BuildingProtection
_DPT20_102_VALID_RANGE = (0, 4)

def _dpt20_102_decode(b: bytes) -> int:
    return b[0] & 0xFF

def _dpt20_102_encode(v: Any) -> bytes:
    lo, hi = _DPT20_102_VALID_RANGE
    return bytes([max(lo, min(hi, int(v)))])


# --- DPT 240.x — Combined Position (3 bytes) ----------------------------------
# Byte 0 (MSB): HeightPosition U8  0-255 → 0-100 %  (~0.4 % resolution)
# Byte 1:       SlatsPosition  U8  0-255 → 0-100 %
# Byte 2 (LSB): Attributes     B8  Bit0=ValidHeightPos, Bit1=ValidSlatsPos
def _dpt240_800_decode(b: bytes) -> dict:
    height_raw = b[0]
    slats_raw  = b[1]
    attrs      = b[2]
    valid_h    = bool(attrs & 0x01)
    valid_s    = bool(attrs & 0x02)
    return {
        "height_pct": round(height_raw * 100.0 / 255.0, 1) if valid_h else None,
        "slats_pct":  round(slats_raw  * 100.0 / 255.0, 1) if valid_s else None,
        "valid_height": valid_h,
        "valid_slats":  valid_s,
    }

def _dpt240_800_encode(v: Any) -> bytes:
    import json as _json
    if isinstance(v, str):
        v = _json.loads(v)
    if isinstance(v, dict):
        h       = float(v.get("height_pct") or 0)
        s       = float(v.get("slats_pct")  or 0)
        valid_h = bool(v.get("valid_height", True))
        valid_s = bool(v.get("valid_slats",  True))
    else:
        h = s = 0.0
        valid_h = valid_s = False
    height_raw = max(0, min(255, round(h * 255.0 / 100.0)))
    slats_raw  = max(0, min(255, round(s * 255.0 / 100.0)))
    attrs      = (0x01 if valid_h else 0x00) | (0x02 if valid_s else 0x00)
    return bytes([height_raw, slats_raw, attrs])


# --- DPT 219.x — AlarmInfo (2 bytes) ------------------------------------------
# Byte 0 (High): Mode-Bits  |  Byte 1 (Low): Status-Bits
# Rohwert als Integer (0-65535); Interpretation abhängig vom Gerät
def _dpt219_decode(b: bytes) -> int:
    return struct.unpack(">H", b[:2])[0]

def _dpt219_encode(v: Any) -> bytes:
    return struct.pack(">H", max(0, min(0xFFFF, int(v))))


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
    decoder=lambda b: b.hex(),  # hex string is JSON-serialisable; raw bytes are not
)


# ---------------------------------------------------------------------------
# Built-in DPT registrations
# ---------------------------------------------------------------------------

def _register_builtin_dpts() -> None:
    defs = [
        # DPT 1 — 1-bit (vollständig nach KNX-Spec)
        DPTDefinition("DPT1.001", "Switch",                "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.002", "Bool",                  "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.003", "Enable",                "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.004", "Ramp",                  "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.005", "Alarm",                 "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.006", "Binary Value",          "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.007", "Step",                  "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.008", "Up/Down",               "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.009", "Open/Close",            "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.010", "Start/Stop",            "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.011", "State",                 "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.012", "Invert",                "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.013", "Dim Send Style",        "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.014", "Input Source",          "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.015", "Reset",                 "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.016", "Ack",                   "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.017", "Trigger",               "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.018", "Occupancy",             "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.019", "Window/Door",           "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.021", "Logical Function",      "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.022", "Scene A/B",             "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.023", "Shutter/Blinds Mode",   "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),
        DPTDefinition("DPT1.024", "Day/Night",             "BOOLEAN", "", 1, _dpt1_encode, _dpt1_decode),

        # DPT 3 — 4-bit relative control (lower nibble: bit3=direction, bits2-0=speed)
        DPTDefinition("DPT3.007", "Dimming",           "INTEGER", "",    1, _dpt3_encode, _dpt3_decode),
        DPTDefinition("DPT3.008", "Blinds",            "INTEGER", "",    1, _dpt3_encode, _dpt3_decode),

        # DPT 5 — 8-bit unsigned
        DPTDefinition("DPT5.001", "Scaling (0-100%)",     "FLOAT",   "%",   1, _dpt5_encode_percent, _dpt5_decode_percent),
        DPTDefinition("DPT5.003", "Angle",                "FLOAT",   "\u00b0", 1, _dpt5_encode_angle, _dpt5_decode_angle),
        DPTDefinition("DPT5.004", "Percent (0-255%)",     "INTEGER", "%",   1, _dpt5_encode_raw,     _dpt5_decode_raw),
        DPTDefinition("DPT5.005", "Decimal Factor",       "INTEGER", "",    1, _dpt5_encode_raw,     _dpt5_decode_raw),
        DPTDefinition("DPT5.006", "Tariff",               "INTEGER", "",    1, _dpt5_encode_raw,     _dpt5_decode_raw),
        DPTDefinition("DPT5.010", "Counter Pulses",       "INTEGER", "",    1, _dpt5_encode_raw,     _dpt5_decode_raw),

        # DPT 6 — 8-bit signed
        DPTDefinition("DPT6.001", "Percent (signed)",            "INTEGER", "%",  1, _dpt6_encode, _dpt6_decode),
        DPTDefinition("DPT6.010", "Counter Pulses (signed)",     "INTEGER", "",   1, _dpt6_encode, _dpt6_decode),

        # DPT 7 — 16-bit unsigned
        DPTDefinition("DPT7.001", "Counter Pulses",              "INTEGER", "",     2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.002", "Time Period (ms)",            "INTEGER", "ms",   2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.003", "Time Period (\u00d710 ms)",   "INTEGER", "ms",   2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.004", "Time Period (\u00d7100 ms)",  "INTEGER", "ms",   2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.005", "Time Period (s)",             "INTEGER", "s",    2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.006", "Time Period (min)",           "INTEGER", "min",  2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.007", "Time Period (h)",             "INTEGER", "h",    2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.011", "Length (mm)",                 "INTEGER", "mm",   2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.012", "Electric Current (mA)",       "INTEGER", "mA",   2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.013", "Brightness (lx)",             "INTEGER", "lx",   2, _dpt7_encode, _dpt7_decode),
        DPTDefinition("DPT7.600", "Colour Temperature (K)",      "INTEGER", "K",    2, _dpt7_encode, _dpt7_decode),

        # DPT 8 — 16-bit signed
        DPTDefinition("DPT8.001", "Counter Pulses (signed)",     "INTEGER", "",     2, _dpt8_encode, _dpt8_decode),
        DPTDefinition("DPT8.002", "Delta Time (ms)",             "INTEGER", "ms",   2, _dpt8_encode, _dpt8_decode),
        DPTDefinition("DPT8.003", "Delta Time (\u00d710 ms)",    "INTEGER", "ms",   2, _dpt8_encode, _dpt8_decode),
        DPTDefinition("DPT8.004", "Delta Time (\u00d7100 ms)",   "INTEGER", "ms",   2, _dpt8_encode, _dpt8_decode),
        DPTDefinition("DPT8.005", "Delta Time (s)",              "INTEGER", "s",    2, _dpt8_encode, _dpt8_decode),
        DPTDefinition("DPT8.006", "Delta Time (min)",            "INTEGER", "min",  2, _dpt8_encode, _dpt8_decode),
        DPTDefinition("DPT8.007", "Delta Time (h)",              "INTEGER", "h",    2, _dpt8_encode, _dpt8_decode),
        DPTDefinition("DPT8.010", "Percent (signed)",            "INTEGER", "%",    2, _dpt8_encode, _dpt8_decode),
        DPTDefinition("DPT8.011", "Rotation Angle",              "INTEGER", "\u00b0", 2, _dpt8_encode, _dpt8_decode),
        DPTDefinition("DPT8.012", "Length (m)",                  "INTEGER", "m",    2, _dpt8_encode, _dpt8_decode),

        # DPT 9 — 16-bit float EIS5 (vollständig nach KNX-Spec)
        DPTDefinition("DPT9.001", "Temperature",          "FLOAT", "\u00b0C",   2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.002", "Temperature Diff",     "FLOAT", "K",         2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.003", "Temperature (K/h)",    "FLOAT", "K/h",       2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.004", "Illuminance",          "FLOAT", "lx",        2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.005", "Wind Speed",           "FLOAT", "m/s",       2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.006", "Air Pressure",         "FLOAT", "Pa",        2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.007", "Humidity",             "FLOAT", "%",         2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.008", "Air Quality (CO2)",    "FLOAT", "ppm",       2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.009", "Air Flow",             "FLOAT", "m\u00b3/h", 2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.010", "Time (s)",             "FLOAT", "s",         2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.011", "Time (ms)",            "FLOAT", "ms",        2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.020", "Voltage (mV)",         "FLOAT", "mV",        2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.021", "Current (mA)",         "FLOAT", "mA",        2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.022", "Power Density",        "FLOAT", "W/m\u00b2", 2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.023", "Kelvin/Percent",       "FLOAT", "K/%",       2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.024", "Power",                "FLOAT", "kW",        2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.025", "Volume Flow",          "FLOAT", "l/h",       2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.026", "Rain Amount",          "FLOAT", "l/m\u00b2", 2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.027", "Temperature (\u00b0F)","FLOAT", "\u00b0F",   2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.028", "Wind Speed (km/h)",    "FLOAT", "km/h",      2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.029", "Absolute Humidity",    "FLOAT", "g/m\u00b3", 2, _dpt9_encode, _dpt9_decode),
        DPTDefinition("DPT9.030", "Concentration",        "FLOAT", "\u03bcg/m\u00b3", 2, _dpt9_encode, _dpt9_decode),

        # DPT 2 — 2-bit controlled value
        DPTDefinition("DPT2.001", "Switch Control",      "INTEGER", "",    1, _dpt2_encode, _dpt2_decode),
        DPTDefinition("DPT2.002", "Bool Control",        "INTEGER", "",    1, _dpt2_encode, _dpt2_decode),

        # DPT 4 — 1-byte character
        DPTDefinition("DPT4.001", "Character (ASCII)",      "STRING", "",  1, _dpt4_001_encode, _dpt4_001_decode),
        DPTDefinition("DPT4.002", "Character (ISO 8859-1)", "STRING", "",  1, _dpt4_002_encode, _dpt4_002_decode),

        # DPT 12 — 32-bit unsigned
        DPTDefinition("DPT12.001", "Counter (32-bit)",    "INTEGER", "",     4, _dpt12_encode, _dpt12_decode),

        # DPT 13 — 32-bit signed
        DPTDefinition("DPT13.001", "Counter (32-bit signed)", "INTEGER", "", 4, _dpt13_encode, _dpt13_decode),
        DPTDefinition("DPT13.010", "Active Energy (Wh)",  "INTEGER", "Wh",  4, _dpt13_encode, _dpt13_decode),
        DPTDefinition("DPT13.013", "Active Energy (kWh)", "INTEGER", "kWh", 4, _dpt13_encode, _dpt13_decode),

        # DPT 14 — 32-bit IEEE float (vollständig nach KNX-Spec v02.02.01)
        DPTDefinition("DPT14.000", "Acceleration",              "FLOAT", "m/s\u00b2",   4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.001", "Acceleration Angular",      "FLOAT", "rad/s\u00b2", 4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.002", "Activation Energy",         "FLOAT", "J/mol",       4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.004", "Amount of Substance",       "FLOAT", "mol",         4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.006", "Angle (rad)",               "FLOAT", "rad",         4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.007", "Angle (deg)",               "FLOAT", "\u00b0",      4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.009", "Angular Velocity",          "FLOAT", "rad/s",       4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.010", "Area",                      "FLOAT", "m\u00b2",     4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.011", "Capacitance",               "FLOAT", "F",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.017", "Density",                   "FLOAT", "kg/m\u00b3",  4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.018", "Electric Charge",           "FLOAT", "C",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.019", "Electric Current",          "FLOAT", "A",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.020", "Electric Current Density",  "FLOAT", "A/m\u00b2",   4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.027", "Electric Potential",        "FLOAT", "V",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.028", "Electric Potential Diff",   "FLOAT", "V",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.030", "Electromotive Force",       "FLOAT", "V",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.031", "Energy",                    "FLOAT", "J",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.032", "Force",                     "FLOAT", "N",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.033", "Frequency",                 "FLOAT", "Hz",          4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.034", "Angular Frequency",         "FLOAT", "rad/s",       4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.036", "Heat Flow Rate",            "FLOAT", "W",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.037", "Heat Quantity",             "FLOAT", "J",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.038", "Impedance",                 "FLOAT", "\u03a9",      4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.039", "Length",                    "FLOAT", "m",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.042", "Luminous Flux",             "FLOAT", "lm",          4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.043", "Luminous Intensity",        "FLOAT", "cd",          4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.051", "Mass",                      "FLOAT", "kg",          4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.052", "Mass Flux",                 "FLOAT", "kg/s",        4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.054", "Phase Angle (rad)",         "FLOAT", "rad",         4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.055", "Phase Angle (deg)",         "FLOAT", "\u00b0",      4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.056", "Power",                     "FLOAT", "W",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.057", "Power Factor",              "FLOAT", "",            4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.058", "Pressure",                  "FLOAT", "Pa",          4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.059", "Reactance",                 "FLOAT", "\u03a9",      4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.060", "Resistance",                "FLOAT", "\u03a9",      4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.062", "Self Inductance",           "FLOAT", "H",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.064", "Sound Intensity",           "FLOAT", "W/m\u00b2",   4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.065", "Speed",                     "FLOAT", "m/s",         4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.067", "Surface Tension",           "FLOAT", "N/m",         4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.068", "Temperature (common)",      "FLOAT", "\u00b0C",     4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.069", "Temperature (absolute)",    "FLOAT", "K",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.070", "Temperature Difference",    "FLOAT", "K",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.072", "Thermal Conductivity",      "FLOAT", "W/(mK)",      4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.074", "Time",                      "FLOAT", "s",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.075", "Torque",                    "FLOAT", "Nm",          4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.076", "Volume",                    "FLOAT", "m\u00b3",     4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.077", "Volume Flux",               "FLOAT", "m\u00b3/s",   4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.078", "Weight",                    "FLOAT", "N",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.079", "Work",                      "FLOAT", "J",           4, _dpt14_encode, _dpt14_decode),
        DPTDefinition("DPT14.080", "Apparent Power",            "FLOAT", "VA",          4, _dpt14_encode, _dpt14_decode),

        # DPT 10 — Time of Day (3 bytes)
        DPTDefinition("DPT10.001", "Time of Day",         "TIME",   "",      3, _dpt10_encode, _dpt10_decode),

        # DPT 11 — Date (3 bytes)
        DPTDefinition("DPT11.001", "Date",                "DATE",   "",      3, _dpt11_encode, _dpt11_decode),

        # DPT 16 — 14-byte string
        DPTDefinition("DPT16.000", "ASCII String",        "STRING", "",     14, _dpt16_encode, _dpt16_decode),
        DPTDefinition("DPT16.001", "ISO 8859-1 String",   "STRING", "",     14, _dpt16_encode, _dpt16_decode),

        # DPT 18 — Scene Control (1 byte)
        DPTDefinition("DPT18.001", "Scene Control",       "INTEGER", "",     1, _dpt18_encode, _dpt18_decode),

        # DPT 19 — Date and Time (8 bytes)
        DPTDefinition("DPT19.001", "Date Time",           "STRING", "",      8, _dpt19_encode, _dpt19_decode),

        # DPT 20 — 1-Byte Enum/Mode (N8, vollständig nach KNX-Spec)
        DPTDefinition("DPT20.001", "SCLO Mode",              "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.002", "Building Mode",          "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.003", "Occupancy Mode",         "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.004", "Priority",               "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.005", "Light Application Mode", "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.006", "Application Area",       "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.007", "Alarm Class Type",       "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.008", "PSU Mode",               "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.011", "Error Class System",     "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.012", "Error Class HVAC",       "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.013", "Time Delay",             "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.014", "Beaufort Wind Scale",    "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.017", "Sensor Select",          "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.020", "Actuator Connect Type",  "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.021", "Cloud Cover",            "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.100", "Fuel Type",              "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.101", "Burner Type",            "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.102", "HVAC Operating Mode",    "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.103", "DHW Mode",               "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.104", "Load Priority",          "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.105", "HVAC Controller Mode",   "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.106", "HVAC Emergency Mode",    "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.107", "Changeover Mode",        "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.108", "Valve Mode",             "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.109", "Damper Mode",            "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.110", "Heater Mode",            "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.111", "Fan Mode",               "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.112", "Master/Slave Mode",      "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.113", "Status Room Setpoint",   "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.600", "Behaviour Lock/Unlock",  "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.601", "Behaviour Bus PowerUp",  "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.602", "DALI Fade Time",         "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.603", "Blinking Mode",          "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),
        DPTDefinition("DPT20.604", "Light Control Mode",     "INTEGER", "", 1, _dpt20_decode, _dpt20_encode),

        # DPT 29 — 64-bit signed (V64) — Smart Metering
        DPTDefinition("DPT29.010", "Active Energy (Wh)",   "INTEGER", "Wh",   8, _dpt29_encode, _dpt29_decode),
        DPTDefinition("DPT29.011", "Apparent Energy (VAh)","INTEGER", "VAh",  8, _dpt29_encode, _dpt29_decode),
        DPTDefinition("DPT29.012", "Reactive Energy (VARh)","INTEGER","VARh", 8, _dpt29_encode, _dpt29_decode),

        # DPT 17 — Scene Number (1 byte, 0-63)
        DPTDefinition("DPT17.001", "Scene Number",        "INTEGER", "",     1, _dpt17_encode, _dpt17_decode),

        # DPT 219 — AlarmInfo (2 bytes)
        DPTDefinition("DPT219.001", "AlarmInfo",          "INTEGER", "",     2, _dpt219_encode, _dpt219_decode),

        # DPT 240 — Combined Position (3 bytes, Shutter & Blinds)
        DPTDefinition("DPT240.800", "Combined Position",  "STRING",  "",     3, _dpt240_800_encode, _dpt240_800_decode),
    ]
    for d in defs:
        DPTRegistry.register(d)


_register_builtin_dpts()
