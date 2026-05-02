"""Unit tests for the Zeitschaltuhr adapter — pure logic functions.

_should_fire, _calculate_target_time, _is_vacation_n, _is_vacation, _is_holiday
are all pure / near-pure methods that can be tested without asyncio.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from obs.adapters.zeitschaltuhr.adapter import (
    HolidayMode,
    TimeRef,
    TimerType,
    ZeitschaltuhrAdapter,
    ZeitschaltuhrBindingConfig,
    ZeitschaltuhrConfig,
    _advent1_date,
    _easter_date,
    _last_weekday_of_month,
    _nth_weekday_of_month,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(**cfg_overrides) -> ZeitschaltuhrAdapter:
    """Create an adapter instance pre-configured with UTC + empty holidays."""
    bus = MagicMock()
    adapter = ZeitschaltuhrAdapter(event_bus=bus, config={})
    adapter._tz = UTC
    adapter._hol = {}  # no holidays by default
    adapter._cfg = ZeitschaltuhrConfig(**cfg_overrides)
    return adapter


def _cfg(**kwargs) -> ZeitschaltuhrBindingConfig:
    """Shorthand for building a binding config with sensible defaults."""
    return ZeitschaltuhrBindingConfig(**kwargs)


def _now(hour: int = 8, minute: int = 0, weekday_offset: int = 0) -> datetime:
    """Return a UTC datetime for a Monday (2026-04-06) + weekday_offset days,
    at hour:minute.  weekday_offset=0 → Monday, 1 → Tuesday, …, 6 → Sunday.
    """
    from datetime import timedelta

    base = datetime(2026, 4, 6, hour, minute, 0, tzinfo=UTC)  # a Monday
    return base + timedelta(days=weekday_offset)


# ---------------------------------------------------------------------------
# _should_fire — basic absolute time
# ---------------------------------------------------------------------------


class TestShouldFireAbsoluteTime:
    def test_fires_at_exact_time(self):
        adapter = _make_adapter()
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=8, minute=0)
        now = _now(hour=8, minute=0)
        assert adapter._should_fire(cfg, now) is True

    def test_does_not_fire_one_minute_early(self):
        adapter = _make_adapter()
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=8, minute=0)
        now = _now(hour=7, minute=59)
        assert adapter._should_fire(cfg, now) is False

    def test_does_not_fire_one_minute_late(self):
        adapter = _make_adapter()
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=8, minute=0)
        now = _now(hour=8, minute=1)
        assert adapter._should_fire(cfg, now) is False

    def test_fires_with_offset(self):
        adapter = _make_adapter()
        # 08:00 + 5 min offset → fires at 08:05
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=8, minute=0, offset_minutes=5)
        assert adapter._should_fire(cfg, _now(8, 5)) is True
        assert adapter._should_fire(cfg, _now(8, 0)) is False


# ---------------------------------------------------------------------------
# _should_fire — weekday filter
# ---------------------------------------------------------------------------


class TestShouldFireWeekdays:
    def test_fires_on_matching_weekday(self):
        adapter = _make_adapter()
        # Monday only
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=8, minute=0, weekdays=[0])
        now = _now(hour=8, minute=0, weekday_offset=0)  # Monday
        assert adapter._should_fire(cfg, now) is True

    def test_does_not_fire_on_non_matching_weekday(self):
        adapter = _make_adapter()
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=8, minute=0, weekdays=[0])  # Mon only
        now = _now(hour=8, minute=0, weekday_offset=1)  # Tuesday
        assert adapter._should_fire(cfg, now) is False

    def test_fires_on_all_weekdays_by_default(self):
        adapter = _make_adapter()
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=10, minute=30)  # default weekdays=[0..6]
        for day_offset in range(7):
            assert adapter._should_fire(cfg, _now(10, 30, day_offset)) is True


# ---------------------------------------------------------------------------
# _should_fire — every_minute / every_hour
# ---------------------------------------------------------------------------


class TestShouldFireCycles:
    def test_every_minute_fires_regardless_of_time(self):
        adapter = _make_adapter()
        cfg = _cfg(every_minute=True)
        for h in (0, 12, 23):
            for m in (0, 30, 59):
                assert adapter._should_fire(cfg, _now(h, m)) is True

    def test_every_hour_fires_at_correct_minute(self):
        adapter = _make_adapter()
        cfg = _cfg(every_hour=True, minute=15)
        assert adapter._should_fire(cfg, _now(8, 15)) is True
        assert adapter._should_fire(cfg, _now(9, 15)) is True
        assert adapter._should_fire(cfg, _now(8, 14)) is False
        assert adapter._should_fire(cfg, _now(8, 16)) is False


# ---------------------------------------------------------------------------
# _should_fire — holiday mode
# ---------------------------------------------------------------------------


class TestShouldFireHolidayMode:
    def _monday_with_holiday(self) -> tuple[ZeitschaltuhrAdapter, datetime]:
        adapter = _make_adapter()
        monday = date(2026, 4, 6)
        adapter._hol = {monday: "Test Holiday"}
        now = _now(hour=8, minute=0, weekday_offset=0)  # that Monday
        return adapter, now

    def test_holiday_skip_prevents_fire(self):
        adapter, now = self._monday_with_holiday()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            holiday_mode=HolidayMode.SKIP,
        )
        assert adapter._should_fire(cfg, now) is False

    def test_holiday_only_fires_on_holiday(self):
        adapter, now = self._monday_with_holiday()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            holiday_mode=HolidayMode.ONLY,
        )
        assert adapter._should_fire(cfg, now) is True

    def test_holiday_only_prevents_fire_on_normal_day(self):
        adapter = _make_adapter()  # no holidays
        now = _now(hour=8, minute=0)  # normal Monday
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            holiday_mode=HolidayMode.ONLY,
        )
        assert adapter._should_fire(cfg, now) is False

    def test_holiday_as_sunday_shifts_weekday_to_6(self):
        adapter, now = self._monday_with_holiday()
        # Monday (0) promoted to Sunday (6) → Sunday-only cfg should fire
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            weekdays=[6],  # only Sunday
            holiday_mode=HolidayMode.AS_SUNDAY,
        )
        assert adapter._should_fire(cfg, now) is True

    def test_holiday_ignore_fires_normally(self):
        adapter, now = self._monday_with_holiday()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            holiday_mode=HolidayMode.IGNORE,
        )
        assert adapter._should_fire(cfg, now) is True


# ---------------------------------------------------------------------------
# _should_fire — vacation mode
# ---------------------------------------------------------------------------


class TestShouldFireVacationMode:
    def _adapter_in_vacation(self) -> tuple[ZeitschaltuhrAdapter, datetime]:
        adapter = _make_adapter(
            vacation_1_start="2026-04-01",
            vacation_1_end="2026-04-10",
        )
        now = _now(hour=8, minute=0)  # 2026-04-06 = in vacation window
        return adapter, now

    def test_vacation_skip_prevents_fire(self):
        adapter, now = self._adapter_in_vacation()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            vacation_mode=HolidayMode.SKIP,
        )
        assert adapter._should_fire(cfg, now) is False

    def test_vacation_only_fires_during_vacation(self):
        adapter, now = self._adapter_in_vacation()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            vacation_mode=HolidayMode.ONLY,
        )
        assert adapter._should_fire(cfg, now) is True


# ---------------------------------------------------------------------------
# _should_fire — annual timer
# ---------------------------------------------------------------------------


class TestShouldFireAnnual:
    def test_annual_fires_in_matching_month(self):
        adapter = _make_adapter()
        cfg = _cfg(
            timer_type=TimerType.ANNUAL,
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            months=[4],  # April
        )
        # _now uses April 6, 2026 (Monday)
        assert adapter._should_fire(cfg, _now(8, 0)) is True

    def test_annual_does_not_fire_in_wrong_month(self):
        adapter = _make_adapter()
        cfg = _cfg(
            timer_type=TimerType.ANNUAL,
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            months=[5],  # May only
        )
        assert adapter._should_fire(cfg, _now(8, 0)) is False  # April

    def test_annual_day_of_month_filter(self):
        adapter = _make_adapter()
        cfg = _cfg(
            timer_type=TimerType.ANNUAL,
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            months=[4],
            day_of_month=6,  # April 6
        )
        assert adapter._should_fire(cfg, _now(8, 0)) is True  # April 6

        cfg2 = _cfg(
            timer_type=TimerType.ANNUAL,
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            months=[4],
            day_of_month=7,  # April 7 — should NOT fire on April 6
        )
        assert adapter._should_fire(cfg2, _now(8, 0)) is False


# ---------------------------------------------------------------------------
# _calculate_target_time — absolute
# ---------------------------------------------------------------------------


class TestCalculateTargetTime:
    def test_absolute_no_offset(self):
        adapter = _make_adapter()
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=9, minute=30)
        result = adapter._calculate_target_time(cfg, date(2026, 4, 6))
        assert result is not None
        assert result.hour == 9
        assert result.minute == 30

    def test_absolute_with_positive_offset(self):
        adapter = _make_adapter()
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=9, minute=0, offset_minutes=30)
        result = adapter._calculate_target_time(cfg, date(2026, 4, 6))
        assert result.hour == 9
        assert result.minute == 30

    def test_absolute_with_negative_offset(self):
        adapter = _make_adapter()
        cfg = _cfg(time_ref=TimeRef.ABSOLUTE, hour=9, minute=0, offset_minutes=-10)
        result = adapter._calculate_target_time(cfg, date(2026, 4, 6))
        assert result.hour == 8
        assert result.minute == 50


# ---------------------------------------------------------------------------
# _is_vacation_n / _is_vacation
# ---------------------------------------------------------------------------


class TestIsVacation:
    def _adapter_with_vacation(self) -> ZeitschaltuhrAdapter:
        return _make_adapter(
            vacation_1_start="2026-07-01",
            vacation_1_end="2026-07-20",
            vacation_2_start="2026-12-24",
            vacation_2_end="2026-12-31",
        )

    def test_in_vacation_1(self):
        adapter = self._adapter_with_vacation()
        assert adapter._is_vacation_n(date(2026, 7, 10), 1) is True

    def test_at_start_boundary(self):
        adapter = self._adapter_with_vacation()
        assert adapter._is_vacation_n(date(2026, 7, 1), 1) is True

    def test_at_end_boundary(self):
        adapter = self._adapter_with_vacation()
        assert adapter._is_vacation_n(date(2026, 7, 20), 1) is True

    def test_one_day_before_vacation(self):
        adapter = self._adapter_with_vacation()
        assert adapter._is_vacation_n(date(2026, 6, 30), 1) is False

    def test_one_day_after_vacation(self):
        adapter = self._adapter_with_vacation()
        assert adapter._is_vacation_n(date(2026, 7, 21), 1) is False

    def test_is_vacation_combines_all_periods(self):
        adapter = self._adapter_with_vacation()
        assert adapter._is_vacation(date(2026, 7, 15)) is True
        assert adapter._is_vacation(date(2026, 12, 25)) is True
        assert adapter._is_vacation(date(2026, 8, 1)) is False

    def test_empty_vacation_period_returns_false(self):
        adapter = _make_adapter()  # no vacation configured
        assert adapter._is_vacation_n(date(2026, 7, 10), 1) is False


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


class TestAdvent1Date:
    def test_advent1_2025(self):
        # Dec 25, 2025 = Thursday → 4. Advent = Dec 21 → 1. Advent = Nov 30
        assert _advent1_date(2025) == date(2025, 11, 30)

    def test_advent1_2024(self):
        # Dec 25, 2024 = Wednesday → 4. Advent = Dec 22 → 1. Advent = Dec 1
        assert _advent1_date(2024) == date(2024, 12, 1)

    def test_advent1_2023(self):
        # Dec 25, 2023 = Monday → 4. Advent = Dec 24 → 1. Advent = Dec 3
        assert _advent1_date(2023) == date(2023, 12, 3)

    def test_advent1_2022(self):
        # Dec 25, 2022 = Sunday → 4. Advent = Dec 18 → 1. Advent = Nov 27
        assert _advent1_date(2022) == date(2022, 11, 27)

    def test_advent1_is_sunday(self):
        for year in range(2020, 2031):
            assert _advent1_date(year).weekday() == 6, f"1. Advent {year} is not a Sunday"

    def test_advent1_range(self):
        # 1. Advent always falls between Nov 27 and Dec 3
        for year in range(2020, 2031):
            d = _advent1_date(year)
            assert (d.month == 11 and d.day >= 27) or (d.month == 12 and d.day <= 3), f"1. Advent {year} = {d} out of expected range"


class TestEasterDate:
    def test_easter_2026(self):
        # Easter Sunday 2026 is April 5
        assert _easter_date(2026) == date(2026, 4, 5)

    def test_easter_2024(self):
        # Easter Sunday 2024 is March 31
        assert _easter_date(2024) == date(2024, 3, 31)

    def test_easter_2025(self):
        # Easter Sunday 2025 is April 20
        assert _easter_date(2025) == date(2025, 4, 20)


class TestLastWeekdayOfMonth:
    def test_last_sunday_of_november_2026(self):
        # Last Sunday of November 2026 = Nov 29
        result = _last_weekday_of_month(2026, 11, 6)
        assert result == date(2026, 11, 29)
        assert result.weekday() == 6

    def test_last_monday_of_december_2026(self):
        result = _last_weekday_of_month(2026, 12, 0)
        assert result.weekday() == 0
        assert result.month == 12
        assert result.year == 2026


class TestNthWeekdayOfMonth:
    def test_first_sunday_of_november_2026(self):
        result = _nth_weekday_of_month(2026, 11, 6, 1)
        assert result is not None
        assert result.weekday() == 6
        assert result.month == 11

    def test_second_sunday_of_october_2026(self):
        result = _nth_weekday_of_month(2026, 10, 6, 2)
        assert result is not None
        assert result.weekday() == 6
        assert result.month == 10

    def test_fifth_weekday_out_of_range_returns_none(self):
        # February 2026 has 28 days — no month can have a 5th occurrence of every weekday.
        # Feb 2026 starts on Sunday; 5th Sunday would be day 29 which doesn't exist.
        result = _nth_weekday_of_month(2026, 2, 6, 5)  # 5th Sunday of Feb 2026
        assert result is None


# ---------------------------------------------------------------------------
# _parse_custom_holiday_entry
# ---------------------------------------------------------------------------


class TestParseCustomHolidayEntry:
    def _adapter(self) -> ZeitschaltuhrAdapter:
        return _make_adapter()

    def test_fixed_date_no_name(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("12-26", 2026)
        assert result == {date(2026, 12, 26): "Benutzerdefinierter Feiertag"}

    def test_fixed_date_with_name(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("12-26:Stephanstag", 2026)
        assert result == {date(2026, 12, 26): "Stephanstag"}

    def test_easter_plus_one(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("easter+1:Ostermontag", 2026)
        # Easter 2026 = April 5 → +1 = April 6
        assert result == {date(2026, 4, 6): "Ostermontag"}

    def test_easter_minus_47_rosenmontag(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("easter-47:Rosenmontag", 2026)
        from datetime import timedelta

        expected = _easter_date(2026) - timedelta(days=47)
        assert result == {expected: "Rosenmontag"}

    def test_easter_zero_offset(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("easter:Ostersonntag", 2026)
        assert result == {_easter_date(2026): "Ostersonntag"}

    def test_last_weekday_german_abbreviation(self):
        adapter = self._adapter()
        # Last Sunday of November
        result = adapter._parse_custom_holiday_entry("last_SO_NOV:Buß- und Bettag", 2026)
        expected_date = _last_weekday_of_month(2026, 11, 6)
        assert result == {expected_date: "Buß- und Bettag"}

    def test_last_weekday_english_abbreviation(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("last_SUN_NOV", 2026)
        assert date(2026, 11, 29) in result

    def test_nth_weekday(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("2_SO_OKT:Erntedank", 2026)
        expected_date = _nth_weekday_of_month(2026, 10, 6, 2)
        assert result == {expected_date: "Erntedank"}

    def test_advent_zero_offset(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("advent+0:1. Advent", 2025)
        assert result == {_advent1_date(2025): "1. Advent"}

    def test_advent_plus_7_second_advent(self):
        from datetime import timedelta

        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("advent+7:2. Advent", 2025)
        assert result == {_advent1_date(2025) + timedelta(days=7): "2. Advent"}

    def test_advent_plus_24_heiligabend(self):
        from datetime import timedelta

        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("advent+24:Heiligabend", 2025)
        expected = _advent1_date(2025) + timedelta(days=24)
        assert result == {expected: "Heiligabend"}
        # Verify: 1. Advent 2025 = Nov 30, +24 = Dec 24
        assert expected == date(2025, 12, 24)

    def test_advent_no_offset(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("advent:Adventszeit", 2025)
        assert result == {_advent1_date(2025): "Adventszeit"}

    def test_unknown_format_returns_empty(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("invalid_expression", 2026)
        assert result == {}

    def test_unknown_weekday_abbreviation_returns_empty(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("last_XYZ_NOV", 2026)
        assert result == {}


# ---------------------------------------------------------------------------
# _build_holidays — custom holidays integration
# ---------------------------------------------------------------------------


class TestBuildHolidaysCustom:
    def test_custom_fixed_date_appears_in_holidays(self):
        adapter = _make_adapter(custom_holidays=["12-26:Stephanstag"])
        adapter._hol = adapter._build_holidays()
        assert adapter._is_holiday(date(2026, 12, 26)) is True
        assert adapter._holiday_name(date(2026, 12, 26)) == "Stephanstag"

    def test_custom_easter_relative_appears_in_holidays(self):
        adapter = _make_adapter(custom_holidays=["easter+1:Ostermontag"])
        adapter._hol = adapter._build_holidays()
        easter_monday_2026 = date(2026, 4, 6)
        assert adapter._is_holiday(easter_monday_2026) is True

    def test_custom_last_weekday_appears_in_holidays(self):
        adapter = _make_adapter(custom_holidays=["last_SO_NOV:Buss+Bettag"])
        adapter._hol = adapter._build_holidays()
        last_sun_nov = _last_weekday_of_month(2026, 11, 6)
        assert adapter._is_holiday(last_sun_nov) is True

    def test_custom_holiday_triggers_skip_mode(self):
        adapter = _make_adapter(custom_holidays=["12-26:Stephanstag"])
        adapter._hol = adapter._build_holidays()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            holiday_mode=HolidayMode.SKIP,
        )
        # Simulate datetime for Dec 26 at 08:00 (a Saturday in 2026)

        dec26 = datetime(2026, 12, 26, 8, 0, 0, tzinfo=UTC)
        assert adapter._should_fire(cfg, dec26) is False

    def test_invalid_entry_is_skipped_gracefully(self):
        adapter = _make_adapter(custom_holidays=["INVALID_ENTRY", "12-25:Weihnachten"])
        adapter._hol = adapter._build_holidays()
        # Valid entry still works
        assert adapter._is_holiday(date(2026, 12, 25)) is True


# ---------------------------------------------------------------------------
# ZeitschaltuhrConfig — field_validator string coercion
# ---------------------------------------------------------------------------


class TestCustomHolidaysStringCoercion:
    def test_single_string_becomes_single_element_list(self):
        cfg = ZeitschaltuhrConfig(custom_holidays="05-02:DanielsTag")
        assert cfg.custom_holidays == ["05-02:DanielsTag"]

    def test_comma_separated_string_splits_correctly(self):
        cfg = ZeitschaltuhrConfig(custom_holidays="12-26:Stephanstag, easter+1:Ostermontag")
        assert cfg.custom_holidays == ["12-26:Stephanstag", "easter+1:Ostermontag"]

    def test_newline_separated_string_splits_correctly(self):
        cfg = ZeitschaltuhrConfig(custom_holidays="12-26:Stephanstag\neaster+1:Ostermontag")
        assert cfg.custom_holidays == ["12-26:Stephanstag", "easter+1:Ostermontag"]

    def test_list_input_unchanged(self):
        cfg = ZeitschaltuhrConfig(custom_holidays=["12-26:Stephanstag", "easter+1:Ostermontag"])
        assert cfg.custom_holidays == ["12-26:Stephanstag", "easter+1:Ostermontag"]

    def test_empty_string_becomes_empty_list(self):
        cfg = ZeitschaltuhrConfig(custom_holidays="")
        assert cfg.custom_holidays == []


# ---------------------------------------------------------------------------
# Feiertagsschaltuhr (timer_type = "holiday")
# ---------------------------------------------------------------------------


class TestShouldFireHolidayType:
    def _adapter_on_holiday(self, holiday_name: str = "Neujahr") -> tuple[ZeitschaltuhrAdapter, datetime]:
        adapter = _make_adapter()
        d = date(2026, 1, 1)
        adapter._hol = {d: holiday_name}
        now = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)  # Thursday (weekday=3)
        return adapter, now

    def test_fires_on_holiday_no_filter(self):
        adapter, now = self._adapter_on_holiday()
        cfg = _cfg(timer_type=TimerType.HOLIDAY, time_ref=TimeRef.ABSOLUTE, hour=8, minute=0)
        assert adapter._should_fire(cfg, now) is True

    def test_does_not_fire_on_non_holiday(self):
        adapter = _make_adapter()  # no holidays
        now = _now(hour=8, minute=0)
        cfg = _cfg(timer_type=TimerType.HOLIDAY, time_ref=TimeRef.ABSOLUTE, hour=8, minute=0)
        assert adapter._should_fire(cfg, now) is False

    def test_fires_when_holiday_in_selected_list(self):
        adapter, now = self._adapter_on_holiday("Neujahr")
        cfg = _cfg(
            timer_type=TimerType.HOLIDAY,
            selected_holidays=["Neujahr", "Weihnachten"],
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
        )
        assert adapter._should_fire(cfg, now) is True

    def test_does_not_fire_when_holiday_not_in_selected_list(self):
        adapter, now = self._adapter_on_holiday("Neujahr")
        cfg = _cfg(
            timer_type=TimerType.HOLIDAY,
            selected_holidays=["Weihnachten"],
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
        )
        assert adapter._should_fire(cfg, now) is False

    def test_fires_at_correct_time(self):
        adapter, _ = self._adapter_on_holiday()
        now_wrong = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
        cfg = _cfg(timer_type=TimerType.HOLIDAY, time_ref=TimeRef.ABSOLUTE, hour=8, minute=0)
        assert adapter._should_fire(cfg, now_wrong) is False

    def test_does_not_use_weekday_filter(self):
        """Holiday type ignores weekdays — Thursday holiday should fire even with weekdays=[6] (Sunday only)."""
        adapter, now = self._adapter_on_holiday()
        cfg = _cfg(
            timer_type=TimerType.HOLIDAY,
            weekdays=[6],  # Sunday only
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
        )
        assert adapter._should_fire(cfg, now) is True

    def test_every_minute_fires_on_holiday(self):
        adapter, now = self._adapter_on_holiday()
        cfg = _cfg(timer_type=TimerType.HOLIDAY, every_minute=True)
        assert adapter._should_fire(cfg, now) is True

    def test_vacation_skip_prevents_fire_on_holiday(self):
        adapter, now = self._adapter_on_holiday()
        # Also put the date in a vacation period
        adapter._cfg = ZeitschaltuhrConfig(vacation_1_start="2026-01-01", vacation_1_end="2026-01-07")
        cfg = _cfg(
            timer_type=TimerType.HOLIDAY,
            vacation_mode=HolidayMode.SKIP,
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
        )
        assert adapter._should_fire(cfg, now) is False

    def test_empty_selected_holidays_fires_on_all(self):
        adapter = _make_adapter()
        day1 = date(2026, 1, 1)
        day2 = date(2026, 3, 26)
        adapter._hol = {day1: "Neujahr", day2: "open bridge server Geburtstag"}
        for d, name in [(day1, "Neujahr"), (day2, "open bridge server Geburtstag")]:
            now = datetime(d.year, d.month, d.day, 8, 0, 0, tzinfo=UTC)
            cfg = _cfg(
                timer_type=TimerType.HOLIDAY,
                selected_holidays=[],  # no filter
                time_ref=TimeRef.ABSOLUTE,
                hour=8,
                minute=0,
            )
            assert adapter._should_fire(cfg, now) is True, f"Should fire on {name}"


# ---------------------------------------------------------------------------
# _parse_date_expression
# ---------------------------------------------------------------------------


class TestParseDateExpression:
    def _adapter_with_hol(self) -> ZeitschaltuhrAdapter:
        adapter = _make_adapter()
        adapter._hol = {
            date(2026, 1, 1): "Neujahr",
            date(2026, 8, 1): "Nationalfeiertag",
        }
        return adapter

    def test_fixed_date(self):
        adapter = _make_adapter()
        result = adapter._parse_date_expression("05-01", 2026)
        assert result == date(2026, 5, 1)

    def test_fixed_date_leading_zeros(self):
        adapter = _make_adapter()
        assert adapter._parse_date_expression("01-01", 2026) == date(2026, 1, 1)

    def test_easter_no_offset(self):
        adapter = _make_adapter()
        assert adapter._parse_date_expression("easter", 2026) == _easter_date(2026)

    def test_easter_plus_offset(self):
        from datetime import timedelta

        adapter = _make_adapter()
        result = adapter._parse_date_expression("easter+1", 2026)
        assert result == _easter_date(2026) + timedelta(days=1)

    def test_easter_minus_offset(self):
        from datetime import timedelta

        adapter = _make_adapter()
        result = adapter._parse_date_expression("easter-7", 2026)
        assert result == _easter_date(2026) + timedelta(days=-7)

    def test_advent_no_offset(self):
        adapter = _make_adapter()
        assert adapter._parse_date_expression("advent", 2026) == _advent1_date(2026)

    def test_advent_plus_21(self):
        from datetime import timedelta

        adapter = _make_adapter()
        result = adapter._parse_date_expression("advent+21", 2026)
        assert result == _advent1_date(2026) + timedelta(days=21)

    def test_holiday_name_found_in_hol(self):
        adapter = self._adapter_with_hol()
        result = adapter._parse_date_expression("holiday:Neujahr", 2026)
        assert result == date(2026, 1, 1)

    def test_holiday_name_with_offset(self):
        from datetime import timedelta

        adapter = self._adapter_with_hol()
        result = adapter._parse_date_expression("holiday:Nationalfeiertag-7", 2026)
        assert result == date(2026, 8, 1) + timedelta(days=-7)

    def test_holiday_name_not_found_returns_none(self):
        adapter = _make_adapter()  # empty _hol
        result = adapter._parse_date_expression("holiday:Unbekannt", 2026)
        assert result is None

    def test_unknown_expr_returns_none(self):
        adapter = _make_adapter()
        assert adapter._parse_date_expression("completely_invalid", 2026) is None

    def test_empty_expr_returns_none(self):
        adapter = _make_adapter()
        assert adapter._parse_date_expression("", 2026) is None

    def test_case_insensitive_easter(self):
        adapter = _make_adapter()
        assert adapter._parse_date_expression("EASTER+1", 2026) == adapter._parse_date_expression("easter+1", 2026)


# ---------------------------------------------------------------------------
# _in_date_window
# ---------------------------------------------------------------------------


class TestInDateWindow:
    def test_today_within_window(self):
        adapter = _make_adapter()
        # Window: May 1 to Aug 31; today = July 15
        assert adapter._in_date_window("05-01", "08-31", date(2026, 7, 15)) is True

    def test_today_on_from_boundary(self):
        adapter = _make_adapter()
        assert adapter._in_date_window("05-01", "08-31", date(2026, 5, 1)) is True

    def test_today_on_to_boundary(self):
        adapter = _make_adapter()
        assert adapter._in_date_window("05-01", "08-31", date(2026, 5, 31)) is True

    def test_today_before_window(self):
        adapter = _make_adapter()
        assert adapter._in_date_window("05-01", "08-31", date(2026, 4, 30)) is False

    def test_today_after_window(self):
        adapter = _make_adapter()
        assert adapter._in_date_window("05-01", "08-31", date(2026, 9, 1)) is False

    def test_cross_year_window_within(self):
        adapter = _make_adapter()
        # Advent (late Nov) → Epiphany (Jan 6 next year): Dec 24 should be inside
        assert adapter._in_date_window("advent+0", "01-06", date(2026, 12, 24)) is True

    def test_cross_year_window_in_january(self):
        adapter = _make_adapter()
        # Jan 3 should be inside the Advent→Epiphany window
        assert adapter._in_date_window("advent+0", "01-06", date(2026, 1, 3)) is True

    def test_cross_year_window_after_epiphany(self):
        adapter = _make_adapter()
        # Jan 7 should be outside
        assert adapter._in_date_window("advent+0", "01-06", date(2026, 1, 7)) is False

    def test_easter_window(self):
        adapter = _make_adapter()
        # Easter 2026 = Apr 5; window: easter-7 to easter+7
        easter = _easter_date(2026)
        from datetime import timedelta

        assert adapter._in_date_window("easter-7", "easter+7", easter) is True
        assert adapter._in_date_window("easter-7", "easter+7", easter - timedelta(days=8)) is False
        assert adapter._in_date_window("easter-7", "easter+7", easter + timedelta(days=8)) is False

    def test_holiday_name_window(self):
        adapter = _make_adapter()
        adapter._hol[date(2026, 8, 1)] = "Nationalfeiertag"

        # Window: 7 days before to 7 days after Nationalfeiertag (Aug 1 ± 7)
        assert adapter._in_date_window("holiday:Nationalfeiertag-7", "holiday:Nationalfeiertag+7", date(2026, 8, 1)) is True
        assert adapter._in_date_window("holiday:Nationalfeiertag-7", "holiday:Nationalfeiertag+7", date(2026, 7, 25)) is True
        assert adapter._in_date_window("holiday:Nationalfeiertag-7", "holiday:Nationalfeiertag+7", date(2026, 7, 24)) is False

    def test_invalid_expression_returns_false(self):
        adapter = _make_adapter()
        assert adapter._in_date_window("INVALID", "08-31", date(2026, 7, 15)) is False

    def test_advent_window_within_one_year(self):
        adapter = _make_adapter()
        # 1. Advent to 4. Advent: advent+0 to advent+21
        advent1_2026 = _advent1_date(2026)
        from datetime import timedelta

        advent4_2026 = advent1_2026 + timedelta(days=21)
        # 2. Advent (day 7) should be inside
        assert adapter._in_date_window("advent+0", "advent+21", advent1_2026 + timedelta(days=7)) is True
        # Day before 1. Advent should be outside
        assert adapter._in_date_window("advent+0", "advent+21", advent1_2026 - timedelta(days=1)) is False
        # Day after 4. Advent should be outside
        assert adapter._in_date_window("advent+0", "advent+21", advent4_2026 + timedelta(days=1)) is False


class TestGetHolidaysForYear:
    def test_returns_list_with_date_and_name(self):
        adapter = _make_adapter(custom_holidays=["01-01:Neujahr", "12-25:Weihnachten"])
        result = adapter.get_holidays_for_year(2026)
        dates = [h["date"] for h in result]
        names = [h["name"] for h in result]
        assert "2026-01-01" in dates
        assert "2026-12-25" in dates
        assert "Neujahr" in names
        assert "Weihnachten" in names

    def test_result_is_sorted_by_date(self):
        adapter = _make_adapter(custom_holidays=["12-25:Weihnachten", "01-01:Neujahr"])
        result = adapter.get_holidays_for_year(2026)
        dates = [h["date"] for h in result]
        assert dates == sorted(dates)

    def test_empty_when_no_library_and_no_custom(self):
        adapter = _make_adapter()
        # Without the holidays library or custom entries, result may be empty
        # (We cannot guarantee the library is installed, so just check it doesn't crash)
        result = adapter.get_holidays_for_year(2026)
        assert isinstance(result, list)
        for h in result:
            assert "date" in h
            assert "name" in h
