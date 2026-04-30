"""Unit tests for obs/adapters/knx/dpt_registry.py

Covers:
  - Registry lookups (known / unknown DPTs)
  - DPT1  — 1-bit boolean encode/decode
  - DPT5  — 8-bit unsigned percent + raw + angle
  - DPT6  — 8-bit signed
  - DPT7  — 16-bit unsigned
  - DPT8  — 16-bit signed
  - DPT9  — 16-bit KNX float (EIS5) — the most critical type
  - DPT12 — 32-bit unsigned
  - DPT13 — 32-bit signed
  - DPT14 — 32-bit IEEE 754 float
  - DPT16 — 14-byte ASCII string
  - Roundtrip consistency for all major DPTs
  - UNKNOWN fallback behaviour
"""

from __future__ import annotations

from obs.adapters.knx.dpt_registry import DPTDefinition, DPTRegistry

# ===========================================================================
# Helpers
# ===========================================================================


def encode(dpt_id: str, value) -> bytes:
    return DPTRegistry.get(dpt_id).encoder(value)


def decode(dpt_id: str, raw: bytes):
    return DPTRegistry.get(dpt_id).decoder(raw)


def roundtrip(dpt_id: str, value, abs_tol=0.0):
    raw = encode(dpt_id, value)
    result = decode(dpt_id, raw)
    if abs_tol:
        assert abs(result - value) <= abs_tol, f"{result} ≉ {value} (tol={abs_tol})"
    else:
        assert result == value
    return result


# ===========================================================================
# Registry lookups
# ===========================================================================


class TestRegistryLookup:
    def test_known_dpt_returns_definition(self):
        d = DPTRegistry.get("DPT9.001")
        assert isinstance(d, DPTDefinition)
        assert d.dpt_id == "DPT9.001"
        assert d.data_type == "FLOAT"

    def test_unknown_dpt_returns_unknown_fallback(self):
        d = DPTRegistry.get("DPT99.999")
        assert d.data_type == "UNKNOWN"
        assert d.dpt_id == "UNKNOWN"

    def test_unknown_never_raises(self):
        d = DPTRegistry.get("DOES_NOT_EXIST")
        assert d is not None

    def test_all_returns_dict(self):
        all_dpts = DPTRegistry.all()
        assert isinstance(all_dpts, dict)
        assert len(all_dpts) > 50  # sanity: we registered many DPTs

    def test_by_data_type_float(self):
        floats = DPTRegistry.by_data_type("FLOAT")
        ids = [d.dpt_id for d in floats]
        assert "DPT9.001" in ids
        assert "DPT14.055" in ids

    def test_by_data_type_boolean(self):
        booleans = DPTRegistry.by_data_type("BOOLEAN")
        assert any(d.dpt_id.startswith("DPT1.") for d in booleans)

    def test_dpt9001_metadata(self):
        d = DPTRegistry.get("DPT9.001")
        assert d.name == "Temperature"
        assert d.unit == "°C"
        assert d.size_bytes == 2

    def test_dpt1001_metadata(self):
        d = DPTRegistry.get("DPT1.001")
        assert d.data_type == "BOOLEAN"
        assert d.size_bytes == 1


# ===========================================================================
# DPT 1 — 1-bit Boolean
# ===========================================================================


class TestDPT1:
    def test_encode_true(self):
        assert encode("DPT1.001", True) == bytes([0x01])

    def test_encode_false(self):
        assert encode("DPT1.001", False) == bytes([0x00])

    def test_decode_on(self):
        assert decode("DPT1.001", bytes([0x01])) is True

    def test_decode_off(self):
        assert decode("DPT1.001", bytes([0x00])) is False

    def test_decode_only_lsb_matters(self):
        # KNX 1-bit uses only LSB of the byte
        assert decode("DPT1.001", bytes([0xFE])) is False  # LSB=0
        assert decode("DPT1.001", bytes([0xFF])) is True  # LSB=1

    def test_roundtrip_true(self):
        roundtrip("DPT1.001", True)

    def test_roundtrip_false(self):
        roundtrip("DPT1.001", False)

    def test_all_dpt1_variants_registered(self):
        for i in (1, 2, 3, 7, 8, 9, 10, 11, 17, 18, 19, 21, 22, 23):
            dpt_id = f"DPT1.{i:03d}"
            d = DPTRegistry.get(dpt_id)
            assert d.data_type == "BOOLEAN", f"{dpt_id} should be BOOLEAN"

    # String inputs — fix for issue #287:
    # value_map can produce string "0"/"false"/"off" as output; _dpt1_encode
    # must treat those as falsy rather than relying on Python string truthiness.
    def test_encode_string_zero(self):
        assert encode("DPT1.001", "0") == bytes([0x00])

    def test_encode_string_one(self):
        assert encode("DPT1.001", "1") == bytes([0x01])

    def test_encode_string_false(self):
        assert encode("DPT1.001", "false") == bytes([0x00])

    def test_encode_string_true(self):
        assert encode("DPT1.001", "true") == bytes([0x01])

    def test_encode_string_off(self):
        assert encode("DPT1.001", "off") == bytes([0x00])

    def test_encode_string_on(self):
        assert encode("DPT1.001", "on") == bytes([0x01])

    def test_encode_int_1(self):
        assert encode("DPT1.001", 1) == bytes([0x01])

    def test_encode_int_0(self):
        assert encode("DPT1.001", 0) == bytes([0x00])


# ===========================================================================
# DPT 5 — 8-bit unsigned
# ===========================================================================


class TestDPT5:
    # DPT5.001 — percentage 0–100%
    def test_percent_zero(self):
        raw = encode("DPT5.001", 0.0)
        assert raw == bytes([0x00])

    def test_percent_100_encodes_to_255(self):
        raw = encode("DPT5.001", 100.0)
        assert raw == bytes([0xFF])

    def test_percent_50_roundtrip(self):
        # 50% → encode → decode should be within ±0.5%
        raw = encode("DPT5.001", 50.0)
        val = decode("DPT5.001", raw)
        assert abs(val - 50.0) < 0.5

    def test_percent_clamp_above_100(self):
        raw = encode("DPT5.001", 150.0)
        assert raw == bytes([0xFF])

    def test_percent_clamp_below_0(self):
        raw = encode("DPT5.001", -10.0)
        assert raw == bytes([0x00])

    # DPT5.004 — raw 0–255
    def test_raw_encode_decode(self):
        for v in (0, 1, 127, 128, 254, 255):
            assert decode("DPT5.004", encode("DPT5.004", v)) == v

    def test_raw_clamp_above_255(self):
        raw = encode("DPT5.004", 300)
        assert raw == bytes([0xFF])

    # DPT5.003 — angle 0°–360°
    def test_angle_zero(self):
        raw = encode("DPT5.003", 0.0)
        assert raw == bytes([0x00])

    def test_angle_360_encodes_to_255(self):
        raw = encode("DPT5.003", 360.0)
        assert raw == bytes([0xFF])

    def test_angle_180_roundtrip(self):
        raw = encode("DPT5.003", 180.0)
        val = decode("DPT5.003", raw)
        assert abs(val - 180.0) < 1.5  # EIS resolution


# ===========================================================================
# DPT 6 — 8-bit signed
# ===========================================================================


class TestDPT6:
    def test_positive(self):
        roundtrip("DPT6.001", 42)

    def test_negative(self):
        roundtrip("DPT6.001", -42)

    def test_zero(self):
        roundtrip("DPT6.001", 0)

    def test_min_boundary(self):
        roundtrip("DPT6.001", -128)

    def test_max_boundary(self):
        roundtrip("DPT6.001", 127)

    def test_clamp_above_127(self):
        raw = encode("DPT6.001", 200)
        assert decode("DPT6.001", raw) == 127

    def test_clamp_below_minus128(self):
        raw = encode("DPT6.001", -200)
        assert decode("DPT6.001", raw) == -128


# ===========================================================================
# DPT 7 — 16-bit unsigned
# ===========================================================================


class TestDPT7:
    def test_zero(self):
        roundtrip("DPT7.001", 0)

    def test_max(self):
        roundtrip("DPT7.001", 65535)

    def test_typical_lux(self):
        roundtrip("DPT7.013", 500)  # 500 lux

    def test_color_temperature(self):
        roundtrip("DPT7.600", 4000)  # 4000 K


# ===========================================================================
# DPT 8 — 16-bit signed
# ===========================================================================


class TestDPT8:
    def test_positive(self):
        roundtrip("DPT8.001", 1000)

    def test_negative(self):
        roundtrip("DPT8.001", -1000)

    def test_boundaries(self):
        roundtrip("DPT8.001", -32768)
        roundtrip("DPT8.001", 32767)


# ===========================================================================
# DPT 9 — 16-bit KNX float (EIS5)  ← most critical
# ===========================================================================


class TestDPT9:
    """DPT9 uses a proprietary 2-byte float format (sign + exponent + mantissa).
    Precision is limited — typical tolerance is ±0.02 for temperature values.
    """

    def test_temperature_21_4(self):
        raw = encode("DPT9.001", 21.4)
        val = decode("DPT9.001", raw)
        assert abs(val - 21.4) < 0.02

    def test_temperature_zero(self):
        raw = encode("DPT9.001", 0.0)
        val = decode("DPT9.001", raw)
        assert abs(val) < 0.01

    def test_temperature_negative(self):
        raw = encode("DPT9.001", -10.5)
        val = decode("DPT9.001", raw)
        assert abs(val - (-10.5)) < 0.02

    def test_temperature_minus_273(self):
        # Absolute zero — near lower bound of DPT9
        raw = encode("DPT9.001", -273.0)
        val = decode("DPT9.001", raw)
        assert abs(val - (-273.0)) < 1.0  # wider tolerance at extremes

    def test_temperature_high(self):
        raw = encode("DPT9.001", 670760.0)  # near max representable
        val = decode("DPT9.001", raw)
        # At extreme magnitudes precision degrades
        assert val > 0

    def test_humidity_percent(self):
        raw = encode("DPT9.007", 55.0)
        val = decode("DPT9.007", raw)
        assert abs(val - 55.0) < 0.1

    def test_wind_speed(self):
        raw = encode("DPT9.004", 3.5)
        val = decode("DPT9.004", raw)
        assert abs(val - 3.5) < 0.05

    def test_co2_ppm(self):
        raw = encode("DPT9.008", 800.0)
        val = decode("DPT9.008", raw)
        assert abs(val - 800.0) < 1.0

    def test_power_watts(self):
        raw = encode("DPT9.010", 2500.0)
        val = decode("DPT9.010", raw)
        assert abs(val - 2500.0) < 5.0

    def test_result_is_float(self):
        val = decode("DPT9.001", encode("DPT9.001", 20.0))
        assert isinstance(val, float)

    def test_all_dpt9_variants_registered(self):
        for sub in range(1, 31):
            dpt_id = f"DPT9.{sub:03d}"
            d = DPTRegistry.get(dpt_id)
            # All registered DPT9 must be FLOAT, unknowns return UNKNOWN fallback
            if d.dpt_id != "UNKNOWN":
                assert d.data_type == "FLOAT", f"{dpt_id} should be FLOAT"


# ===========================================================================
# DPT 12 — 32-bit unsigned
# ===========================================================================


class TestDPT12:
    def test_zero(self):
        roundtrip("DPT12.001", 0)

    def test_large_value(self):
        roundtrip("DPT12.001", 123456789)

    def test_max(self):
        roundtrip("DPT12.001", 0xFFFFFFFF)

    def test_byte_length(self):
        assert len(encode("DPT12.001", 1)) == 4


# ===========================================================================
# DPT 13 — 32-bit signed
# ===========================================================================


class TestDPT13:
    def test_positive_energy(self):
        roundtrip("DPT13.013", 5000)  # 5000 kWh

    def test_negative(self):
        roundtrip("DPT13.001", -100000)

    def test_boundaries(self):
        roundtrip("DPT13.001", -2147483648)
        roundtrip("DPT13.001", 2147483647)


# ===========================================================================
# DPT 14 — 32-bit IEEE 754 float
# ===========================================================================


class TestDPT14:
    def test_power_kw(self):
        roundtrip("DPT14.055", 3.5, abs_tol=1e-4)

    def test_voltage(self):
        # DPT14.027 = Electric Potential (V) — DPT14.024 is not registered
        roundtrip("DPT14.027", 230.0, abs_tol=1e-3)

    def test_negative_value(self):
        roundtrip("DPT14.019", -5.0, abs_tol=1e-4)  # current

    def test_byte_length(self):
        assert len(encode("DPT14.055", 1.0)) == 4

    def test_result_is_float(self):
        val = decode("DPT14.055", encode("DPT14.055", 1.23))
        assert isinstance(val, float)


# ===========================================================================
# DPT 16 — 14-byte ASCII string
# ===========================================================================


class TestDPT16:
    def test_short_string(self):
        roundtrip("DPT16.000", "Hello")

    def test_exactly_14_chars(self):
        roundtrip("DPT16.000", "ABCDEFGHIJKLMN")

    def test_truncated_to_14_chars(self):
        long_str = "A" * 20
        raw = encode("DPT16.000", long_str)
        result = decode("DPT16.000", raw)
        assert len(result) == 14

    def test_empty_string(self):
        raw = encode("DPT16.000", "")
        result = decode("DPT16.000", raw)
        assert result == ""

    def test_byte_length(self):
        assert len(encode("DPT16.000", "test")) == 14

    def test_null_padding_stripped_on_decode(self):
        raw = encode("DPT16.000", "Hi")
        result = decode("DPT16.000", raw)
        assert result == "Hi"
        assert "\x00" not in result


# ===========================================================================
# UNKNOWN DPT fallback
# ===========================================================================


class TestUnknownDPT:
    def test_unknown_dpt_id_returns_unknown(self):
        d = DPTRegistry.get("DPT99.001")
        assert d.data_type == "UNKNOWN"

    def test_unknown_encoder_accepts_bytes(self):
        d = DPTRegistry.get("DPT99.001")
        raw = d.encoder(b"\xab\xcd")
        assert raw == b"\xab\xcd"

    def test_unknown_encoder_accepts_other(self):
        d = DPTRegistry.get("DPT99.001")
        raw = d.encoder("hello")
        assert isinstance(raw, bytes)

    def test_unknown_decoder_returns_hex_string(self):
        d = DPTRegistry.get("DPT99.001")
        result = d.decoder(b"\xde\xad")
        assert result == "dead"
        assert isinstance(result, str)
