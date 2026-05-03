"""Contract tests for croniter — verifies the API surface used by obs.logic.manager."""

from __future__ import annotations

from datetime import UTC, datetime

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
