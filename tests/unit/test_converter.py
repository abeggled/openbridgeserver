"""
Unit tests for obs/core/converter.py

Covers:
  - ConversionResult dataclass
  - All entries in _CONVERTERS matrix
  - Boundary and edge cases (None-like inputs, extreme values)
  - Generic fallback path
  - can_convert() / conversion_has_loss() helpers
"""
from __future__ import annotations

import pytest

from obs.core.converter import (
    ConversionResult,
    can_convert,
    conversion_has_loss,
    convert,
)


# ===========================================================================
# Helpers
# ===========================================================================

def assert_ok(result: ConversionResult, expected_value, expected_type=None):
    assert not result.loss, f"Unexpected loss: {result.loss_description}"
    assert result.value == expected_value
    if expected_type is not None:
        assert isinstance(result.value, expected_type)


def assert_lossy(result: ConversionResult, expected_value=None):
    assert result.loss, "Expected loss=True but got False"
    assert result.loss_description, "Loss should have a description"
    if expected_value is not None:
        assert result.value == expected_value


# ===========================================================================
# ConversionResult dataclass
# ===========================================================================

class TestConversionResult:
    def test_defaults(self):
        r = ConversionResult(value=42)
        assert r.loss is False
        assert r.loss_description == ""

    def test_with_loss(self):
        r = ConversionResult(value=0, loss=True, loss_description="truncated")
        assert r.loss is True
        assert "truncated" in r.loss_description


# ===========================================================================
# FLOAT → *
# ===========================================================================

class TestFloatToInteger:
    def test_whole_number_no_loss(self):
        assert_ok(convert(21.0, "FLOAT", "INTEGER"), 21, int)

    def test_negative_whole_number_no_loss(self):
        assert_ok(convert(-5.0, "FLOAT", "INTEGER"), -5, int)

    def test_zero_no_loss(self):
        assert_ok(convert(0.0, "FLOAT", "INTEGER"), 0, int)

    def test_fractional_is_lossy(self):
        r = convert(21.4, "FLOAT", "INTEGER")
        assert_lossy(r, 21)

    def test_negative_fractional_is_lossy(self):
        r = convert(-3.9, "FLOAT", "INTEGER")
        assert_lossy(r, -3)  # int() truncates toward zero

    def test_large_value(self):
        r = convert(1_000_000.0, "FLOAT", "INTEGER")
        assert_ok(r, 1_000_000, int)

    def test_string_float_input_coerced(self):
        # converter accepts Any — must not raise
        r = convert("7.0", "FLOAT", "INTEGER")
        assert r.value == 7


class TestFloatToBoolean:
    def test_zero_is_false_no_loss(self):
        assert_ok(convert(0.0, "FLOAT", "BOOLEAN"), False, bool)

    def test_one_is_true_no_loss(self):
        assert_ok(convert(1.0, "FLOAT", "BOOLEAN"), True, bool)

    def test_non_binary_positive_is_lossy(self):
        r = convert(0.5, "FLOAT", "BOOLEAN")
        assert_lossy(r)
        assert r.value is True   # bool(0.5) == True

    def test_non_binary_negative_is_lossy(self):
        r = convert(-1.0, "FLOAT", "BOOLEAN")
        assert_lossy(r)
        assert r.value is True   # bool(-1.0) == True

    def test_large_float_is_lossy(self):
        r = convert(99.9, "FLOAT", "BOOLEAN")
        assert_lossy(r)


class TestFloatToString:
    def test_always_lossy(self):
        r = convert(3.14, "FLOAT", "STRING")
        assert_lossy(r)
        assert "3.14" in r.value

    def test_zero_string(self):
        r = convert(0.0, "FLOAT", "STRING")
        assert r.loss is True
        assert "0" in r.value


# ===========================================================================
# INTEGER → *
# ===========================================================================

class TestIntegerToFloat:
    def test_positive(self):
        assert_ok(convert(42, "INTEGER", "FLOAT"), 42.0, float)

    def test_negative(self):
        assert_ok(convert(-7, "INTEGER", "FLOAT"), -7.0, float)

    def test_zero(self):
        assert_ok(convert(0, "INTEGER", "FLOAT"), 0.0, float)

    def test_no_loss(self):
        r = convert(5, "INTEGER", "FLOAT")
        assert r.loss is False


class TestIntegerToBoolean:
    def test_zero_is_false_no_loss(self):
        assert_ok(convert(0, "INTEGER", "BOOLEAN"), False, bool)

    def test_one_is_true_no_loss(self):
        assert_ok(convert(1, "INTEGER", "BOOLEAN"), True, bool)

    def test_two_is_lossy(self):
        r = convert(2, "INTEGER", "BOOLEAN")
        assert_lossy(r)
        assert r.value is True

    def test_negative_is_lossy(self):
        r = convert(-1, "INTEGER", "BOOLEAN")
        assert_lossy(r)


class TestIntegerToString:
    def test_always_lossy(self):
        r = convert(123, "INTEGER", "STRING")
        assert_lossy(r, "123")


# ===========================================================================
# BOOLEAN → *
# ===========================================================================

class TestBooleanToInteger:
    def test_true_to_one(self):
        assert_ok(convert(True, "BOOLEAN", "INTEGER"), 1, int)

    def test_false_to_zero(self):
        assert_ok(convert(False, "BOOLEAN", "INTEGER"), 0, int)

    def test_no_loss(self):
        assert convert(True, "BOOLEAN", "INTEGER").loss is False


class TestBooleanToFloat:
    def test_true_to_one(self):
        assert_ok(convert(True, "BOOLEAN", "FLOAT"), 1.0, float)

    def test_false_to_zero(self):
        assert_ok(convert(False, "BOOLEAN", "FLOAT"), 0.0, float)

    def test_no_loss(self):
        assert convert(False, "BOOLEAN", "FLOAT").loss is False


class TestBooleanToString:
    def test_always_lossy(self):
        r = convert(True, "BOOLEAN", "STRING")
        assert r.loss is True


# ===========================================================================
# STRING → *
# ===========================================================================

class TestStringToFloat:
    def test_valid_number(self):
        r = convert("3.14", "STRING", "FLOAT")
        assert r.value == pytest.approx(3.14)
        assert r.loss is True   # STRING → anything is always lossy

    def test_integer_string(self):
        r = convert("42", "STRING", "FLOAT")
        assert r.value == 42.0

    def test_invalid_string_defaults_to_zero(self):
        r = convert("banana", "STRING", "FLOAT")
        assert r.value == 0.0
        assert r.loss is True

    def test_empty_string_defaults_to_zero(self):
        r = convert("", "STRING", "FLOAT")
        assert r.value == 0.0
        assert r.loss is True


class TestStringToInteger:
    def test_valid_integer_string(self):
        r = convert("99", "STRING", "INTEGER")
        assert r.value == 99
        assert r.loss is True

    def test_invalid_defaults_to_zero(self):
        r = convert("nope", "STRING", "INTEGER")
        assert r.value == 0
        assert r.loss is True


class TestStringToBoolean:
    @pytest.mark.parametrize("s, expected", [
        ("true", True), ("True", True), ("TRUE", True),
        ("1", True), ("yes", True), ("on", True),
        ("false", False), ("False", False), ("FALSE", False),
        ("0", False), ("no", False), ("off", False),
    ])
    def test_recognized_strings(self, s, expected):
        r = convert(s, "STRING", "BOOLEAN")
        assert r.value is expected
        assert r.loss is True   # always lossy for STRING source

    def test_ambiguous_string_is_lossy(self):
        r = convert("maybe", "STRING", "BOOLEAN")
        assert r.loss is True
        assert isinstance(r.value, bool)


# ===========================================================================
# Same-type → no conversion
# ===========================================================================

class TestSameType:
    @pytest.mark.parametrize("value, dtype", [
        (3.14, "FLOAT"),
        (42, "INTEGER"),
        (True, "BOOLEAN"),
        ("hello", "STRING"),
    ])
    def test_same_type_returns_original(self, value, dtype):
        r = convert(value, dtype, dtype)
        assert r.value == value
        assert r.loss is False
        assert r.loss_description == ""


# ===========================================================================
# Generic fallback (no direct converter)
# ===========================================================================

class TestFallback:
    def test_unknown_to_float_uses_string_fallback(self):
        r = convert(b"\x01\x02", "UNKNOWN", "FLOAT")
        assert r.loss is True

    def test_float_to_date_uses_string_fallback(self):
        r = convert(1.0, "FLOAT", "DATE")
        assert r.loss is True
        assert r.value == str(float(1.0))

    def test_fallback_never_raises(self):
        # Should never raise regardless of inputs
        r = convert(object(), "UNKNOWN", "DATETIME")
        assert r.loss is True


# ===========================================================================
# can_convert()
# ===========================================================================

class TestCanConvert:
    @pytest.mark.parametrize("f, t", [
        ("FLOAT",   "INTEGER"),
        ("FLOAT",   "BOOLEAN"),
        ("FLOAT",   "STRING"),
        ("INTEGER", "FLOAT"),
        ("INTEGER", "BOOLEAN"),
        ("INTEGER", "STRING"),
        ("BOOLEAN", "INTEGER"),
        ("BOOLEAN", "FLOAT"),
        ("BOOLEAN", "STRING"),
        ("STRING",  "FLOAT"),
        ("STRING",  "INTEGER"),
        ("STRING",  "BOOLEAN"),
    ])
    def test_known_conversions(self, f, t):
        assert can_convert(f, t) is True

    def test_same_type_always_true(self):
        for t in ("FLOAT", "INTEGER", "BOOLEAN", "STRING", "UNKNOWN"):
            assert can_convert(t, t) is True

    def test_no_direct_converter_returns_false(self):
        assert can_convert("FLOAT", "DATE") is False
        assert can_convert("UNKNOWN", "FLOAT") is False


# ===========================================================================
# conversion_has_loss()
# ===========================================================================

class TestConversionHasLoss:
    def test_same_type_no_loss(self):
        for t in ("FLOAT", "INTEGER", "BOOLEAN", "STRING"):
            assert conversion_has_loss(t, t) is False

    def test_lossless_known_conversions(self):
        # integer → float is always lossless with sample value 1
        assert conversion_has_loss("INTEGER", "FLOAT") is False
        assert conversion_has_loss("BOOLEAN", "INTEGER") is False
        assert conversion_has_loss("BOOLEAN", "FLOAT") is False

    def test_lossy_conversions(self):
        # STRING → anything is always lossy
        assert conversion_has_loss("STRING", "FLOAT") is True
        assert conversion_has_loss("STRING", "INTEGER") is True
        assert conversion_has_loss("STRING", "BOOLEAN") is True
        # FLOAT → STRING always lossy
        assert conversion_has_loss("FLOAT", "STRING") is True
