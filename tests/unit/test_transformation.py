"""Unit tests for obs/core/transformation.py

Covers:
  - apply_value_map: 2-value and N-value maps
  - apply_value_map: float-to-int key normalisation (5.0 → "5")
  - apply_value_map: bool normalisation (True → "true")
  - apply_value_map: passthrough when no map / no match
  - apply_source_type: type coercions (int, float, bool, string, json, xml)
"""

from __future__ import annotations

import pytest

from obs.core.transformation import apply_source_type, apply_value_map

# ===========================================================================
# apply_value_map
# ===========================================================================


class TestApplyValueMapBasic:
    def test_none_map_returns_value_unchanged(self):
        assert apply_value_map(42, None) == 42

    def test_empty_map_returns_value_unchanged(self):
        assert apply_value_map(42, {}) == 42

    def test_no_match_returns_original(self):
        assert apply_value_map(99, {"0": "Aus", "1": "An"}) == 99

    def test_string_key_match(self):
        assert apply_value_map("0", {"0": "Aus", "1": "An"}) == "Aus"

    def test_int_key_match(self):
        assert apply_value_map(1, {"0": "Aus", "1": "An"}) == "An"


class TestApplyValueMapNValues:
    """N-value maps with numeric keys — the primary use case from issue #208."""

    MAP_11 = {
        "0": "Aus",
        "1": "Initialisierung",
        "2": "Isolationsmessung",
        "3": "Netzprüfung",
        "4": "Einschalten",
        "5": "Betrieb",
        "6": "Abschalten",
        "7": "Fehler",
        "8": "Wartung",
        "9": "Update",
        "10": "Standby",
    }

    def test_first_entry(self):
        assert apply_value_map(0, self.MAP_11) == "Aus"

    def test_middle_entry(self):
        assert apply_value_map(5, self.MAP_11) == "Betrieb"

    def test_last_entry(self):
        assert apply_value_map(10, self.MAP_11) == "Standby"

    def test_no_match_returns_original(self):
        assert apply_value_map(11, self.MAP_11) == 11

    def test_all_entries_resolve(self):
        for k, expected in self.MAP_11.items():
            assert apply_value_map(int(k), self.MAP_11) == expected


class TestApplyValueMapFloatNormalisation:
    """Modbus and similar adapters often deliver integer-valued floats (5.0, 10.0).
    The map keys are always strings of integers ("5", "10").
    The lookup must normalise 5.0 → "5" so the map is applied correctly.
    """

    MAP = {"0": "Aus", "5": "Betrieb", "10": "Standby"}

    def test_float_zero_matches(self):
        assert apply_value_map(0.0, self.MAP) == "Aus"

    def test_float_mid_matches(self):
        assert apply_value_map(5.0, self.MAP) == "Betrieb"

    def test_float_upper_matches(self):
        assert apply_value_map(10.0, self.MAP) == "Standby"

    def test_non_integer_float_does_not_strip_decimal(self):
        # 5.5 should NOT match "5"
        assert apply_value_map(5.5, {"5": "Betrieb"}) == 5.5

    def test_large_integer_float(self):
        assert apply_value_map(100.0, {"100": "Max"}) == "Max"


class TestApplyValueMapBoolNormalisation:
    def test_true_matches_lowercase(self):
        assert apply_value_map(True, {"true": "on", "false": "off"}) == "on"

    def test_false_matches_lowercase(self):
        assert apply_value_map(False, {"true": "on", "false": "off"}) == "off"

    def test_bool_true_falls_back_to_numeric_1(self):
        # When "true" is not a key, fall back to "1" (fix for issue #287)
        assert apply_value_map(True, {"1": "on"}) == "on"

    def test_bool_false_falls_back_to_numeric_0(self):
        # When "false" is not a key, fall back to "0" (fix for issue #287)
        assert apply_value_map(False, {"0": "off"}) == "off"

    def test_bool_true_prefers_true_key_over_numeric(self):
        # If both "true" and "1" exist, "true" wins (bool key has priority)
        assert apply_value_map(True, {"true": "bool-on", "1": "num-on"}) == "bool-on"

    def test_bool_no_match_returns_original(self):
        # Neither "true" nor "1" in map → original value returned
        assert apply_value_map(True, {"foo": "bar"}) is True

    def test_numeric_1_matches_1_key(self):
        assert apply_value_map(1, {"1": "on"}) == "on"

    def test_num_invert_preset_with_bool_true(self):
        # KNX DPT1.x decodes to bool; "0↔1 invert" preset must work (issue #287)
        m = {"0": "1", "1": "0"}
        assert apply_value_map(True, m) == "0"

    def test_num_invert_preset_with_bool_false(self):
        m = {"0": "1", "1": "0"}
        assert apply_value_map(False, m) == "1"


class TestApplyValueMapStringValues:
    def test_on_off_preset(self):
        m = {"true": "on", "false": "off"}
        assert apply_value_map(True, m) == "on"
        assert apply_value_map(False, m) == "off"

    def test_inverted_numeric(self):
        m = {"0": "1", "1": "0"}
        assert apply_value_map(0, m) == "1"
        assert apply_value_map(1, m) == "0"


# ===========================================================================
# apply_source_type
# ===========================================================================


def _run(raw, source_data_type=None, json_key=None, xml_path=None):
    try:
        import json

        auto = json.loads(raw)
    except Exception:
        auto = raw
    return apply_source_type(raw, auto, source_data_type, json_key, xml_path)


class TestApplySourceTypeInt:
    def test_int_string(self):
        assert _run("42", "int") == 42

    def test_float_string_coerces_to_int(self):
        assert _run("42.9", "int") == 42

    def test_invalid_int_returns_original(self):
        result = _run("abc", "int")
        assert result == "abc"


class TestApplySourceTypeFloat:
    def test_float_string(self):
        assert _run("3.14", "float") == pytest.approx(3.14)

    def test_int_string_to_float(self):
        assert _run("10", "float") == pytest.approx(10.0)


class TestApplySourceTypeBool:
    def test_true_string(self):
        assert _run("true", "bool") is True

    def test_false_string(self):
        assert _run("false", "bool") is False

    def test_one_string(self):
        assert _run("1", "bool") is True

    def test_zero_value(self):
        assert _run("0", "bool") is False


class TestApplySourceTypeString:
    def test_converts_number_to_str(self):
        assert _run("42", "string") == "42"


class TestApplySourceTypeJson:
    def test_extracts_key(self):
        result = _run('{"temp": 22.5}', "json", json_key="temp")
        assert result == pytest.approx(22.5)

    def test_missing_key_returns_auto(self):
        import json

        raw = '{"temp": 22.5}'
        auto = json.loads(raw)
        result = apply_source_type(raw, auto, "json", "humidity", None)
        assert result == auto  # fallback to full object

    def test_no_key_returns_full_object(self):
        import json

        raw = '{"a": 1, "b": 2}'
        auto = json.loads(raw)
        result = apply_source_type(raw, auto, "json", None, None)
        assert result == {"a": 1, "b": 2}
