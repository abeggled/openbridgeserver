"""Unit tests for KNX DPT codec functions in dpt_registry.py.

Covers all codec helpers and DPTRegistry class methods not exercised by
the adapter-level tests.
"""

from __future__ import annotations

import datetime

import pytest

from obs.adapters.knx.dpt_registry import DPTRegistry, _UNKNOWN_DPT


# ---------------------------------------------------------------------------
# DPTRegistry class methods
# ---------------------------------------------------------------------------


class TestDPTRegistryMethods:
    def test_all_returns_populated_dict(self):
        result = DPTRegistry.all()
        assert isinstance(result, dict)
        assert len(result) > 50
        assert "DPT1.001" in result
        assert "DPT9.001" in result

    def test_all_returns_copy(self):
        r1 = DPTRegistry.all()
        r2 = DPTRegistry.all()
        assert r1 is not r2

    def test_by_data_type_float(self):
        floats = DPTRegistry.by_data_type("FLOAT")
        assert len(floats) > 0
        assert all(d.data_type == "FLOAT" for d in floats)
        ids = [d.dpt_id for d in floats]
        assert "DPT9.001" in ids

    def test_by_data_type_boolean(self):
        booleans = DPTRegistry.by_data_type("BOOLEAN")
        assert len(booleans) >= 20
        assert all(d.data_type == "BOOLEAN" for d in booleans)

    def test_by_data_type_integer(self):
        integers = DPTRegistry.by_data_type("INTEGER")
        assert len(integers) > 0

    def test_by_data_type_unknown_returns_empty(self):
        result = DPTRegistry.by_data_type("NONEXISTENT_TYPE")
        assert result == []


# ---------------------------------------------------------------------------
# DPT 1.x — 1-bit BOOLEAN
# ---------------------------------------------------------------------------


class TestDPT1Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT1.001")

    def test_encode_true_bool(self):
        assert self.dpt.encoder(True) == bytes([0x01])

    def test_encode_false_bool(self):
        assert self.dpt.encoder(False) == bytes([0x00])

    def test_encode_string_true(self):
        for s in ("1", "true", "True", "TRUE", "on", "yes"):
            assert self.dpt.encoder(s) == bytes([0x01]), f"Expected True for '{s}'"

    def test_encode_string_false(self):
        for s in ("0", "false", "False", "FALSE", "off", "no", ""):
            assert self.dpt.encoder(s) == bytes([0x00]), f"Expected False for '{s}'"

    def test_encode_int_nonzero(self):
        assert self.dpt.encoder(1) == bytes([0x01])
        assert self.dpt.encoder(255) == bytes([0x01])

    def test_encode_int_zero(self):
        assert self.dpt.encoder(0) == bytes([0x00])

    def test_decode_bit1(self):
        assert self.dpt.decoder(bytes([0x01])) is True

    def test_decode_bit0(self):
        assert self.dpt.decoder(bytes([0x00])) is False

    def test_decode_masks_upper_bits(self):
        assert self.dpt.decoder(bytes([0xFE])) is False
        assert self.dpt.decoder(bytes([0xFF])) is True

    def test_round_trip_all_variants(self):
        dpt = DPTRegistry.get("DPT1.008")  # Up/Down, same codec
        assert dpt.decoder(dpt.encoder(True)) is True
        assert dpt.decoder(dpt.encoder(False)) is False


# ---------------------------------------------------------------------------
# DPT 2.x — 2-bit controlled value
# ---------------------------------------------------------------------------


class TestDPT2Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT2.001")

    def test_encode_decode_round_trip(self):
        for v in range(4):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_encode_clamps_to_0_3(self):
        assert self.dpt.encoder(0) == bytes([0x00])
        assert self.dpt.encoder(3) == bytes([0x03])
        assert self.dpt.encoder(4) == bytes([0x03])  # clamped
        assert self.dpt.encoder(-1) == bytes([0x00])  # clamped

    def test_decode_masks_lower_2_bits(self):
        assert self.dpt.decoder(bytes([0xFF])) == 3
        assert self.dpt.decoder(bytes([0xFC])) == 0


# ---------------------------------------------------------------------------
# DPT 3.x — 4-bit relative control
# ---------------------------------------------------------------------------


class TestDPT3Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT3.007")  # Dimming

    def test_encode_decode_round_trip(self):
        for v in range(16):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_encode_masks_to_nibble(self):
        assert self.dpt.encoder(0x0F) == bytes([0x0F])
        assert self.dpt.encoder(0xFF) == bytes([0x0F])  # only lower nibble

    def test_decode_masks_lower_nibble(self):
        assert self.dpt.decoder(bytes([0xF0])) == 0
        assert self.dpt.decoder(bytes([0xFF])) == 0x0F

    def test_blinds_dpt(self):
        dpt = DPTRegistry.get("DPT3.008")
        assert dpt.decoder(dpt.encoder(7)) == 7


# ---------------------------------------------------------------------------
# DPT 4.x — 1-byte character
# ---------------------------------------------------------------------------


class TestDPT4Codecs:
    def test_ascii_encode_decode(self):
        dpt = DPTRegistry.get("DPT4.001")
        for ch in ("A", "z", "0", "!"):
            assert dpt.decoder(dpt.encoder(ch)) == ch

    def test_ascii_truncates_to_one_char(self):
        dpt = DPTRegistry.get("DPT4.001")
        result = dpt.decoder(dpt.encoder("AB"))
        assert len(result) == 1
        assert result == "A"

    def test_latin1_encode_decode(self):
        dpt = DPTRegistry.get("DPT4.002")
        assert dpt.decoder(dpt.encoder("A")) == "A"

    def test_ascii_empty_string_encodes_null(self):
        dpt = DPTRegistry.get("DPT4.001")
        result = dpt.encoder("")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# DPT 5.x — 8-bit unsigned
# ---------------------------------------------------------------------------


class TestDPT5Codecs:
    def test_percent_decode_0(self):
        dpt = DPTRegistry.get("DPT5.001")
        assert dpt.decoder(bytes([0])) == 0.0

    def test_percent_decode_255(self):
        dpt = DPTRegistry.get("DPT5.001")
        assert dpt.decoder(bytes([255])) == 100.0

    def test_percent_encode_0(self):
        dpt = DPTRegistry.get("DPT5.001")
        assert dpt.encoder(0) == bytes([0])

    def test_percent_encode_100(self):
        dpt = DPTRegistry.get("DPT5.001")
        assert dpt.encoder(100) == bytes([255])

    def test_percent_round_trip_midpoint(self):
        dpt = DPTRegistry.get("DPT5.001")
        raw = dpt.encoder(50.0)
        val = dpt.decoder(raw)
        assert abs(val - 50.0) < 1.0

    def test_percent_encode_clamps_above_100(self):
        dpt = DPTRegistry.get("DPT5.001")
        assert dpt.encoder(200) == bytes([255])

    def test_percent_encode_clamps_below_0(self):
        dpt = DPTRegistry.get("DPT5.001")
        assert dpt.encoder(-10) == bytes([0])

    def test_angle_decode_0(self):
        dpt = DPTRegistry.get("DPT5.003")
        assert dpt.decoder(bytes([0])) == 0.0

    def test_angle_decode_255(self):
        dpt = DPTRegistry.get("DPT5.003")
        assert dpt.decoder(bytes([255])) == 360.0

    def test_angle_round_trip_180(self):
        dpt = DPTRegistry.get("DPT5.003")
        raw = dpt.encoder(180.0)
        val = dpt.decoder(raw)
        assert abs(val - 180.0) < 2.0

    def test_raw_encode_decode(self):
        dpt = DPTRegistry.get("DPT5.010")  # Counter Pulses, raw codec
        for v in (0, 1, 127, 255):
            assert dpt.decoder(dpt.encoder(v)) == v

    def test_raw_clamps_above_255(self):
        dpt = DPTRegistry.get("DPT5.010")
        assert dpt.encoder(300) == bytes([255])

    def test_raw_clamps_below_0(self):
        dpt = DPTRegistry.get("DPT5.010")
        assert dpt.encoder(-5) == bytes([0])


# ---------------------------------------------------------------------------
# DPT 6.x — 8-bit signed
# ---------------------------------------------------------------------------


class TestDPT6Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT6.001")

    def test_encode_decode_round_trip(self):
        for v in (-128, -1, 0, 1, 127):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_clamps_above_127(self):
        assert self.dpt.decoder(self.dpt.encoder(200)) == 127

    def test_clamps_below_minus128(self):
        assert self.dpt.decoder(self.dpt.encoder(-200)) == -128

    def test_signed_counter_pulses(self):
        dpt = DPTRegistry.get("DPT6.010")
        assert dpt.decoder(dpt.encoder(-5)) == -5


# ---------------------------------------------------------------------------
# DPT 7.x — 16-bit unsigned
# ---------------------------------------------------------------------------


class TestDPT7Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT7.001")

    def test_encode_decode_round_trip(self):
        for v in (0, 1, 1000, 65535):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_clamps_above_65535(self):
        assert self.dpt.decoder(self.dpt.encoder(70000)) == 65535

    def test_clamps_below_0(self):
        assert self.dpt.decoder(self.dpt.encoder(-1)) == 0

    def test_colour_temperature(self):
        dpt = DPTRegistry.get("DPT7.600")
        assert dpt.decoder(dpt.encoder(2700)) == 2700


# ---------------------------------------------------------------------------
# DPT 8.x — 16-bit signed
# ---------------------------------------------------------------------------


class TestDPT8Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT8.001")

    def test_encode_decode_round_trip(self):
        for v in (-32768, -1, 0, 1, 32767):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_clamps_above_32767(self):
        assert self.dpt.decoder(self.dpt.encoder(40000)) == 32767

    def test_clamps_below_minus32768(self):
        assert self.dpt.decoder(self.dpt.encoder(-40000)) == -32768

    def test_rotation_angle(self):
        dpt = DPTRegistry.get("DPT8.011")
        assert dpt.decoder(dpt.encoder(-90)) == -90


# ---------------------------------------------------------------------------
# DPT 9.x — 16-bit KNX float (EIS5)
# ---------------------------------------------------------------------------


class TestDPT9Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT9.001")

    def test_encode_decode_zero(self):
        assert abs(self.dpt.decoder(self.dpt.encoder(0.0))) < 0.01

    def test_encode_decode_positive(self):
        val = 21.5
        assert abs(self.dpt.decoder(self.dpt.encoder(val)) - val) < 0.1

    def test_encode_decode_negative(self):
        val = -5.0
        assert abs(self.dpt.decoder(self.dpt.encoder(val)) - val) < 0.1

    def test_encode_very_large_value_clamps(self):
        raw = self.dpt.encoder(1e10)
        assert len(raw) == 2

    def test_encode_very_negative_value_clamps(self):
        raw = self.dpt.encoder(-1e10)
        assert len(raw) == 2

    def test_humidity(self):
        dpt = DPTRegistry.get("DPT9.007")
        val = 55.3
        assert abs(dpt.decoder(dpt.encoder(val)) - val) < 0.2

    def test_wind_speed(self):
        dpt = DPTRegistry.get("DPT9.005")
        val = 12.4
        assert abs(dpt.decoder(dpt.encoder(val)) - val) < 0.2


# ---------------------------------------------------------------------------
# DPT 10.x — Time of Day
# ---------------------------------------------------------------------------


class TestDPT10Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT10.001")

    def test_decode_returns_time_object(self):
        result = self.dpt.decoder(bytes([10, 30, 0]))
        assert result == datetime.time(10, 30, 0)

    def test_decode_invalid_hour_raises(self):
        with pytest.raises(ValueError):
            self.dpt.decoder(bytes([0x1F, 0x00, 0x00]))

    def test_decode_short_payload_raises(self):
        with pytest.raises(ValueError):
            self.dpt.decoder(bytes([10, 30]))

    def test_encode_from_time_object(self):
        t = datetime.time(14, 45, 30)
        raw = self.dpt.encoder(t)
        assert len(raw) == 3
        assert raw[0] == 14
        assert raw[1] == 45
        assert raw[2] == 30

    def test_encode_from_iso_string(self):
        raw = self.dpt.encoder("08:00:00")
        assert raw[0] == 8
        assert raw[1] == 0
        assert raw[2] == 0

    def test_encode_from_int_seconds(self):
        raw = self.dpt.encoder(3661)  # 1h 1m 1s
        assert raw[0] == 1
        assert raw[1] == 1
        assert raw[2] == 1

    def test_encode_invalid_fallback(self):
        raw = self.dpt.encoder(None)
        # Falls through to datetime.now().time() → 3 bytes
        assert len(raw) == 3

    def test_round_trip_via_string(self):
        original = "09:15:45"
        raw = self.dpt.encoder(original)
        result = self.dpt.decoder(raw)
        assert result == datetime.time(9, 15, 45)


# ---------------------------------------------------------------------------
# DPT 11.x — Date
# ---------------------------------------------------------------------------


class TestDPT11Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT11.001")

    def test_decode_returns_date_object(self):
        # Day=15, Month=6, Year=25 → 2025-06-15
        result = self.dpt.decoder(bytes([15, 6, 25]))
        assert result == datetime.date(2025, 6, 15)

    def test_decode_year_90s_maps_to_1990s(self):
        result = self.dpt.decoder(bytes([1, 1, 90]))
        assert result == datetime.date(1990, 1, 1)

    def test_decode_invalid_raises(self):
        with pytest.raises(ValueError):
            self.dpt.decoder(bytes([0, 0, 0]))

    def test_decode_short_payload_raises(self):
        with pytest.raises(ValueError):
            self.dpt.decoder(bytes([1, 1]))

    def test_encode_from_date_object(self):
        d = datetime.date(2025, 3, 15)
        raw = self.dpt.encoder(d)
        assert raw[0] == 15  # day
        assert raw[1] == 3  # month
        assert raw[2] == 25  # year % 100

    def test_encode_from_iso_string(self):
        raw = self.dpt.encoder("2025-12-31")
        assert raw[0] == 31
        assert raw[1] == 12
        assert raw[2] == 25

    def test_encode_from_timestamp(self):
        raw = self.dpt.encoder(0.0)  # Unix epoch → 1970-01-01
        assert len(raw) == 3
        assert raw[1] == 1  # January

    def test_encode_invalid_fallback(self):
        raw = self.dpt.encoder(None)
        # Falls through to datetime.date.today() → 3 bytes
        assert len(raw) == 3

    def test_round_trip(self):
        original = "2024-07-04"
        raw = self.dpt.encoder(original)
        result = self.dpt.decoder(raw)
        assert result == datetime.date(2024, 7, 4)


# ---------------------------------------------------------------------------
# DPT 12.x — 32-bit unsigned
# ---------------------------------------------------------------------------


class TestDPT12Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT12.001")

    def test_encode_decode_round_trip(self):
        for v in (0, 1, 1000000, 0xFFFFFFFF):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_encode_clamps_above_max(self):
        assert self.dpt.decoder(self.dpt.encoder(0x1_0000_0000)) == 0xFFFFFFFF

    def test_encode_clamps_below_0(self):
        assert self.dpt.decoder(self.dpt.encoder(-1)) == 0


# ---------------------------------------------------------------------------
# DPT 13.x — 32-bit signed
# ---------------------------------------------------------------------------


class TestDPT13Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT13.001")

    def test_encode_decode_round_trip(self):
        for v in (-0x80000000, -1, 0, 1, 0x7FFFFFFF):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_active_energy_wh(self):
        dpt = DPTRegistry.get("DPT13.010")
        assert dpt.decoder(dpt.encoder(12345)) == 12345

    def test_active_energy_kwh(self):
        dpt = DPTRegistry.get("DPT13.013")
        assert dpt.decoder(dpt.encoder(-999)) == -999


# ---------------------------------------------------------------------------
# DPT 14.x — 32-bit IEEE float
# ---------------------------------------------------------------------------


class TestDPT14Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT14.056")  # Power (W)

    def test_encode_decode_round_trip(self):
        for v in (0.0, 1.5, -3.14, 1000.0):
            decoded = self.dpt.decoder(self.dpt.encoder(v))
            assert abs(decoded - v) < 1e-3

    def test_temperature(self):
        dpt = DPTRegistry.get("DPT14.068")
        val = 23.456
        decoded = dpt.decoder(dpt.encoder(val))
        assert abs(decoded - val) < 1e-4

    def test_voltage(self):
        dpt = DPTRegistry.get("DPT14.027")
        assert abs(dpt.decoder(dpt.encoder(230.0)) - 230.0) < 0.001


# ---------------------------------------------------------------------------
# DPT 16.x — 14-byte ASCII string
# ---------------------------------------------------------------------------


class TestDPT16Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT16.000")

    def test_encode_decode_short_string(self):
        assert self.dpt.decoder(self.dpt.encoder("Hello")) == "Hello"

    def test_encode_pads_to_14_bytes(self):
        raw = self.dpt.encoder("Hi")
        assert len(raw) == 14
        assert raw[2:] == b"\x00" * 12

    def test_encode_truncates_at_14(self):
        raw = self.dpt.encoder("A" * 20)
        assert len(raw) == 14

    def test_decode_strips_null_terminator(self):
        result = self.dpt.decoder(b"Test\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
        assert result == "Test"

    def test_iso_8859_1_codec(self):
        dpt = DPTRegistry.get("DPT16.001")
        result = dpt.decoder(dpt.encoder("Test"))
        assert result == "Test"

    def test_empty_string(self):
        result = self.dpt.decoder(self.dpt.encoder(""))
        assert result == ""


# ---------------------------------------------------------------------------
# DPT 17.x — Scene Number
# ---------------------------------------------------------------------------


class TestDPT17Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT17.001")

    def test_encode_decode_round_trip(self):
        for v in (0, 1, 32, 63):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_clamps_above_63(self):
        assert self.dpt.decoder(self.dpt.encoder(64)) == 63

    def test_clamps_below_0(self):
        assert self.dpt.decoder(self.dpt.encoder(-1)) == 0


# ---------------------------------------------------------------------------
# DPT 18.x — Scene Control
# ---------------------------------------------------------------------------


class TestDPT18Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT18.001")

    def test_activate_scene_0(self):
        raw = self.dpt.encoder(0)
        assert raw == bytes([0x00])
        assert self.dpt.decoder(raw) == 0

    def test_activate_scene_63(self):
        raw = self.dpt.encoder(63)
        assert raw == bytes([0x3F])
        assert self.dpt.decoder(raw) == 63

    def test_learn_mode_scene_0(self):
        # -1 → learn scene 0 → byte 0x80
        raw = self.dpt.encoder(-1)
        assert raw == bytes([0x80])
        assert self.dpt.decoder(raw) == -1

    def test_learn_mode_scene_5(self):
        # -6 → learn scene 5 → byte 0x80 | 0x05
        raw = self.dpt.encoder(-6)
        assert raw == bytes([0x85])
        assert self.dpt.decoder(raw) == -6

    def test_activate_round_trip(self):
        for v in range(10):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v


# ---------------------------------------------------------------------------
# DPT 19.x — Date and Time
# ---------------------------------------------------------------------------


class TestDPT19Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT19.001")

    def test_encode_from_iso_string(self):
        raw = self.dpt.encoder("2025-06-15T10:30:00")
        assert len(raw) == 8
        assert raw[0] == 125  # 2025-1900
        assert raw[1] == 6
        assert raw[2] == 15
        assert raw[4] == 30
        assert raw[5] == 0

    def test_encode_from_timestamp(self):
        raw = self.dpt.encoder(0.0)  # Unix epoch
        assert len(raw) == 8

    def test_encode_invalid_falls_back(self):
        raw = self.dpt.encoder(None)
        assert len(raw) == 8  # datetime.now() fallback

    def test_decode_returns_iso_string(self):
        # 2025-06-15 10:30:00, DoW in upper 3 bits of byte3
        raw = bytes([125, 6, 15, 0b00001010, 30, 0, 0, 0])
        result = self.dpt.decoder(raw)
        assert "2025" in result
        assert "06-15" in result

    def test_decode_invalid_returns_empty(self):
        # month=0 → invalid date
        raw = bytes([125, 0, 15, 0, 30, 0, 0, 0])
        result = self.dpt.decoder(raw)
        assert result == ""

    def test_round_trip(self):
        original = "2025-01-20T14:00:00"
        raw = self.dpt.encoder(original)
        decoded = self.dpt.decoder(raw)
        assert decoded == original


# ---------------------------------------------------------------------------
# DPT 20.x — 1-Byte Enum/Mode
# ---------------------------------------------------------------------------


class TestDPT20Codecs:
    def test_generic_encode_decode(self):
        dpt = DPTRegistry.get("DPT20.001")  # SCLO Mode
        for v in (0, 1, 128, 255):
            assert dpt.decoder(dpt.encoder(v)) == v

    def test_hvac_mode_encode_decode(self):
        dpt = DPTRegistry.get("DPT20.102")
        assert dpt.decoder(dpt.encoder(0)) == 0
        assert dpt.decoder(dpt.encoder(4)) == 4

    def test_various_mode_dpts(self):
        for dpt_id in ("DPT20.002", "DPT20.100", "DPT20.600", "DPT20.604"):
            dpt = DPTRegistry.get(dpt_id)
            assert dpt.decoder(dpt.encoder(1)) == 1


# ---------------------------------------------------------------------------
# DPT 29.x — 64-bit signed
# ---------------------------------------------------------------------------


class TestDPT29Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT29.010")

    def test_encode_decode_round_trip(self):
        for v in (-9223372036854775808, -1, 0, 1, 9223372036854775807):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_all_29_subtypes(self):
        for dpt_id in ("DPT29.010", "DPT29.011", "DPT29.012"):
            dpt = DPTRegistry.get(dpt_id)
            assert dpt.decoder(dpt.encoder(999999)) == 999999


# ---------------------------------------------------------------------------
# DPT 219.x — AlarmInfo
# ---------------------------------------------------------------------------


class TestDPT219Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT219.001")

    def test_encode_decode_round_trip(self):
        for v in (0, 1, 32767, 65535):
            assert self.dpt.decoder(self.dpt.encoder(v)) == v

    def test_clamps_above_65535(self):
        assert self.dpt.decoder(self.dpt.encoder(70000)) == 65535

    def test_clamps_below_0(self):
        assert self.dpt.decoder(self.dpt.encoder(-1)) == 0


# ---------------------------------------------------------------------------
# DPT 240.x — Combined Position (Shutter & Blinds)
# ---------------------------------------------------------------------------


class TestDPT240Codecs:
    def setup_method(self):
        self.dpt = DPTRegistry.get("DPT240.800")

    def test_decode_both_valid(self):
        raw = bytes([128, 0, 0x03])
        result = self.dpt.decoder(raw)
        assert result["valid_height"] is True
        assert result["valid_slats"] is True
        assert result["height_pct"] is not None
        assert abs(result["height_pct"] - 50.2) < 1.0
        assert result["slats_pct"] == 0.0

    def test_decode_invalid_flags(self):
        raw = bytes([128, 64, 0x00])
        result = self.dpt.decoder(raw)
        assert result["valid_height"] is False
        assert result["valid_slats"] is False
        assert result["height_pct"] is None
        assert result["slats_pct"] is None

    def test_decode_only_height_valid(self):
        raw = bytes([255, 128, 0x01])
        result = self.dpt.decoder(raw)
        assert result["valid_height"] is True
        assert result["valid_slats"] is False

    def test_encode_from_dict(self):
        v = {"height_pct": 50.0, "slats_pct": 25.0, "valid_height": True, "valid_slats": True}
        raw = self.dpt.encoder(v)
        assert len(raw) == 3
        assert raw[2] == 0x03  # both valid

    def test_encode_from_json_string(self):
        import json

        v = json.dumps({"height_pct": 100.0, "slats_pct": 0.0, "valid_height": True, "valid_slats": False})
        raw = self.dpt.encoder(v)
        assert raw[0] == 255  # 100% height
        assert raw[2] == 0x01  # only height valid

    def test_encode_non_dict_encodes_zero(self):
        raw = self.dpt.encoder(42)
        assert raw == bytes([0, 0, 0])

    def test_round_trip_dict(self):
        v = {"height_pct": 75.0, "slats_pct": 50.0, "valid_height": True, "valid_slats": True}
        raw = self.dpt.encoder(v)
        result = self.dpt.decoder(raw)
        assert result["valid_height"] is True
        assert result["valid_slats"] is True
        assert abs(result["height_pct"] - 75.0) < 1.5
        assert abs(result["slats_pct"] - 50.0) < 1.5


# ---------------------------------------------------------------------------
# UNKNOWN fallback
# ---------------------------------------------------------------------------


class TestUnknownDPT:
    def test_encoder_passthrough_bytes(self):
        result = _UNKNOWN_DPT.encoder(b"\xab\xcd")
        assert result == b"\xab\xcd"

    def test_encoder_string_to_bytes(self):
        result = _UNKNOWN_DPT.encoder("hello")
        assert result == b"hello"

    def test_decoder_returns_hex_string(self):
        result = _UNKNOWN_DPT.decoder(b"\xab\xcd")
        assert result == "abcd"


# ---------------------------------------------------------------------------
# Exception fallback paths
# ---------------------------------------------------------------------------


class TestDPT10EncodeExceptionFallback:
    def test_invalid_iso_string_returns_empty_bytes(self):
        dpt = DPTRegistry.get("DPT10.001")
        raw = dpt.encoder("not-a-time")
        assert raw == bytes(3)


class TestDPT11EncodeExceptionFallback:
    def test_invalid_iso_string_returns_empty_bytes(self):
        dpt = DPTRegistry.get("DPT11.001")
        raw = dpt.encoder("not-a-date")
        assert raw == bytes(3)


class TestDPT19EncodeExceptionFallback:
    def test_invalid_iso_string_returns_empty_bytes(self):
        dpt = DPTRegistry.get("DPT19.001")
        raw = dpt.encoder("not-a-datetime")
        assert raw == bytes(8)


# ---------------------------------------------------------------------------
# _dpt20_102_decode / _dpt20_102_encode (defined in module but not registered)
# ---------------------------------------------------------------------------


class TestDPT20102SpecificFunctions:
    def test_decode_clamps_to_byte(self):
        from obs.adapters.knx.dpt_registry import _dpt20_102_decode

        assert _dpt20_102_decode(bytes([0])) == 0
        assert _dpt20_102_decode(bytes([4])) == 4

    def test_encode_clamps_to_valid_range(self):
        from obs.adapters.knx.dpt_registry import _dpt20_102_encode

        assert _dpt20_102_encode(0) == bytes([0])
        assert _dpt20_102_encode(4) == bytes([4])
        assert _dpt20_102_encode(5) == bytes([4])  # clamped to hi=4
        assert _dpt20_102_encode(-1) == bytes([0])  # clamped to lo=0
