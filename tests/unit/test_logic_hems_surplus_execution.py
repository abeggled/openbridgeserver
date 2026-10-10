"""Executor and manager integration of the ``hems_surplus`` block (issue #1327).

The control algorithm itself is covered in ``test_logic_hems_surplus_engine.py``;
this file drives the block the way production does — through the dispatcher,
the autonomous scheduler loop, persistence and a real Read Object → Write Object
wiring.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic.executor import GraphExecutor
from obs.logic.manager import LogicManager
from obs.logic.models import FlowData
from tests.unit.conftest import edge, node

CONSUMERS = [
    {"id": "boiler", "name": "Boiler", "mode": "onoff", "power_w": 2000, "on_delay_s": 0},
    {"id": "wash", "name": "Wash", "mode": "trigger", "min_surplus_w": 1500, "on_delay_s": 0, "pulse_s": 1},
]


def _flow(consumers=CONSUMERS, **extra) -> FlowData:
    return FlowData.model_validate({"nodes": [node("h", "hems_surplus", {"consumers": consumers, **extra})], "edges": []})


def _manager(values: dict[str, object] | None = None) -> LogicManager:
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(data_type="UNKNOWN")
    seeded = values or {}
    registry.get_value.side_effect = lambda dp_id: SimpleNamespace(value=seeded.get(str(dp_id)), ts=None)
    return LogicManager(AsyncMock(), AsyncMock(), registry)


# ── Executor dispatch ───────────────────────────────────────────────────


def test_dispatcher_runs_the_engine_and_keeps_state_between_runs():
    flow = _flow()
    state: dict = {}
    executor = GraphExecutor(flow, state)
    out = executor.execute(input_overrides={"h": {"grid": -2500}})["h"]
    assert out["c_boiler"] is True
    assert out["c_wash"] is False
    assert out["status"] == "ok"
    assert state["h"]["consumers"]["boiler"]["on"] is True

    # the next tick inside the control interval emits nothing at all
    again = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})["h"]
    assert again == {}


def test_a_debug_run_shows_every_output_even_inside_the_control_interval():
    flow = _flow()
    state: dict = {}
    GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    plain = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})["h"]
    assert plain == {}
    debug = GraphExecutor(flow, state, input_capture={}).execute(input_overrides={"h": {"grid": -2500}})["h"]
    assert debug["c_boiler"] is True
    assert debug["status"] == "ok"
    assert "surplus" in debug


def test_dispatcher_puts_an_invalid_meter_into_the_safe_state():
    flow = _flow()
    out = GraphExecutor(flow, {}).execute(input_overrides={"h": {"grid": "n/a"}})["h"]
    assert out["status"] == "invalid_input"
    assert out["c_boiler"] is False


def test_withheld_outputs_do_not_count_as_a_failed_producer_for_a_change_filter():
    flow = FlowData.model_validate(
        {
            "nodes": [node("h", "hems_surplus", {"consumers": CONSUMERS}), node("cf", "change_filter", {})],
            "edges": [edge("h", "cf", "c_boiler", "in")],
        }
    )
    state: dict = {}
    GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    out = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    assert "__error__" not in out["cf"]


# ── Scheduler ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_cron_tasks_schedules_the_hems_loop_once_per_node():
    manager = _manager()
    manager._graphs["g"] = ("G", True, _flow())
    manager._start_cron_tasks()
    task = manager._cron_tasks[("g", "h")]
    assert task.get_name().startswith("hems-")
    manager._start_cron_tasks()  # already running → no second task
    assert manager._cron_tasks[("g", "h")] is task
    task.cancel()


def _loop_manager(flow=None, enabled=True):
    manager = _manager()
    manager._graphs["g"] = ("G", enabled, flow or _flow(interval_s=45))
    manager._execute_graph = AsyncMock(return_value={})
    return manager


async def _run_loop(manager, iterations=1):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= iterations:
            raise asyncio.CancelledError()

    with patch("obs.logic.manager.asyncio.sleep", fake_sleep), pytest.raises(asyncio.CancelledError):
        await manager._hems_loop("g", "h")
    return sleeps


@pytest.mark.asyncio
async def test_loop_reevaluates_the_graph_at_the_configured_interval():
    manager = _loop_manager()
    sleeps = await _run_loop(manager)
    manager._execute_graph.assert_awaited_once_with("g", "G", manager._graphs["g"][2], {"h": {}})
    assert sleeps == [45.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(("configured", "expected"), [(1, 5.0), (10**6, 3600.0), ("x", 30.0)])
async def test_loop_clamps_the_interval(configured, expected):
    sleeps = await _run_loop(_loop_manager(_flow(interval_s=configured)))
    assert sleeps == [expected]


@pytest.mark.asyncio
async def test_loop_wakes_early_for_a_pending_pulse_end():
    manager = _loop_manager()
    now = 1_000_000.0
    manager._hysteresis = {"g": {"h": {"next_wake": now + 2}}}
    with patch("obs.logic.manager.datetime") as fake_datetime:
        fake_datetime.now.return_value.timestamp.return_value = now
        sleeps = await _run_loop(manager)
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_loop_never_sleeps_less_than_a_tenth_of_a_second():
    manager = _loop_manager()
    now = 1_000_000.0
    manager._hysteresis = {"g": {"h": {"next_wake": now - 50}}}
    with patch("obs.logic.manager.datetime") as fake_datetime:
        fake_datetime.now.return_value.timestamp.return_value = now
        assert await _run_loop(manager) == [0.1]


@pytest.mark.asyncio
async def test_loop_skips_a_disabled_graph_and_a_missing_node():
    manager = _loop_manager(enabled=False)
    assert await _run_loop(manager) == [30.0]
    manager._execute_graph.assert_not_awaited()

    manager = _loop_manager(FlowData.model_validate({"nodes": [node("other", "const_value", {})], "edges": []}))
    assert await _run_loop(manager) == [30.0]
    manager._execute_graph.assert_awaited_once()


@pytest.mark.asyncio
async def test_loop_backs_off_after_an_unexpected_error():
    manager = _loop_manager()
    manager._execute_graph = AsyncMock(side_effect=RuntimeError("boom"))
    assert await _run_loop(manager) == [60]


# ── Persistence and initialization ──────────────────────────────────────


@pytest.mark.asyncio
async def test_state_is_never_persisted_so_a_restart_begins_safe():
    flow = FlowData.model_validate({"nodes": [node("h", "hems_surplus", {"consumers": CONSUMERS}), node("cf", "change_filter", {})], "edges": []})
    manager = _manager()
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"h": {"consumers": {"boiler": {"on": True}}}, "cf": {"value": 1}}
    await manager._persist_node_state("g")
    saved_json = manager._db.execute_and_commit.await_args.args[1][0]
    assert "boiler" not in saved_json  # the hems_surplus state stays out of the snapshot …
    assert '"cf"' in saved_json  # … while an ordinary stateful block is still saved
    assert set(json.loads(saved_json)["state"]) == {"cf"}


@pytest.mark.asyncio
async def test_saving_a_graph_does_not_start_the_control_or_publish_anything():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(dp_grid)}),
                node("h", "hems_surplus", {"consumers": CONSUMERS}),
                node("w", "datapoint_write", {"datapoint_id": str(dp_out)}),
            ],
            "edges": [edge("read", "h", "value", "grid"), edge("h", "w", "c_boiler", "value")],
        }
    )
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    await manager.initialize_graph("g")
    assert "h" not in manager._hysteresis.get("g", {})
    manager._event_bus.publish.assert_not_awaited()


# ── Real wiring: Read Object → Surplus control → Write Object ───────────


@pytest.mark.asyncio
async def test_real_wiring_publishes_switch_and_one_shot_trigger():
    dp_grid, dp_boiler, dp_wash = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(dp_grid)}),
                node("h", "hems_surplus", {"consumers": CONSUMERS, "interval_s": 5}),
                node("w1", "datapoint_write", {"datapoint_id": str(dp_boiler)}),
                node("w2", "datapoint_write", {"datapoint_id": str(dp_wash)}),
            ],
            "edges": [
                edge("read", "h", "value", "grid"),
                edge("h", "w1", "c_boiler", "value"),
                edge("h", "w2", "c_wash", "value"),
            ],
        }
    )
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)

    await manager._execute_graph("g", "G", flow, {})
    published = {str(c.args[0].datapoint_id): c.args[0].value for c in manager._event_bus.publish.await_args_list}
    assert published[str(dp_boiler)] is True
    assert published[str(dp_wash)] is True  # 3000 W left ≥ 1500 W, no delay configured
    state = manager._hysteresis["g"]["h"]
    assert state["next_wake"] is not None  # the pulse end is scheduled

    # the pulse ends and the trigger is not fired again
    state["consumers"]["wash"]["pulse_until"] = 0.0
    manager._event_bus.publish.reset_mock()
    await manager._execute_graph("g", "G", flow, {})
    published = [(str(c.args[0].datapoint_id), c.args[0].value) for c in manager._event_bus.publish.await_args_list]
    assert (str(dp_wash), False) in published
    assert (str(dp_wash), True) not in published
