"""Unit tests for :mod:`obs.regional_format` (issue #1073).

The regional format is an explicit setting, independent of the UI language:
the same German UI must be able to render ``1.234,50`` (Germany) or
``1'234.50`` (Switzerland).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from obs.regional_format import (
    DEFAULT_CURRENCY,
    DEFAULT_REGION_FORMAT,
    SUPPORTED_CURRENCIES,
    SUPPORTED_REGION_FORMATS,
    format_currency,
    format_number,
    resolve_currency,
    resolve_region_format,
    validate_regional_setting,
)

NBSP = " "
NARROW_NBSP = " "


# ---------------------------------------------------------------------------
# resolve_region_format / resolve_currency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("region_format", "language", "expected"),
    [
        ("de-CH", "de", "de-CH"),
        ("de-CH", "en", "de-CH"),  # format is independent of the UI language
        ("auto", "de", "de-DE"),
        ("auto", "gsw", "de-CH"),
        ("auto", "en", "en-US"),
        ("auto", "fr", "fr-FR"),
        ("auto", "it", "it-IT"),
        ("auto", "es", "es-ES"),
        ("auto", "xx", "de-DE"),  # unknown language falls back
        (None, None, "de-DE"),
        ("nl-NL", "en", "en-US"),  # unsupported format falls back to language
    ],
)
def test_resolve_region_format(region_format, language, expected):
    assert resolve_region_format(region_format, language) == expected


@pytest.mark.parametrize(
    ("currency", "region_format", "expected"),
    [
        ("CHF", "de-DE", "CHF"),
        ("auto", "de-CH", "CHF"),
        ("auto", "fr-CH", "CHF"),
        ("auto", "it-CH", "CHF"),
        ("auto", "en-US", "USD"),
        ("auto", "en-GB", "GBP"),
        ("auto", "de-DE", "EUR"),
        (None, "de-DE", "EUR"),
        ("XXX", "de-CH", "CHF"),  # unsupported currency falls back
    ],
)
def test_resolve_currency(currency, region_format, expected):
    assert resolve_currency(currency, region_format) == expected


def test_defaults_are_selectable():
    assert DEFAULT_REGION_FORMAT in SUPPORTED_REGION_FORMATS
    assert DEFAULT_CURRENCY in SUPPORTED_CURRENCIES


# ---------------------------------------------------------------------------
# format_number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("region_format", "expected"),
    [
        ("de-DE", "1,050"),
        ("de-AT", "1,050"),
        ("de-CH", "1.050"),
        ("en-US", "1.050"),
        ("en-GB", "1.050"),
        ("fr-FR", "1,050"),
        ("fr-CH", "1,050"),
        ("it-IT", "1,050"),
        ("it-CH", "1.050"),
        ("es-ES", "1,050"),
    ],
)
def test_format_number_acceptance_case_from_issue(region_format, expected):
    """1.05 with three decimals — the exact example from issue #1073."""
    assert format_number(1.05, region_format, decimals=3) == expected


@pytest.mark.parametrize(
    ("region_format", "expected"),
    [
        ("de-DE", "1.234.567,50"),
        ("de-AT", f"1{NBSP}234{NBSP}567,50"),
        ("de-CH", "1'234'567.50"),
        ("en-US", "1,234,567.50"),
        ("fr-FR", f"1{NARROW_NBSP}234{NARROW_NBSP}567,50"),
        ("fr-CH", "1'234'567,50"),
        ("it-CH", "1'234'567.50"),
    ],
)
def test_format_number_grouping(region_format, expected):
    assert format_number(1234567.5, region_format, decimals=2) == expected


@pytest.mark.parametrize(
    ("region_format", "expected"),
    [
        # minimumGroupingDigits = 1: a separator appears from four digits on.
        ("de-DE", "1.234,5"),
        ("de-AT", f"1{NBSP}234,5"),
        ("de-CH", "1'234.5"),
        ("en-US", "1,234.5"),
        ("en-GB", "1,234.5"),
        ("fr-FR", f"1{NARROW_NBSP}234,5"),
        ("fr-CH", "1'234,5"),
        # minimumGroupingDigits = 2: four digits stay ungrouped.
        ("it-IT", "1234,5"),
        ("it-CH", "1234.5"),
        ("es-ES", "1234,5"),
    ],
)
def test_format_number_honours_minimum_grouping_digits(region_format, expected):
    """Italian and Spanish only group from five digits (CLDR minimumGroupingDigits)."""
    assert format_number(1234.5, region_format) == expected


@pytest.mark.parametrize(
    ("region_format", "expected"),
    [("it-IT", "12.345,5"), ("it-CH", "12'345.5"), ("es-ES", "12.345,5")],
)
def test_format_number_groups_five_digits_in_minimum_two_locales(region_format, expected):
    assert format_number(12345.5, region_format) == expected


@pytest.mark.parametrize("region_format", [code for code in SUPPORTED_REGION_FORMATS if code != "auto"])
def test_format_number_never_groups_three_digits(region_format):
    assert format_number(999, region_format) == "999"


def test_format_number_without_grouping():
    assert format_number(1234567.5, "de-DE", decimals=2, grouping=False) == "1234567,50"


def test_format_number_keeps_natural_precision_when_decimals_is_none():
    assert format_number(1.05, "de-DE") == "1,05"
    assert format_number(42, "de-DE") == "42"
    assert format_number(1000, "de-DE") == "1.000"


def test_format_number_handles_negative_values():
    assert format_number(-1234.5, "de-DE", decimals=1) == "-1.234,5"
    assert format_number(-0.5, "de-CH", decimals=2) == "-0.50"


def test_format_number_accepts_decimal_and_scientific_notation():
    assert format_number(Decimal("1.500"), "de-DE") == "1,500"
    assert format_number(1e-05, "de-DE") == "0,00001"


def test_format_number_rejects_negative_decimals_by_clamping():
    assert format_number(1234.9, "de-DE", decimals=-2) == "1.235"


@pytest.mark.parametrize("value", [9007199254740993, 12345678901234567890, 2**63 - 1])
def test_format_number_keeps_integers_exact_beyond_float_precision(value):
    """Routing an int through float would round anything above 2**53."""
    assert format_number(value, "en-US", grouping=False) == str(value)


def test_format_number_groups_large_integers_exactly():
    assert format_number(9007199254740993, "de-DE") == "9.007.199.254.740.993"


@pytest.mark.parametrize("value", [1e30, 1e21, 1.5e300])
def test_format_number_handles_magnitudes_beyond_the_default_decimal_precision(value):
    """quantize() raises InvalidOperation once the result exceeds the context precision."""
    formatted = format_number(value, "de-DE")

    assert formatted.replace(".", "").isdigit()
    assert formatted.startswith("1")


def test_format_number_keeps_large_magnitudes_grouped_with_fixed_decimals():
    assert format_number(1e30, "de-CH", decimals=2).endswith(".00")
    assert format_number(1e30, "de-CH", decimals=2).count("'") == 10


@pytest.mark.parametrize(("value", "expected"), [(float("inf"), "inf"), (float("-inf"), "-inf"), (float("nan"), "nan")])
def test_format_number_returns_non_finite_values_verbatim(value, expected):
    assert format_number(value, "de-DE") == expected


def test_format_currency_does_not_raise_for_non_finite_values():
    assert format_currency(float("inf"), "de-DE", "EUR") == f"inf{NBSP}€"


def test_format_number_falls_back_to_default_separators_for_unknown_region():
    assert format_number(1234.5, "xx-XX", decimals=1) == "1.234,5"


# ---------------------------------------------------------------------------
# format_currency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("region_format", "currency", "expected"),
    [
        ("de-DE", "EUR", f"1.234,50{NBSP}€"),
        ("de-CH", "CHF", f"CHF{NBSP}1'234.50"),
        ("de-AT", "EUR", f"€{NBSP}1{NBSP}234,50"),
        ("en-US", "USD", "$1,234.50"),
        ("en-GB", "GBP", "£1,234.50"),
        ("fr-FR", "EUR", f"1{NARROW_NBSP}234,50{NBSP}€"),
        ("it-CH", "CHF", f"CHF{NBSP}1234.50"),  # it-CH groups only from five digits
        ("de-DE", "XYZ", f"1.234,50{NBSP}XYZ"),  # unknown code renders verbatim
    ],
)
def test_format_currency(region_format, currency, expected):
    assert format_currency(1234.5, region_format, currency) == expected


def test_format_currency_respects_decimals():
    assert format_currency(1234.5, "de-DE", "EUR", decimals=0) == f"1.235{NBSP}€"


# ---------------------------------------------------------------------------
# validate_regional_setting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "value"), [("region_format", "de-CH"), ("region_format", "auto"), ("currency", "CHF"), ("currency", "auto")])
def test_validate_regional_setting_accepts_supported_values(key, value):
    validate_regional_setting(key, value)


@pytest.mark.parametrize(
    ("key", "value"),
    [("region_format", "nl-NL"), ("region_format", None), ("region_format", ""), ("currency", "BTC"), ("currency", None)],
)
def test_validate_regional_setting_rejects_unsupported_values(key, value):
    with pytest.raises(ValueError):
        validate_regional_setting(key, value)


def test_validate_regional_setting_ignores_unrelated_keys():
    validate_regional_setting("timezone", "Europe/Zurich")
