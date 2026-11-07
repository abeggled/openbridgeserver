"""Runtime of the Takt/Tick block (``timer_interval``, issue #446)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic.executor import GraphExecutor
from obs.logic.manager import LogicManager
from obs.logic.models import FlowData


def _flow(data: dict | None = None) -> FlowData:
    return FlowData.model_validate({"nodes": [{"id": "t1", "type": "timer_interval", "position": {"x": 0, "y": 0}, "data": data or {}}], "edges": []})


def _manager(graphs: dict) -> LogicManager:
    db = MagicMock()
    db.execute_and_commit = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    registry = MagicMock()
    registry.get_value = MagicMock(return_value=None)
    mgr = LogicManager(db, event_bus, registry)
    mgr._graphs = graphs
    return mgr


@pytest.mark.parametrize(("inputs", "expected"), [({"trigger": True}, True), ({}, False)])
def test_executor_passes_the_fired_trigger_through(inputs, expected):
    out = GraphExecutor(_flow()).execute({"t1": inputs} if inputs else {})
    assert out["t1"]["trigger"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("data", "expected"), [({"interval_s": 2}, 2.0), ({"interval_s": 0.2}, 1.0), ({}, 5.0)])
async def test_start_schedules_one_loop_per_block_with_a_bounded_interval(data, expected):
    mgr = _manager({"g1": ("G1", True, _flow(data))})
    mgr._interval_loop = MagicMock(return_value=asyncio.sleep(3600))
    mgr._start_cron_tasks()
    try:
        assert ("g1", "t1") in mgr._cron_tasks
        mgr._interval_loop.assert_called_once_with("g1", "t1", expected)
        # A second start leaves the running loop alone.
        mgr._start_cron_tasks()
        mgr._interval_loop.assert_called_once()
    finally:
        for task in mgr._cron_tasks.values():
            task.cancel()
        await asyncio.gather(*mgr._cron_tasks.values(), return_exceptions=True)


async def _run_loop(mgr: LogicManager, ticks: int) -> list[float]:
    sleeps: list[float] = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > ticks:
            raise asyncio.CancelledError()

    with patch("obs.logic.manager.asyncio.sleep", _fake_sleep), pytest.raises(asyncio.CancelledError):
        await mgr._interval_loop("g1", "t1", 3.0)
    return sleeps


@pytest.mark.asyncio
async def test_loop_fires_the_block_like_a_cron_tick():
    flow = _flow()
    mgr = _manager({"g1": ("G1", True, flow)})
    mgr._execute_graph = AsyncMock()
    assert await _run_loop(mgr, 2) == [3.0, 3.0, 3.0]
    assert mgr._execute_graph.await_count == 2
    mgr._execute_graph.assert_awaited_with("g1", "G1", flow, {"t1": {"trigger": True}})


@pytest.mark.asyncio
@pytest.mark.parametrize("graphs", [{}, {"g1": ("G1", False, _flow())}])
async def test_loop_does_not_fire_a_missing_or_disabled_sheet(graphs):
    mgr = _manager(graphs)
    mgr._execute_graph = AsyncMock()
    await _run_loop(mgr, 1)
    mgr._execute_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_loop_survives_a_failing_run_and_backs_off():
    mgr = _manager({"g1": ("G1", True, _flow())})
    mgr._execute_graph = AsyncMock(side_effect=RuntimeError("boom"))
    # tick, back-off, tick → cancelled on the fourth sleep
    assert await _run_loop(mgr, 3) == [3.0, 3.0, 3.0, 3.0]
    assert mgr._execute_graph.await_count == 2


def test_validation_rejects_a_negative_interval():
    from obs.logic.validation import validate_timer_durations

    with pytest.raises(ValueError, match="interval_s"):
        validate_timer_durations(_flow({"interval_s": -1}))
    validate_timer_durations(_flow({"interval_s": 0}))
