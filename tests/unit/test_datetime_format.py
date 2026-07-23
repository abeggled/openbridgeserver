from datetime import UTC, datetime

import pytest

from obs.datetime_format import format_datetime, validate_datetime_setting


def test_format_datetime_supports_all_documented_tokens():
    value = datetime(2026, 6, 8, 2, 4, 5, tzinfo=UTC)

    assert format_datetime(value, "H HH m mm s ss d dd EE EEE EEEE M MM MMM MMMM yy yyyy") == (
        "2 02 4 04 5 05 8 08 Mo Mo. Montag 6 06 Juni Juni 26 2026"
    )


def test_format_datetime_uses_selected_language():
    value = datetime(2026, 6, 8, tzinfo=UTC)

    assert format_datetime(value, "EE EEE EEEE MMM MMMM", "en") == "Mo Mon Monday Jun June"


def test_format_datetime_uses_swiss_german_names():
    value = datetime(2026, 6, 8, tzinfo=UTC)

    assert format_datetime(value, "EE EEE EEEE", "gsw") == "Mä Mä. Mäntig"


def test_format_datetime_preserves_literal_text():
    assert format_datetime(datetime(2026, 1, 2, tzinfo=UTC), "dd.MM.yyyy / x") == "02.01.2026 / x"


def test_format_datetime_does_not_replace_tokens_inside_literal_words():
    value = datetime(2026, 7, 21, 2, 4, 0, tzinfo=UTC)

    assert format_datetime(value, "EEE, dd. MMMM yyyy guguseli", "de") == "Di., 21. Juli 2026 guguseli"


def test_format_datetime_supports_adjacent_tokens():
    value = datetime(2026, 7, 21, 2, 4, 5, tzinfo=UTC)

    assert format_datetime(value, "yyyyMMdd-HHmmss", "de") == "20260721-020405"


def test_format_datetime_preserves_literal_token_separators():
    value = datetime(2026, 7, 21, 9, 8, 7, tzinfo=UTC)

    assert format_datetime(value, "yyyy-MM-ddTHH:mm:ss", "de") == "2026-07-21T09:08:07"
    assert format_datetime(value, "HHhmm", "de") == "09h08"


@pytest.mark.parametrize(
    ("key", "value"),
    [("timezone", "Invalid/Timezone"), ("date_format", ""), ("time_format", None), ("language", "pt")],
)
def test_validate_datetime_setting_rejects_invalid_values(key, value):
    with pytest.raises(ValueError):
        validate_datetime_setting(key, value)
