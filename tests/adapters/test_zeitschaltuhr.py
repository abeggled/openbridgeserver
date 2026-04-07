"""
Unit tests for the Zeitschaltuhr adapter — pure logic functions.

_should_fire, _calculate_target_time, _is_vacation_n, _is_vacation, _is_holiday
are all pure / near-pure methods that can be tested without asyncio.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from obs.adapters.zeitschaltuhr.adapter import (
    HolidayMode,
    TimerType,
    TimeRef,
    ZeitschaltuhrAdapter,
    ZeitschaltuhrBindingConfig,
    ZeitschaltuhrConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(**cfg_overrides) -> ZeitschaltuhrAdapter:
    """Create an adapter instance pre-configured with UTC + empty holidays."""
    bus = MagicMock()
    adapter = ZeitschaltuhrAdapter(event_bus=bus, config={})
    adapter._tz = timezone.utc
    adapter._hol = {}  # no holidays by default
    adapter._cfg = ZeitschaltuhrConfig(**cfg_overrides)
    return adapter


def _cfg(**kwargs) -> ZeitschaltuhrBindingConfig:
    """Shorthand for building a binding config with sensible defaults."""
    return ZeitschaltuhrBindingConfig(**kwargs)


def _now(hour: int = 8, minute: int = 0, weekday_offset: int = 0) -> datetime:
    """
    Return a UTC datetime for a Monday (2026-04-06) + weekday_offset days,
    at hour:minute.  weekday_offset=0 → Monday, 1 → Tuesday, …, 6 → Sunday.
    """
    from datetime import timedelta
    base = datetime(2026, 4, 6, hour, minute, 0, tzinfo=timezone.utc)  # a Monday
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
    def _monday_with_holiday(self) -> "tuple[ZeitschaltuhrAdapter, datetime]":
        adapter = _make_adapter()
        monday = date(2026, 4, 6)
        adapter._hol = {monday: "Test Holiday"}
        now = _now(hour=8, minute=0, weekday_offset=0)  # that Monday
        return adapter, now

    def test_holiday_skip_prevents_fire(self):
        adapter, now = self._monday_with_holiday()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
            holiday_mode=HolidayMode.SKIP,
        )
        assert adapter._should_fire(cfg, now) is False

    def test_holiday_only_fires_on_holiday(self):
        adapter, now = self._monday_with_holiday()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
            holiday_mode=HolidayMode.ONLY,
        )
        assert adapter._should_fire(cfg, now) is True

    def test_holiday_only_prevents_fire_on_normal_day(self):
        adapter = _make_adapter()  # no holidays
        now = _now(hour=8, minute=0)  # normal Monday
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
            holiday_mode=HolidayMode.ONLY,
        )
        assert adapter._should_fire(cfg, now) is False

    def test_holiday_as_sunday_shifts_weekday_to_6(self):
        adapter, now = self._monday_with_holiday()
        # Monday (0) promoted to Sunday (6) → Sunday-only cfg should fire
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
            weekdays=[6],   # only Sunday
            holiday_mode=HolidayMode.AS_SUNDAY,
        )
        assert adapter._should_fire(cfg, now) is True

    def test_holiday_ignore_fires_normally(self):
        adapter, now = self._monday_with_holiday()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
            holiday_mode=HolidayMode.IGNORE,
        )
        assert adapter._should_fire(cfg, now) is True


# ---------------------------------------------------------------------------
# _should_fire — vacation mode
# ---------------------------------------------------------------------------

class TestShouldFireVacationMode:
    def _adapter_in_vacation(self) -> "tuple[ZeitschaltuhrAdapter, datetime]":
        adapter = _make_adapter(
            vacation_1_start="2026-04-01",
            vacation_1_end="2026-04-10",
        )
        now = _now(hour=8, minute=0)  # 2026-04-06 = in vacation window
        return adapter, now

    def test_vacation_skip_prevents_fire(self):
        adapter, now = self._adapter_in_vacation()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
            vacation_mode=HolidayMode.SKIP,
        )
        assert adapter._should_fire(cfg, now) is False

    def test_vacation_only_fires_during_vacation(self):
        adapter, now = self._adapter_in_vacation()
        cfg = _cfg(
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
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
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
            months=[4],  # April
        )
        # _now uses April 6, 2026 (Monday)
        assert adapter._should_fire(cfg, _now(8, 0)) is True

    def test_annual_does_not_fire_in_wrong_month(self):
        adapter = _make_adapter()
        cfg = _cfg(
            timer_type=TimerType.ANNUAL,
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
            months=[5],  # May only
        )
        assert adapter._should_fire(cfg, _now(8, 0)) is False  # April

    def test_annual_day_of_month_filter(self):
        adapter = _make_adapter()
        cfg = _cfg(
            timer_type=TimerType.ANNUAL,
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
            months=[4],
            day_of_month=6,  # April 6
        )
        assert adapter._should_fire(cfg, _now(8, 0)) is True  # April 6

        cfg2 = _cfg(
            timer_type=TimerType.ANNUAL,
            time_ref=TimeRef.ABSOLUTE, hour=8, minute=0,
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
