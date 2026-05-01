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
        expected_date = _last_weekday_of_month(2026, 11, 6)
        assert date(2026, 11, 29) in result

    def test_nth_weekday(self):
        adapter = self._adapter()
        result = adapter._parse_custom_holiday_entry("2_SO_OKT:Erntedank", 2026)
        expected_date = _nth_weekday_of_month(2026, 10, 6, 2)
        assert result == {expected_date: "Erntedank"}

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
        from datetime import timezone
        dec26 = datetime(2026, 12, 26, 8, 0, 0, tzinfo=UTC)
        assert adapter._should_fire(cfg, dec26) is False

    def test_invalid_entry_is_skipped_gracefully(self):
        adapter = _make_adapter(custom_holidays=["INVALID_ENTRY", "12-25:Weihnachten"])
        adapter._hol = adapter._build_holidays()
        # Valid entry still works
        assert adapter._is_holiday(date(2026, 12, 25)) is True
