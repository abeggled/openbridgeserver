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
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic.executor import GraphExecutor, hems_clock
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

    assert set(out["_emitted"]) == {k for k in out if k != "_emitted"}

    # the next tick inside the control interval sends nothing, but the graph
    # still sees every output at its current level
    again = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})["h"]
    assert again["_emitted"] == []
    assert again["c_boiler"] is True


def test_input_capture_does_not_change_what_the_block_emits():
    # The debug view (input capture) must not turn withheld outputs into writes.
    flow = _flow()
    state: dict = {}
    GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    debug = GraphExecutor(flow, state, input_capture={}).execute(input_overrides={"h": {"grid": -2500}})["h"]
    assert debug["_emitted"] == []


@pytest.mark.asyncio
async def test_a_manual_debug_run_shows_every_output_even_inside_the_control_interval():
    flow = _flow()
    manager = _manager()
    manager._graphs["g"] = ("G", True, flow)
    await manager.execute_graph_debug("g", {"h": {"grid": -2500}})
    outputs, _inputs = await manager.execute_graph_debug("g", {"h": {"grid": -2500}})
    assert outputs["h"]["c_boiler"] is True
    assert outputs["h"]["status"] == "ok"
    assert "surplus" in outputs["h"]


@pytest.mark.asyncio
async def test_an_open_debug_view_neither_repeats_writes_nor_hides_outputs():
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
    ws = MagicMock()
    ws.has_logic_debug_subscribers.return_value = True
    ws.broadcast_logic_debug = AsyncMock()
    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        for _ in range(3):
            await manager._execute_graph("g", "G", flow, {"read": {"value": -5000}})
    written = [c.args[0].value for c in manager._event_bus.publish.await_args_list if str(c.args[0].datapoint_id) == str(dp_out)]
    assert written == [True]
    payload = ws.broadcast_logic_debug.await_args_list[-1].args[1]
    assert "c_boiler" in json.dumps(payload)


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
    with patch("obs.logic.manager.hems_clock", return_value=now):
        sleeps = await _run_loop(manager)
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_loop_never_sleeps_less_than_a_tenth_of_a_second():
    manager = _loop_manager()
    now = 1_000_000.0
    manager._hysteresis = {"g": {"h": {"next_wake": now + 0.01}}}
    with patch("obs.logic.manager.hems_clock", return_value=now):
        assert await _run_loop(manager) == [0.1]


@pytest.mark.asyncio
async def test_loop_ignores_a_wake_time_the_run_did_not_refresh():
    # The node was skipped or failed, so next_wake is still the old, past value:
    # the loop must fall back to its interval instead of spinning every 0.1 s.
    manager = _loop_manager()
    now = 1_000_000.0
    manager._hysteresis = {"g": {"h": {"next_wake": now - 50}}}
    with patch("obs.logic.manager.hems_clock", return_value=now):
        assert await _run_loop(manager) == [45.0]


@pytest.mark.asyncio
async def test_loop_skips_a_disabled_graph_and_a_missing_node():
    manager = _loop_manager(enabled=False)
    assert await _run_loop(manager) == [30.0]
    manager._execute_graph.assert_not_awaited()

    manager = _loop_manager(FlowData.model_validate({"nodes": [node("other", "const_value", {})], "edges": []}))
    assert await _run_loop(manager) == [30.0]
    manager._execute_graph.assert_not_awaited()  # the block is gone: nothing to regulate


@pytest.mark.asyncio
async def test_loop_backs_off_after_an_unexpected_error():
    manager = _loop_manager()
    manager._execute_graph = AsyncMock(side_effect=RuntimeError("boom"))
    assert await _run_loop(manager) == [5.0]  # short: the safe state depends on this loop


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


def _relay_flow(dp_grid, dp_out, consumer, relay_edges, extra_nodes):
    return FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(dp_grid)}),
                node("h", "hems_surplus", {"consumers": [consumer], "interval_s": 5}),
                *extra_nodes,
            ],
            "edges": [edge("read", "h", "value", "grid"), *relay_edges],
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "consumer",
    [
        {"id": "wash", "mode": "trigger", "min_surplus_w": 1000, "on_delay_s": 0, "pulse_s": 1},
        {"id": "wash", "mode": "onoff", "power_w": 1000, "on_delay_s": 0},
    ],
)
async def test_logic_behind_a_withheld_output_never_sees_a_placeholder(consumer):
    # Between two emissions the HEMS output is unchanged, not False: a NOT
    # behind it keeps inverting the level last sent instead of a placeholder.
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _relay_flow(
        dp_grid,
        dp_out,
        consumer,
        [edge("h", "n", "c_wash", "in1"), edge("n", "w", "out", "value")],
        [node("n", "not", {}), node("w", "datapoint_write", {"datapoint_id": str(dp_out)})],
    )
    manager = _manager({str(dp_grid): -2000})
    manager._graphs["g"] = ("G", True, flow)

    def written():
        values = [c.args[0].value for c in manager._event_bus.publish.await_args_list if str(c.args[0].datapoint_id) == str(dp_out)]
        manager._event_bus.publish.reset_mock()
        return values

    await manager._execute_graph("g", "G", flow, {})
    assert written() == [False]
    await manager._execute_graph("g", "G", flow, {"h": {}})
    # never the spurious True of a missing input: a level keeps its value,
    # an unsent trigger (an event) produces nothing at all
    assert written() == ([] if consumer["mode"] == "trigger" else [False])

    if consumer["mode"] == "trigger":
        manager._hysteresis["g"]["h"]["consumers"]["wash"]["pulse_until"] = 0.0
        await manager._execute_graph("g", "G", flow, {"h": {}})
        assert written() == [True]


@pytest.mark.asyncio
async def test_a_held_level_combines_with_another_live_signal():
    dp_grid, dp_window, dp_out = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    flow = _relay_flow(
        dp_grid,
        dp_out,
        {"id": "boiler", "mode": "onoff", "power_w": 1000, "on_delay_s": 0},
        [edge("h", "and", "c_boiler", "in1"), edge("win", "and", "value", "in2"), edge("and", "w", "out", "value")],
        [
            node("win", "datapoint_read", {"datapoint_id": str(dp_window)}),
            node("and", "and", {}),
            node("w", "datapoint_write", {"datapoint_id": str(dp_out)}),
        ],
    )
    manager = _manager({str(dp_grid): -2000, str(dp_window): False})
    manager._graphs["g"] = ("G", True, flow)
    await manager._execute_graph("g", "G", flow, {})
    manager._event_bus.publish.reset_mock()
    # the window closes inside the control interval: c_boiler is withheld but still True
    await manager._execute_graph("g", "G", flow, {"win": {"value": True}})
    written = [c.args[0].value for c in manager._event_bus.publish.await_args_list if str(c.args[0].datapoint_id) == str(dp_out)]
    assert written == [True]


@pytest.mark.asyncio
async def test_a_withheld_trigger_does_not_restart_a_value_sequence():
    dp_grid = uuid.uuid4()
    flow = _relay_flow(
        dp_grid,
        None,
        {"id": "wash", "mode": "trigger", "min_surplus_w": 1000, "on_delay_s": 0, "pulse_s": 1},
        [edge("h", "seq", "c_wash", "trigger")],
        [node("seq", "value_sequence", {"steps": []})],
    )
    manager = _manager({str(dp_grid): -2000})
    manager._graphs["g"] = ("G", True, flow)
    with patch.object(manager, "_start_value_sequence") as start:
        await manager._execute_graph("g", "G", flow, {})
        assert start.call_count == 1
        await manager._execute_graph("g", "G", flow, {"h": {}})
        assert start.call_count == 1


def test_logic_behind_an_unknown_level_gets_none():
    # An invalid meter leaves the grid power unknown: None, not the last value.
    flow = FlowData.model_validate(
        {
            "nodes": [node("h", "hems_surplus", {"consumers": CONSUMERS}), node("cf", "change_filter", {})],
            "edges": [edge("h", "cf", "grid_power", "in")],
        }
    )
    state: dict = {}
    assert GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})["cf"]["out"] == -2500
    out = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": "bad"}})
    assert out["h"]["grid_power"] is None
    assert "__error__" not in out["cf"]  # no failed producer either


def test_a_wired_interlock_that_delivers_nothing_puts_the_block_into_the_safe_state():
    # Edge Detection sends nothing without an edge: plant_ok is wired, but unknown.
    flow = FlowData.model_validate(
        {
            "nodes": [node("ed", "edge_detect", {}), node("h", "hems_surplus", {"consumers": CONSUMERS})],
            "edges": [edge("ed", "h", "out", "plant_ok")],
        }
    )
    out = GraphExecutor(flow, {}).execute(input_overrides={"h": {"grid": -5000}})["h"]
    assert out["status"] == "plant_not_ok"
    assert out["c_boiler"] is False


def test_a_removed_consumer_with_a_dangling_edge_is_not_a_failed_producer():
    flow = FlowData.model_validate(
        {
            "nodes": [node("h", "hems_surplus", {"consumers": CONSUMERS}), node("n", "not", {}), node("cf", "change_filter", {})],
            "edges": [edge("h", "n", "c_gone", "in1"), edge("n", "cf", "out", "in")],
        }
    )
    out = GraphExecutor(flow, {}).execute(input_overrides={"h": {"grid": -5000}})
    assert "__error__" not in out["n"]


def test_logic_behind_a_failed_block_does_not_compute_with_stale_values():
    flow = FlowData.model_validate(
        {
            "nodes": [node("h", "hems_surplus", {"consumers": CONSUMERS}), node("n", "not", {})],
            "edges": [edge("h", "n", "c_boiler", "in1")],
        }
    )
    state: dict = {}
    GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -5000}})
    assert state["h"]["last_out"]["c_boiler"] is True
    with patch("obs.logic.executor.hems_surplus.evaluate", side_effect=RuntimeError("boom")):
        out = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -5000}})
    assert "__error__" in out["h"]
    assert out["n"]["out"] is True  # the ordinary missing-input default, not NOT(stale True)


def test_a_failed_block_is_a_failed_producer_for_a_change_filter():
    flow = FlowData.model_validate(
        {
            "nodes": [node("h", "hems_surplus", {"consumers": CONSUMERS}), node("n", "not", {}), node("cf", "change_filter", {})],
            "edges": [edge("h", "n", "c_boiler", "in1"), edge("n", "cf", "out", "in")],
        }
    )
    with patch("obs.logic.executor.hems_surplus.evaluate", side_effect=RuntimeError("boom")):
        out = GraphExecutor(flow, {}).execute(input_overrides={"h": {"grid": -5000}})
    assert "Missing upstream output" in out["n"]["__error__"]
    assert out["cf"].get("changed") is not True


@pytest.mark.asyncio
async def test_an_api_call_on_a_trigger_output_runs_once_per_firing():
    # Start a washing machine through an API: further meter telegrams during the
    # pulse must not send the request again.
    from tests.unit.test_host_check import _MockResponse

    dp_grid = uuid.uuid4()
    flow = _relay_flow(
        dp_grid,
        None,
        {"id": "wash", "mode": "trigger", "min_surplus_w": 1000, "on_delay_s": 0, "pulse_s": 60},
        [edge("h", "ac", "c_wash", "trigger")],
        [node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"})],
    )
    manager = _manager({str(dp_grid): -2000})
    manager._graphs["g"] = ("G", True, flow)
    with patch("obs.logic.manager.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        client.request = AsyncMock(return_value=_MockResponse(200, text="OK"))
        for _ in range(4):
            await manager._execute_graph("g", "G", flow, {"read": {"value": -2000}})
    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_saving_logs_no_failed_producer_for_logic_behind_the_block(caplog):
    dp_grid = uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(dp_grid)}),
                node("h", "hems_surplus", {"consumers": CONSUMERS}),
                node("n", "not", {}),
                node("cf", "change_filter", {}),
            ],
            "edges": [edge("read", "h", "value", "grid"), edge("h", "n", "c_boiler", "in1"), edge("n", "cf", "out", "in")],
        }
    )
    manager = _manager({str(dp_grid): -3000})
    manager._graphs["g"] = ("G", True, flow)
    await manager.initialize_graph("g")
    assert "Missing upstream output" not in caplog.text


@pytest.mark.asyncio
async def test_a_level_output_starts_a_value_sequence_on_its_rising_edge_only():
    # A level is no pulse: an unchanged True must not restart the sequence.
    dp_grid = uuid.uuid4()
    flow = _relay_flow(
        dp_grid,
        None,
        {"id": "boiler", "mode": "onoff", "power_w": 1000, "on_delay_s": 0},
        [edge("h", "seq", "c_boiler", "trigger")],
        [node("seq", "value_sequence", {"steps": []})],
    )
    manager = _manager({str(dp_grid): -2000})
    manager._graphs["g"] = ("G", True, flow)
    with patch.object(manager, "_start_value_sequence") as start:
        for _ in range(3):
            await manager._execute_graph("g", "G", flow, {"read": {"value": -2000}})
    assert start.call_count == 1


@pytest.mark.asyncio
async def test_an_event_run_wakes_the_block_early_for_a_pulse_its_loop_would_miss():
    flow = _flow(interval_s=3600)
    manager = _manager()
    manager._graphs["g"] = ("G", True, flow)
    now = hems_clock()
    manager._hems_planned_wake[("g", "h")] = now + 3000
    manager._hysteresis["g"] = {"h": {"next_wake": now + 1}}
    with patch.object(manager, "_hems_wake_once", new=AsyncMock()) as wake:
        manager._schedule_hems_early_wakes("g", flow, {"read": {"value": 1}})
        await asyncio.sleep(0)
        wake.assert_called_once_with("g", "h", now + 1)
        # an already pending wake at that time is not stacked …
        manager._schedule_hems_early_wakes("g", flow, {"read": {"value": 2}})
        await asyncio.sleep(0)
        assert wake.call_count == 1
        # … but an earlier one is added (the later one is never cancelled)
        manager._hysteresis["g"]["h"]["next_wake"] = now + 0.2
        manager._schedule_hems_early_wakes("g", flow, {"read": {"value": 3}})
        await asyncio.sleep(0)
        assert wake.call_count == 2
        assert manager._hems_wake_at[("g", "h")] == now + 0.2
    manager._cancel_hems_wakes("g")
    assert manager._hems_wake_tasks == {}
    assert manager._hems_wake_at == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "planned", "next_wake"),
    [
        ({"h": {}}, 3000, 1),  # the loop's own run plans itself
        ({"read": {"value": 1}}, None, 1),  # no loop running
        ({"read": {"value": 1}}, 3000, None),  # nothing pending
        ({"read": {"value": 1}}, 3000, 2999.8),  # not meaningfully earlier
        ({"read": {"value": 1}}, 3000, -5),  # already past
    ],
)
async def test_no_early_wake_when_the_loop_covers_it(overrides, planned, next_wake):
    flow = _flow()
    manager = _manager()
    now = hems_clock()
    if planned is not None:
        manager._hems_planned_wake[("g", "h")] = now + planned
    manager._hysteresis["g"] = {"h": {"next_wake": None if next_wake is None else now + next_wake}}
    manager._schedule_hems_early_wakes("g", flow, overrides)
    assert ("g", "h") not in manager._hems_wake_at


@pytest.mark.asyncio
async def test_the_early_wake_runs_the_block_unless_the_graph_is_gone_or_disabled():
    flow = _flow()
    manager = _manager()
    manager._execute_graph = AsyncMock(return_value={})
    manager._graphs["g"] = ("G", True, flow)
    manager._hems_wake_at[("g", "h")] = 0.0
    await manager._hems_wake_once("g", "h", 0.0)
    manager._execute_graph.assert_awaited_once_with("g", "G", flow, {"h": {}})
    assert ("g", "h") not in manager._hems_wake_at  # its own pending entry is cleared
    manager._hems_wake_at[("g", "h")] = 99.0  # a newer pending wake is left alone
    await manager._hems_wake_once("g", "h", 0.0)
    assert manager._hems_wake_at[("g", "h")] == 99.0
    manager._execute_graph.reset_mock()
    manager._graphs["g"] = ("G", False, flow)
    await manager._hems_wake_once("g", "h", 0)
    manager._graphs["g"] = ("G", True, FlowData(nodes=[], edges=[]))  # node deleted
    await manager._hems_wake_once("g", "h", 0)
    del manager._graphs["g"]
    await manager._hems_wake_once("g", "h", 0)
    assert manager._execute_graph.await_count == 0


@pytest.mark.asyncio
async def test_the_loop_records_when_it_will_wake_next():
    manager = _loop_manager()
    now = 1_000_000.0
    with patch("obs.logic.manager.hems_clock", return_value=now):
        await _run_loop(manager)
    assert manager._hems_planned_wake[("g", "h")] == now + 45


@pytest.mark.asyncio
async def test_invalidating_a_graph_forgets_its_planned_and_pending_wakes():
    manager = _manager()
    manager._hems_planned_wake[("g", "h")] = 1.0
    manager._hems_planned_wake[("other", "h")] = 2.0
    manager._hems_wake_at[("g", "h")] = 1.0
    sleeper = asyncio.create_task(asyncio.sleep(60))
    manager._hems_wake_tasks["g"] = {sleeper}
    manager.invalidate_cache("g")
    await asyncio.sleep(0)
    assert manager._hems_planned_wake == {("other", "h"): 2.0}
    assert manager._hems_wake_at == {}
    assert sleeper.cancelled()


@pytest.mark.asyncio
async def test_stop_cancels_every_pending_wake_but_a_reload_keeps_them():
    manager = _manager()
    manager._load_graphs = AsyncMock()
    manager._event_bus.unsubscribe = MagicMock()
    sleeper = asyncio.create_task(asyncio.sleep(60))
    manager._hems_wake_tasks["g"] = {sleeper}
    await manager.reload()  # saving any graph must not drop another graph's pulse end
    await asyncio.sleep(0)
    assert not sleeper.cancelled()
    await manager.stop()
    await asyncio.sleep(0)
    assert sleeper.cancelled()
    assert manager._hems_wake_tasks == {}


@pytest.mark.asyncio
async def test_an_early_wake_run_schedules_its_own_follow_up():
    flow = _flow(interval_s=3600)
    manager = _manager()
    now = hems_clock()
    manager._hems_planned_wake[("g", "h")] = now + 3000
    manager._hysteresis["g"] = {"h": {"next_wake": now + 4}}
    manager._hems_waking.add(("g", "h"))  # the wake run itself is executing
    with patch.object(manager, "_hems_wake_once", new=AsyncMock()) as wake:
        manager._schedule_hems_early_wakes("g", flow, {"h": {}})
        await asyncio.sleep(0)
    wake.assert_called_once_with("g", "h", now + 4)
    manager._cancel_hems_wakes()


@pytest.mark.asyncio
async def test_a_wake_run_is_marked_only_while_it_executes():
    flow = _flow()
    manager = _manager()
    seen: list[bool] = []

    async def fake_execute(*args, **kwargs):
        seen.append(("g", "h") in manager._hems_waking)
        raise RuntimeError("boom")

    manager._execute_graph = fake_execute
    manager._graphs["g"] = ("G", True, flow)
    with patch("obs.logic.manager.logger") as log:
        await manager._hems_wake_once("g", "h", 0)  # a failing run is logged, not raised into nowhere
    log.exception.assert_called_once()
    assert seen == [True]
    assert ("g", "h") not in manager._hems_waking


@pytest.mark.asyncio
async def test_a_firing_is_an_event_for_an_action_node_whatever_started_the_run():
    # The run is started by an unrelated Read Object; the block's cycle is due
    # and fires — the archive entry must still be written.
    dp_grid, dp_other = uuid.uuid4(), uuid.uuid4()
    flow = _relay_flow(
        dp_grid,
        None,
        {"id": "wash", "mode": "trigger", "min_surplus_w": 1000, "on_delay_s": 0, "pulse_s": 1},
        [edge("h", "ma", "c_wash", "trigger")],
        [
            node("other", "datapoint_read", {"datapoint_id": str(dp_other)}),
            node("ma", "message_archive", {"archive_id": "Alerts", "title": "Wash", "message": "started"}),
        ],
    )
    manager = _manager({str(dp_grid): -2000, str(dp_other): 1})
    manager._graphs["g"] = ("G", True, flow)
    service = MagicMock()
    service.record = AsyncMock(return_value={"id": "entry-1"})
    with patch("obs.message_archive.get_message_archive_service", return_value=service):
        await manager._execute_graph("g", "G", flow, {"other": {"value": 1}})
        service.record.assert_awaited_once()
        # a later unrelated run without a firing records nothing more
        await manager._execute_graph("g", "G", flow, {"other": {"value": 2}})
    service.record.assert_awaited_once()


def test_the_executor_runs_the_block_on_the_monotonic_hems_clock():
    flow = _flow()
    with patch("obs.logic.executor.hems_clock", return_value=12345.0):
        GraphExecutor(flow, (state := {})).execute(input_overrides={"h": {"grid": -2500}})
    assert state["h"]["last_cycle"] == 12345.0


@pytest.mark.asyncio
async def test_a_change_filter_behind_a_trigger_never_commits_the_missing_pulse_as_a_value():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _relay_flow(
        dp_grid,
        dp_out,
        {"id": "wash", "mode": "trigger", "min_surplus_w": 1000, "on_delay_s": 0, "pulse_s": 60},
        [edge("h", "n", "c_wash", "in1"), edge("n", "cf", "out", "in"), edge("cf", "w", "out", "value")],
        [node("n", "not", {}), node("cf", "change_filter", {}), node("w", "datapoint_write", {"datapoint_id": str(dp_out)})],
    )
    manager = _manager({str(dp_grid): -2000})
    manager._graphs["g"] = ("G", True, flow)
    await manager._execute_graph("g", "G", flow, {})  # fires: NOT → False
    assert manager._hysteresis["g"]["cf"]["value"] is False
    manager._event_bus.publish.reset_mock()
    for _ in range(2):  # pulse still running, trigger output absent
        await manager._execute_graph("g", "G", flow, {"read": {"value": -2000}})
    assert manager._hysteresis["g"]["cf"]["value"] is False
    # cf.out keeps carrying its held False; the inverted missing pulse (True) never appears
    assert True not in [c.args[0].value for c in manager._event_bus.publish.await_args_list if str(c.args[0].datapoint_id) == str(dp_out)]


def test_an_absent_level_output_is_a_failed_producer_for_a_change_filter():
    # A level the block has no value for (never sent, or lost).
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("h", "hems_surplus", {"consumers": CONSUMERS}),
                node("cmp", "compare", {"operator": "lt", "operand": "0"}),
                node("cf", "change_filter", {}),
            ],
            "edges": [edge("h", "cmp", "grid_power", "in1"), edge("cmp", "cf", "out", "in")],
        }
    )
    state: dict = {}
    GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    del state["h"]["last_out"]["grid_power"]  # e.g. a level whose value was lost
    out = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})  # inside the interval
    assert "Missing upstream output" in out["cmp"]["__error__"]


@pytest.mark.asyncio
async def test_cancelling_the_loop_never_interrupts_a_run_half_way():
    manager = _loop_manager()
    started, finished = asyncio.Event(), []

    async def slow_run(*args, **kwargs):
        started.set()
        await asyncio.sleep(0.05)
        finished.append(True)
        return {}

    manager._execute_graph = slow_run
    loop = asyncio.create_task(manager._hems_loop("g", "h"))
    await started.wait()
    loop.cancel()  # e.g. a save reloads the graphs
    with pytest.raises(asyncio.CancelledError):
        await loop
    await asyncio.sleep(0.1)
    assert finished == [True]


@pytest.mark.asyncio
async def test_cancelling_an_early_wake_never_interrupts_its_run_half_way():
    manager = _manager()
    manager._graphs["g"] = ("G", True, _flow())
    started, finished = asyncio.Event(), []

    async def slow_run(*args, **kwargs):
        started.set()
        await asyncio.sleep(0.05)
        finished.append(True)
        return {}

    manager._execute_graph = slow_run
    wake = asyncio.create_task(manager._hems_wake_once("g", "h", 0.0))
    await started.wait()
    wake.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wake
    await asyncio.sleep(0.1)
    assert finished == [True]


def test_invalidating_a_graph_asks_its_blocks_to_resend_their_levels():
    manager = _manager()
    flow = FlowData.model_validate({"nodes": [node("h", "hems_surplus", {"consumers": CONSUMERS}), node("n", "not", {})], "edges": []})
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"h": {"last_out": {}}}
    manager._hysteresis["g"]["h"]["config"] = "fingerprint"
    manager.invalidate_cache("g")
    assert manager._hysteresis["g"]["h"]["resend"] is True
    assert "config" not in manager._hysteresis["g"]["h"]  # forces the cycle that resends
    manager.invalidate_cache("unknown")  # nothing cached: nothing to do


def _write_flow(dp_grid, dp_out, write_data=None, hems_data=None, extra_edges=(), extra_nodes=()):
    return FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(dp_grid)}),
                node("h", "hems_surplus", {"consumers": CONSUMERS, **(hems_data or {})}),
                node("w", "datapoint_write", {"datapoint_id": str(dp_out), **(write_data or {})}),
                *extra_nodes,
            ],
            "edges": [edge("read", "h", "value", "grid"), edge("h", "w", "c_boiler", "value"), *extra_edges],
        }
    )


async def _writes(manager, flow, dp_out, runs):
    manager._event_bus.publish.reset_mock()
    for _ in range(runs):
        await manager._execute_graph("g", "G", flow, {"read": {"value": -5000}})
    return [c.args[0].value for c in manager._event_bus.publish.await_args_list if str(c.args[0].datapoint_id) == str(dp_out)]


@pytest.mark.asyncio
async def test_a_direct_write_is_not_repeated_while_the_level_is_unchanged():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _write_flow(dp_grid, dp_out)
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    assert await _writes(manager, flow, dp_out, 3) == [True]


@pytest.mark.asyncio
async def test_a_change_the_writes_throttle_dropped_is_written_later():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _write_flow(dp_grid, dp_out, {"throttle_value": 60})
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    # the Write Object wrote False a moment ago: its throttle blocks the change to True
    manager._node_state["g"] = {"w": {"last_write_val": False, "last_write_ts": datetime.now(UTC)}}
    assert await _writes(manager, flow, dp_out, 1) == []
    manager._node_state["g"]["w"]["last_write_ts"] = datetime.now(UTC) - timedelta(minutes=5)
    assert await _writes(manager, flow, dp_out, 2) == [True]  # unchanged level, but never written: now it is


@pytest.mark.asyncio
async def test_a_write_with_its_own_trigger_or_a_block_sending_every_cycle_writes_every_time():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _write_flow(
        dp_grid,
        dp_out,
        extra_nodes=[node("on", "const_value", {"value": "true", "data_type": "bool"})],
        extra_edges=[edge("on", "w", "value", "trigger")],
    )
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    assert await _writes(manager, flow, dp_out, 2) == [True, True]

    flow = _write_flow(dp_grid, dp_out, hems_data={"only_on_change": False})
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    assert len(await _writes(manager, flow, dp_out, 2)) == 2


def test_a_value_counts_as_written_only_with_the_same_type():
    from obs.logic.manager import _same_value

    assert _same_value(True, True)
    assert not _same_value(True, 1)
    assert not _same_value(50, 50.5)


@pytest.mark.asyncio
async def test_a_restarted_loop_resumes_at_its_planned_wake():
    manager = _loop_manager()
    manager._hems_planned_wake[("g", "h")] = hems_clock() + 100
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError()

    with patch("obs.logic.manager.asyncio.sleep", fake_sleep), pytest.raises(asyncio.CancelledError):
        await manager._hems_loop("g", "h")
    assert 99 < sleeps[0] <= 100
    manager._execute_graph.assert_not_awaited()  # nothing ran before the planned wake
