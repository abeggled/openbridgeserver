"""Application-wide regional formatting (numbers, currency).

The regional format is a *separate* setting from the UI language: language and
formatting convention are two independent dimensions.  A German-speaking user in
Switzerland expects ``1'234.50`` while one in Germany expects ``1.234,50`` — the
UI language is identical in both cases.

The separator table is taken from ``Intl.NumberFormat`` (CLDR via ICU 78) so
backend-rendered text — MESSAGE templates are the only such surface — reads the
same as the two frontends. CLDR does revise separators between releases, and a
browser on an older or data-reduced ICU build can therefore differ for
individual locales; the table is a snapshot, not a guarantee of byte-identical
output on every client.

Only *display* text is affected — datapoint values, calculations, API payloads
and stored history stay locale-neutral numbers.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, localcontext

DEFAULT_REGION_FORMAT = "auto"
DEFAULT_CURRENCY = "auto"

# Curated list of selectable regional formats. "auto" derives the format from
# the configured UI language.
SUPPORTED_REGION_FORMATS: tuple[str, ...] = (
    "auto",
    "de-DE",
    "de-AT",
    "de-CH",
    "en-US",
    "en-GB",
    "fr-FR",
    "fr-CH",
    "it-IT",
    "it-CH",
    "es-ES",
)

# "auto" derives the currency from the resolved regional format.
SUPPORTED_CURRENCIES: tuple[str, ...] = ("auto", "EUR", "CHF", "USD", "GBP")

REGIONAL_SETTING_KEYS = {"region_format", "currency"}

# Fallback used when the regional format is set to "auto".
_LANGUAGE_REGION: dict[str, str] = {
    "de": "de-DE",
    "gsw": "de-CH",
    "en": "en-US",
    "fr": "fr-FR",
    "it": "it-IT",
    "es": "es-ES",
}

_REGION_CURRENCY: dict[str, str] = {
    "de-CH": "CHF",
    "fr-CH": "CHF",
    "it-CH": "CHF",
    "en-US": "USD",
    "en-GB": "GBP",
}

_NBSP = " "
_NARROW_NBSP = " "

# (decimal separator, group separator) per regional format — values taken from
# ``Intl.NumberFormat(locale, ...).formatToParts(1234567.5)``.
_SEPARATORS: dict[str, tuple[str, str]] = {
    "de-DE": (",", "."),
    "de-AT": (",", _NBSP),
    "de-CH": (".", "'"),
    "en-US": (".", ","),
    "en-GB": (".", ","),
    "fr-FR": (",", _NARROW_NBSP),
    "fr-CH": (",", "'"),
    "it-IT": (",", "."),
    "it-CH": (".", "'"),
    "es-ES": (",", "."),
}

# CLDR ``minimumGroupingDigits``: how many digits must precede the first group
# before a separator is inserted at all. Most locales use 1 (group from four
# digits), but Italian and Spanish use 2 — ``1234,5`` stays ungrouped there and
# only ``12.345,5`` gets a separator.
_MIN_GROUPING_DIGITS: dict[str, int] = {"it-IT": 2, "it-CH": 2, "es-ES": 2}
_DEFAULT_MIN_GROUPING_DIGITS = 1

# Regional formats that write the currency symbol/code before the amount.
_CURRENCY_PREFIX: frozenset[str] = frozenset({"de-AT", "de-CH", "en-US", "en-GB", "it-CH"})

_CURRENCY_SYMBOLS: dict[str, str] = {"EUR": "€", "CHF": "CHF", "USD": "$", "GBP": "£"}


def validate_regional_setting(key: str, value: str | None) -> None:
    """Validate one persisted regional formatting setting."""
    if key == "region_format" and value not in SUPPORTED_REGION_FORMATS:
        raise ValueError("Unsupported regional format")
    if key == "currency" and value not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported currency")


def resolve_region_format(region_format: str | None, language: str | None = None) -> str:
    """Return the effective BCP-47 formatting locale.

    ``auto`` (or an unknown value) falls back to the region derived from
    *language*, and finally to ``de-DE``.
    """
    if region_format and region_format != "auto" and region_format in _SEPARATORS:
        return region_format
    return _LANGUAGE_REGION.get(language or "", "de-DE")


def resolve_currency(currency: str | None, region_format: str) -> str:
    """Return the effective ISO-4217 currency for *region_format*."""
    if currency and currency != "auto" and currency in SUPPORTED_CURRENCIES:
        return currency
    return _REGION_CURRENCY.get(region_format, "EUR")


def _to_decimal(value: float | Decimal) -> Decimal:
    """Convert *value* without losing precision.

    Integers are converted exactly — routing them through ``float`` would round
    anything above 2**53 (e.g. a 64-bit counter register) to a different number.
    Floats go through ``str``, which yields their shortest round-tripping form.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


def is_finite_number(value: float | Decimal) -> bool:
    """Whether *value* has a regional representation.

    Integers are always finite and are checked *without* converting to float —
    ``math.isfinite(10**309)`` would raise ``OverflowError`` on a perfectly
    valid Python integer.
    """
    if isinstance(value, Decimal):
        return value.is_finite()
    if isinstance(value, int):
        return True
    return math.isfinite(value)


def _natural_decimals(value: float | Decimal) -> int:
    """Number of decimal places the value carries in its shortest form.

    *value* must be finite — NaN and infinities carry a non-numeric exponent and
    are filtered out by the only caller before it gets here.
    """
    exponent = int(_to_decimal(value).as_tuple().exponent)
    return -exponent if exponent < 0 else 0


def format_number(
    value: float | Decimal,
    region_format: str,
    *,
    decimals: int | None = None,
    grouping: bool = True,
) -> str:
    """Format *value* for display using the separators of *region_format*.

    *decimals* fixes the number of fraction digits; ``None`` keeps the value's
    own precision.  Integers never gain a fraction part unless *decimals* asks
    for one.  Infinities and NaN have no regional representation and are
    returned as their plain Python text.
    """
    if not is_finite_number(value):
        return str(value)
    decimal_sep, group_sep = _SEPARATORS.get(region_format, _SEPARATORS["de-DE"])
    places = _natural_decimals(value) if decimals is None else max(0, decimals)
    number = _to_decimal(value)
    # Round half away from zero so the backend agrees with Intl.NumberFormat in
    # the browser; Python's default float formatting rounds half to even. The
    # context has to hold every digit of the result, otherwise quantize() raises
    # InvalidOperation for magnitudes beyond the default 28-digit precision.
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, number.adjusted() + places + 2)
        quantized = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{quantized:.{places}f}"
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    integer_part, _, fraction_part = text.partition(".")
    min_grouping = _MIN_GROUPING_DIGITS.get(region_format, _DEFAULT_MIN_GROUPING_DIGITS)
    if grouping and len(integer_part) >= 3 + min_grouping:
        digits: list[str] = []
        for index, char in enumerate(reversed(integer_part)):
            if index and index % 3 == 0:
                digits.append(group_sep)
            digits.append(char)
        integer_part = "".join(reversed(digits))
    result = integer_part + (decimal_sep + fraction_part if fraction_part else "")
    return f"-{result}" if negative else result


def format_currency(
    value: float | Decimal,
    region_format: str,
    currency: str,
    *,
    decimals: int = 2,
) -> str:
    """Format *value* as a currency amount for *region_format*."""
    amount = format_number(value, region_format, decimals=decimals)
    symbol = _CURRENCY_SYMBOLS.get(currency, currency)
    if region_format not in _CURRENCY_PREFIX:
        return f"{amount}{_NBSP}{symbol}"
    # English conventions write the symbol tight against the amount.
    separator = "" if region_format.startswith("en-") else _NBSP
    return f"{symbol}{separator}{amount}"
