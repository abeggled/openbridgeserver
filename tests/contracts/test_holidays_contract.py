"""Contract tests for holidays — verifies the API surface used by
obs.adapters.zeitschaltuhr.adapter._build_holidays().

Production usage:
  hol_obj = hol_lib.country_holidays(country_code, years=[...], subdiv=..., language=...)
  result.update(dict(hol_obj.items()))
  date in hol_obj  # membership check
"""

from __future__ import annotations

from datetime import date

import pytest

holidays = pytest.importorskip("holidays", reason="holidays not installed")

import holidays as hol_lib


class TestCountryHolidaysFunction:
    def test_function_exists(self):
        assert hasattr(hol_lib, "country_holidays"), (
            "holidays.country_holidays() no longer exists. "
            "obs/adapters/zeitschaltuhr/adapter.py calls hol_lib.country_holidays(country, years=[...])."
        )

    def test_returns_dict_like_object(self):
        result = hol_lib.country_holidays("DE", years=[2024])
        assert hasattr(result, "items"), "holidays result must support .items()"
        assert hasattr(result, "__contains__"), "holidays result must support 'date in obj'"

    def test_items_yields_date_string_pairs(self):
        result = hol_lib.country_holidays("DE", years=[2024])
        items = list(result.items())
        assert len(items) > 0, "Germany 2024 should have at least one holiday"
        first_key, first_val = items[0]
        assert isinstance(first_key, date), f"holiday key must be date, got {type(first_key)}"
        assert isinstance(first_val, str), f"holiday value must be str, got {type(first_val)}"

    def test_accepts_years_list(self):
        result = hol_lib.country_holidays("DE", years=[2024, 2025])
        dates = list(result.keys())
        years_found = {d.year for d in dates}
        assert 2024 in years_found
        assert 2025 in years_found

    def test_membership_check(self):
        result = hol_lib.country_holidays("DE", years=[2024])
        # Christmas Day is a public holiday in Germany
        christmas = date(2024, 12, 25)
        assert christmas in result, "Christmas Day should be in German holidays"

    def test_accepts_subdiv_kwarg(self):
        # Bavaria-specific holiday (Epiphany, Jan 6)
        result = hol_lib.country_holidays("DE", subdiv="BY", years=[2024])
        assert hasattr(result, "items")

    def test_dict_conversion_via_update(self):
        # Reproduces: result.update(dict(hol_obj.items()))
        hol_obj = hol_lib.country_holidays("DE", years=[2024])
        combined: dict[date, str] = {}
        combined.update(dict(hol_obj.items()))
        assert len(combined) > 0
