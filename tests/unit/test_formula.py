from __future__ import annotations

import pytest

from obs.core.formula import apply_formula, validate_formula


def test_validate_formula_allows_math_constants() -> None:
    assert validate_formula("math.pi * x + math.e") is None


def test_validate_formula_allows_direct_math_constants() -> None:
    assert validate_formula("pi * x + e") is None


def test_apply_formula_with_math_constants() -> None:
    result = apply_formula("math.pi * x", 2)
    assert result == pytest.approx(2 * 3.141592653589793)


def test_apply_formula_rejects_attribute_escape() -> None:
    payload = "abs.__self__.__dict__.get('__import__')('math').pi + x"
    assert apply_formula(payload, 2) == 2


def test_apply_formula_int_modulo_correct_for_large_counter64() -> None:
    # Without int preservation, float(2^53+1) = 2^53 (even), so x%2 = 0 (wrong).
    # With x kept as int, 2^53+1 is odd and x%2 = 1 (correct).
    large = 2**53 + 1  # odd, not exactly representable as float64
    result = apply_formula("x % 2", large)
    assert result == 1


def test_apply_formula_int_floor_division_precise_for_large_counter64() -> None:
    # Integer floor division (x // n) gives exact int results — recommended for
    # Counter64 formulas because the result changes whenever the raw counter changes,
    # regardless of how large the counter is.
    large = 2**60
    r1 = apply_formula("x // 1_000_000_000", large)
    r2 = apply_formula("x // 1_000_000_000", large + 1_000_000_000)
    assert r1 != r2


def test_apply_formula_float_divisor_inherent_float64_limit() -> None:
    # At 2^60, adjacent Counter64 values collapse after float division because
    # the result ULP (~10^-7) exceeds the per-counter-increment (~10^-9).
    # This is an inherent float64 limit, not a bug: use x//divisor to avoid it.
    large = 2**60
    r1 = apply_formula("x / 1_000_000_000.0", large)
    r2 = apply_formula("x / 1_000_000_000.0", large + 8)
    assert r1 == r2  # inherently indistinguishable at this scale


def test_apply_formula_int_result_stays_int_for_pure_int_arithmetic() -> None:
    # x + 1 on an int input should return int (Python eval keeps it int)
    result = apply_formula("x + 1", 42)
    assert result == 43
    assert isinstance(result, int)


def test_apply_formula_bool_input_converted_to_float() -> None:
    # bool is a subclass of int; treat as 0.0/1.0, not as integer path
    assert apply_formula("x * 2", True) == pytest.approx(2.0)
    assert apply_formula("x * 2", False) == pytest.approx(0.0)
