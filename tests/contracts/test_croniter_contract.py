"""Contract tests for croniter — verifies the API surface used by obs.logic.manager."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

croniter = pytest.importorskip("croniter", reason="croniter not installed")

from croniter import croniter as Croniter


class TestCroniterConstruction:
    def test_basic_construction(self):
        now = datetime.now(UTC)
        it = Croniter("0 7 * * *", now)
        assert it is not None

    def test_every_minute_expression(self):
        now = datetime.now(UTC)
        it = Croniter("* * * * *", now)
        assert it is not None

    def test_complex_expression(self):
        now = datetime.now(UTC)
        it = Croniter("*/5 8-18 * * 1-5", now)
        assert it is not None


class TestCroniterScheduling:
    def test_get_next_returns_datetime(self):
        now = datetime(2024, 1, 1, 6, 0, 0, tzinfo=UTC)
        it = Croniter("0 7 * * *", now)
        next_dt = it.get_next(datetime)
        assert isinstance(next_dt, datetime)

    def test_get_next_is_in_future(self):
        now = datetime(2024, 1, 1, 6, 0, 0, tzinfo=UTC)
        it = Croniter("0 7 * * *", now)
        next_dt = it.get_next(datetime)
        assert next_dt > now

    def test_get_next_matches_cron_expression(self):
        # "0 7 * * *" → next fire is 07:00
        now = datetime(2024, 1, 1, 6, 0, 0, tzinfo=UTC)
        it = Croniter("0 7 * * *", now)
        next_dt = it.get_next(datetime)
        assert next_dt.hour == 7
        assert next_dt.minute == 0

    def test_get_next_called_twice_advances(self):
        now = datetime(2024, 1, 1, 6, 0, 0, tzinfo=UTC)
        it = Croniter("0 7 * * *", now)
        first = it.get_next(datetime)
        second = it.get_next(datetime)
        assert second > first

    def test_manager_pattern_wait_seconds_non_negative(self):
        # Reproduces the exact usage in obs/logic/manager.py _cron_loop:
        #   it = croniter(cron_expr, now)
        #   next_dt = it.get_next(datetime)
        #   wait_s = max(0.0, (next_dt - now).total_seconds())
        now = datetime.now(UTC)
        it = Croniter("*/5 * * * *", now)
        next_dt = it.get_next(datetime)
        wait_s = max(0.0, (next_dt - now).total_seconds())
        assert wait_s >= 0.0
        assert wait_s <= 300.0  # at most 5 minutes for */5 * * * *


_ZURICH = ZoneInfo("Europe/Zurich")


def _next(expr: str, start: datetime, count: int = 1) -> list[datetime]:
    it = Croniter(expr, start)
    return [it.get_next(datetime) for _ in range(count)]


class TestCroniter7Semantics:
    """Meanings that changed in croniter 7 (#1444).

    obs.logic.manager warns about the max-step form and refuses to schedule an
    expression that cannot fire; these pin the library behaviour it relies on,
    so the next major bump fails here instead of silently moving schedules.
    """

    def test_exception_classes_are_exported(self):
        from croniter import CroniterBadCronError, CroniterBadDateError, CroniterError

        assert issubclass(CroniterBadCronError, CroniterError)
        assert issubclass(CroniterBadDateError, CroniterError)

    def test_step_from_the_minute_maximum_means_that_minute_only(self):
        start = datetime(2026, 11, 15, 12, 3, tzinfo=_ZURICH)
        assert [dt.minute for dt in _next("59/15 * * * *", start, 3)] == [59, 59, 59]

    def test_step_from_the_weekday_maximum_means_that_weekday_only(self):
        start = datetime(2026, 11, 15, 12, 3, tzinfo=_ZURICH)  # a Sunday
        assert {dt.weekday() for dt in _next("0 0 * * 6/1", start, 3)} == {5}  # Saturday

    def test_step_below_the_maximum_still_covers_the_field(self):
        start = datetime(2026, 11, 15, 12, 3, tzinfo=_ZURICH)
        assert [dt.minute for dt in _next("0/15 * * * *", start, 4)] == [15, 30, 45, 0]

    def test_unsatisfiable_max_step_raises_bad_date(self):
        from croniter import CroniterBadDateError

        with pytest.raises(CroniterBadDateError):
            _next("0 0 31/5 2 *", datetime(2026, 11, 15, 12, 3, tzinfo=_ZURICH))

    @pytest.mark.parametrize("expr", ["0 0 0 * * *", "٥ * * * *", "0 0 * * 1#0", "0 0 * * 1#7", ""])
    def test_rejected_expressions_are_invalid(self, expr):
        assert Croniter.is_valid(expr) is False

    @pytest.mark.parametrize("expr", ["0 7 * * *", "*/15 * * * *", "0 8-17 * * 1-5", "0 0 * * 7", "0 0 1 1 *"])
    def test_ordinary_expressions_stay_valid(self, expr):
        assert Croniter.is_valid(expr) is True


class TestCroniterDstEuropeZurich:
    """The manager evaluates schedules in the local system timezone (#1201)."""

    def test_daily_trigger_keeps_local_wall_clock_across_dst(self):
        start = datetime(2027, 3, 26, 8, 0, tzinfo=_ZURICH)
        fires = _next("0 7 * * *", start, 4)
        assert [dt.hour for dt in fires] == [7, 7, 7, 7]
        assert [dt.utcoffset().total_seconds() / 3600 for dt in fires] == [1, 2, 2, 2]  # 27.03. CET, from 28.03. CEST

    def test_slot_in_the_spring_forward_gap_fires_once_after_the_gap(self):
        start = datetime(2027, 3, 28, 1, 30, tzinfo=_ZURICH)  # 02:00-03:00 does not exist
        first, second = _next("30 2 * * *", start, 2)
        assert (first.date().isoformat(), first.hour, first.minute) == ("2027-03-28", 3, 0)
        assert (second.date().isoformat(), second.hour, second.minute) == ("2027-03-29", 2, 30)

    def test_slot_in_the_repeated_fall_back_hour_fires_in_both_offsets(self):
        start = datetime(2027, 10, 31, 1, 30, tzinfo=_ZURICH)  # 02:00-03:00 occurs twice
        first, second, third = _next("30 2 * * *", start, 3)
        assert [first.hour, second.hour] == [2, 2]
        assert [first.utcoffset().total_seconds(), second.utcoffset().total_seconds()] == [7200, 3600]
        assert third.date().isoformat() == "2027-11-01"
