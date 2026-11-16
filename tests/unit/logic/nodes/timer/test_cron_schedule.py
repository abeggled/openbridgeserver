"""Scheduling of ``timer_cron`` blocks against the croniter 7 semantics (#1444).

croniter 7 changed what some valid expressions mean and rejects others it used
to accept. The scheduler checks each expression once when it plans the block,
instead of logging a traceback every minute from the cron loop.
"""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from obs.logic.manager import LogicManager, _cron_max_step_fields, _cron_schedule_problem
from obs.logic.models import FlowData

ZURICH = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 11, 15, 12, 3, tzinfo=ZURICH)


def _manager(cron, enabled=True):
    db = MagicMock()
    db.execute_and_commit = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    mgr = LogicManager(db, MagicMock(), MagicMock())
    node = {"id": "cron-node-1", "type": "timer_cron", "position": {"x": 0, "y": 0}, "data": {}}
    if cron is not None:
        node["data"]["cron"] = cron
    flow = FlowData.model_validate({"nodes": [node], "edges": []})
    mgr._graphs = {"graph-0001": ("Morgens", enabled, flow)}
    return mgr


def _cancel(mgr):
    for task in mgr._cron_tasks.values():
        task.cancel()


class TestMaxStepFields:
    @pytest.mark.parametrize(
        ("expr", "fields"),
        [
            ("59/15 * * * *", ["59/15"]),
            ("0 23/6 * * *", ["23/6"]),
            ("0 0 31/5 * *", ["31/5"]),
            ("0 0 1 12/3 *", ["12/3"]),
            ("0 0 1 DEC/3 *", ["DEC/3"]),
            ("0 0 1 dec/3 *", ["dec/3"]),
            ("* * * * 6/1", ["6/1"]),
            ("* * * * SAT/2", ["SAT/2"]),
            ("* * * * sat/2", ["sat/2"]),
            # croniter reads a number with leading zeros as that number (#1444 review)
            ("059/15 * * * *", ["059/15"]),
            ("0059/15 * * * *", ["0059/15"]),
            ("0 023/6 * * *", ["023/6"]),
            ("0 0 031/5 * *", ["031/5"]),
            ("0 0 1 012/3 *", ["012/3"]),
            ("* * * * 06/1", ["06/1"]),
            ("0 0 1 1 * 0 02099/1", ["02099/1"]),
            ("0 0 * * * 59/15", ["59/15"]),  # optional seconds field
            ("0 0 1 1 * 0 2099/1", ["2099/1"]),  # optional year field
            ("0,59/15 * * * *", ["0,59/15"]),  # only one list item affected
            ("59/15 23/6 * * *", ["59/15", "23/6"]),
        ],
    )
    def test_reports_steps_starting_at_the_field_maximum(self, expr, fields):
        assert _cron_max_step_fields(expr) == fields

    @pytest.mark.parametrize(
        "expr",
        [
            "0 7 * * *",
            "*/15 * * * *",
            "58/15 * * * *",  # below the maximum: unchanged in croniter 7
            "59 * * * *",  # the maximum without a step
            "0 59/15 * * *",  # 59 is no maximum in the hour field
            "0 8-17 * * 1-5",
            "0 6,18 * * *",
            "* * * * 7/2",  # Sunday-based in croniter 6 and 7 alike: no change
            "* * * * 07/1",
            "058/15 * * * *",  # leading zero, but below the maximum
            "0 0 1 nov/3 *",  # a name, but not the maximum
            "٥٩/15 * * * *",  # non-ASCII digits are no number (croniter 7 rejects them)
        ],
    )
    def test_leaves_other_expressions_alone(self, expr):
        assert _cron_max_step_fields(expr) == []


class TestScheduleProblem:
    def test_schedulable_expression(self):
        assert _cron_schedule_problem("0 7 * * *", NOW) is None

    @pytest.mark.parametrize("expr", ["", "not a cron", "0 0 0 * * *", "٥ * * * *", "0 0 * * 1#0", None, 7])
    def test_invalid_expression(self, expr):
        assert _cron_schedule_problem(expr, NOW) == "invalid cron expression"

    def test_expression_without_future_match(self):
        # Day 31 of February: croniter 6 read 31/5 as every fifth day.
        assert _cron_schedule_problem("0 0 31/5 2 *", NOW) == "the cron expression has no future match"

    def test_unexpected_error_is_left_to_the_cron_loop(self):
        from croniter import croniter

        with patch.object(croniter, "get_next", side_effect=RuntimeError("boom")):
            assert _cron_schedule_problem("0 7 * * *", NOW) is None


class TestStartCronTasksAudit:
    @pytest.mark.asyncio
    async def test_valid_expression_is_scheduled_without_warning(self, caplog):
        mgr = _manager("0 7 * * *")
        with caplog.at_level(logging.WARNING, logger="obs.logic.manager"):
            mgr._start_cron_tasks()
        assert ("graph-0001", "cron-node-1") in mgr._cron_tasks
        assert caplog.records == []
        _cancel(mgr)

    @pytest.mark.asyncio
    async def test_default_expression_is_scheduled(self):
        mgr = _manager(None)
        mgr._start_cron_tasks()
        assert ("graph-0001", "cron-node-1") in mgr._cron_tasks
        _cancel(mgr)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("expr", "reason"),
        [
            ("0 0 0 * * *", "invalid cron expression"),
            ("0 0 31/5 2 *", "the cron expression has no future match"),
        ],
    )
    async def test_expression_that_cannot_fire_is_reported_once_and_not_scheduled(self, caplog, expr, reason):
        mgr = _manager(expr)
        with caplog.at_level(logging.WARNING, logger="obs.logic.manager"):
            mgr._start_cron_tasks()
        assert mgr._cron_tasks == {}
        [record] = caplog.records
        message = record.getMessage()
        assert "Cron not scheduled" in message
        assert "Morgens" in message
        assert "cron-nod" in message
        assert repr(expr) in message
        assert reason in message

    @pytest.mark.asyncio
    async def test_max_step_expression_warns_and_is_still_scheduled(self, caplog):
        mgr = _manager("59/15 * * * *")
        with caplog.at_level(logging.WARNING, logger="obs.logic.manager"):
            mgr._start_cron_tasks()
        assert ("graph-0001", "cron-node-1") in mgr._cron_tasks
        [record] = caplog.records
        message = record.getMessage()
        assert "'59/15 * * * *'" in message
        assert "59/15 starts at the field maximum" in message
        assert "Morgens" in message
        _cancel(mgr)

    @pytest.mark.asyncio
    async def test_fixed_expression_is_scheduled_on_the_next_reload(self):
        mgr = _manager("0 0 0 * * *")
        mgr._start_cron_tasks()
        assert mgr._cron_tasks == {}
        mgr._graphs["graph-0001"][2].nodes[0].data["cron"] = "0 0 * * *"
        mgr._start_cron_tasks()
        assert ("graph-0001", "cron-node-1") in mgr._cron_tasks
        _cancel(mgr)


class TestCronTimezone:
    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            ("Pacific/Kiritimati", "Pacific/Kiritimati"),
            ("Not/ARealZone", "Europe/Zurich"),
            ("", "Europe/Zurich"),
            (None, "Europe/Zurich"),
        ],
    )
    def test_resolves_the_configured_zone_or_falls_back_to_zurich(self, configured, expected):
        mgr = _manager("0 7 * * *")
        mgr._app_config["timezone"] = configured
        assert mgr._cron_timezone() == ZoneInfo(expected)

    def test_defaults_to_zurich_without_configuration(self):
        mgr = _manager("0 7 * * *")
        mgr._app_config.pop("timezone", None)
        assert mgr._cron_timezone() == ZURICH


@pytest.mark.asyncio
async def test_cron_loop_error_names_node_and_expression(caplog):
    import asyncio

    mgr = _manager("0 7 * * *")
    sleeps = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError()

    with (
        patch("obs.logic.manager.asyncio.sleep", _fake_sleep),
        caplog.at_level(logging.ERROR, logger="obs.logic.manager"),
    ):
        task = asyncio.create_task(mgr._cron_loop("graph-0001", "cron-node-1", "not-a-valid-cron-expression"))
        with pytest.raises(asyncio.CancelledError):
            await task

    assert sleeps == [60]
    message = caplog.records[0].getMessage()
    assert "node=cron-nod" in message
    assert "'not-a-valid-cron-expression'" in message
