"""Seam S1 (#1296): one internal notation for KNX group addresses.

``normalize_ga`` accepts any of the three ETS notations and returns the
internal three-level text; ``format_ga`` renders an address in a project's
style. Every case is checked against the raw 16-bit address it denotes, so
the three notations of one address must collapse onto the same text.
"""

from __future__ import annotations

import pytest

from obs.adapters.knx.group_address import (
    GROUP_ADDRESS_STYLES,
    InvalidGroupAddress,
    format_ga,
    normalize_ga,
    try_normalize_ga,
)

# raw 2282 == 1/0/234 (three-level) == 1/234 (two-level) == 2282 (free)
SAME_ADDRESS = [
    ("ThreeLevel", "1/0/234"),
    ("TwoLevel", "1/234"),
    ("Free", "2282"),
]


@pytest.mark.parametrize(("style", "text"), SAME_ADDRESS)
def test_every_notation_normalizes_to_the_internal_three_level_text(style, text):
    assert normalize_ga(text) == "1/0/234"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("31/7/255", "31/7/255"),  # three-level maximum
        ("31/2047", "31/7/255"),  # two-level maximum
        ("65535", "31/7/255"),  # free maximum
        ("0/0/1", "0/0/1"),
        ("0/1", "0/0/1"),
        ("1", "0/0/1"),
        ("  2/3/4 ", "2/3/4"),  # surrounding whitespace is not part of the address
        ("01/02/003", "1/2/3"),  # leading zeros collapse to one spelling
    ],
)
def test_normalize_covers_ranges_and_spelling_variants(text, expected):
    assert normalize_ga(text) == expected


def test_normalize_is_idempotent():
    assert normalize_ga(normalize_ga("1/234")) == "1/0/234"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "1/2/3/4",
        "a/b/c",
        "1/-/-",  # ETS range placeholder, not an address
        "32/0/0",  # main group > 31
        "1/8/0",  # middle group > 7
        "1/0/256",  # sub group > 255
        "1/2048",  # two-level sub group > 2047
        "65536",  # free > 16 bit
        "-1",
        "1.1.5",  # an individual address, not a group address
    ],
)
def test_normalize_rejects_invalid_input_with_a_clear_error(text):
    with pytest.raises(InvalidGroupAddress, match="Gruppenadresse"):
        normalize_ga(text)


@pytest.mark.parametrize("value", [None, 2282, 1.5])
def test_normalize_rejects_non_text(value):
    with pytest.raises(InvalidGroupAddress):
        normalize_ga(value)


def test_invalid_group_address_is_a_value_error():
    """Pydantic validators turn ValueError into a 422, so the subclass relation is load-bearing."""
    assert issubclass(InvalidGroupAddress, ValueError)


@pytest.mark.parametrize(("value", "expected"), [("1/234", "1/0/234"), ("x", None), (None, None), ("", None)])
def test_try_normalize_returns_none_instead_of_raising(value, expected):
    assert try_normalize_ga(value) == expected


@pytest.mark.parametrize(("style", "text"), SAME_ADDRESS)
def test_format_renders_the_project_style(style, text):
    assert format_ga("1/0/234", style) == text


@pytest.mark.parametrize(("style", "text"), SAME_ADDRESS)
def test_format_round_trips_through_normalize(style, text):
    for raw in (1, 255, 256, 2047, 2048, 2282, 65535):
        internal = normalize_ga(str(raw))
        assert normalize_ga(format_ga(internal, style)) == internal


def test_format_rejects_unknown_style():
    with pytest.raises(ValueError, match="Gruppenadressstil"):
        format_ga("1/0/234", "FourLevel")


def test_format_rejects_invalid_address():
    with pytest.raises(InvalidGroupAddress):
        format_ga("1/8/0", "ThreeLevel")


def test_styles_match_the_xknxproject_vocabulary():
    from xknxproject.models import GroupAddressStyle

    assert set(GROUP_ADDRESS_STYLES) == {style.value for style in GroupAddressStyle}
