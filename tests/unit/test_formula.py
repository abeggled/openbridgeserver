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
