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
    with patch("obs.logic.manager.hems_surplus.interval_s", side_effect=RuntimeError("boom")):
        assert await _run_loop(manager) == [5.0]  # short: the safe state depends on this loop


@pytest.mark.asyncio
async def test_a_failing_run_is_logged_once_and_costs_no_cycle():
    manager = _loop_manager()
    manager._execute_graph = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("obs.logic.manager.logger") as log:
        assert await _run_loop(manager) == [45.0]
        await asyncio.sleep(0)
    log.error.assert_called_once()


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
        # the pulse end's False is no new event either: nothing is written
        manager._hysteresis["g"]["h"]["consumers"]["wash"]["pulse_until"] = 0.0
        await manager._execute_graph("g", "G", flow, {"h": {}})
        assert written() == []


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
        await asyncio.sleep(0)
    log.error.assert_called_once()
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
async def test_a_write_with_its_own_trigger_writes_every_time_and_every_cycle_sends_once_per_cycle():
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

    # "send every cycle": once per cycle, not on every run of the sheet
    flow = _write_flow(dp_grid, dp_out, hems_data={"only_on_change": False})
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    assert await _writes(manager, flow, dp_out, 2) == [True]
    manager._hysteresis["g"]["h"]["last_cycle"] -= 60  # the next cycle is due
    assert await _writes(manager, flow, dp_out, 1) == [True]


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


@pytest.mark.asyncio
async def test_a_pulse_end_the_writes_throttle_dropped_is_written_later():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(dp_grid)}),
                node("h", "hems_surplus", {"consumers": CONSUMERS}),
                node("w", "datapoint_write", {"datapoint_id": str(dp_out), "throttle_value": 5}),
            ],
            "edges": [edge("read", "h", "value", "grid"), edge("h", "w", "c_wash", "value")],
        }
    )
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    assert await _writes(manager, flow, dp_out, 1) == [True]
    manager._hysteresis["g"]["h"]["consumers"]["wash"]["pulse_until"] = 0.0  # the pulse ends …
    assert await _writes(manager, flow, dp_out, 1) == []  # … but the throttle drops its False
    manager._node_state["g"]["w"]["last_write_ts"] = datetime.now(UTC) - timedelta(minutes=1)
    assert await _writes(manager, flow, dp_out, 2) == [False]  # written later, once


@pytest.mark.asyncio
async def test_a_restarted_loop_never_runs_the_sheet_alongside_a_run_still_going():
    manager = _loop_manager()
    running, overlaps, release = 0, [], asyncio.Event()

    async def slow_run(*args, **kwargs):
        nonlocal running
        running += 1
        overlaps.append(running)
        await release.wait()
        running -= 1
        return {}

    manager._execute_graph = slow_run
    first = asyncio.create_task(manager._hems_loop("g", "h"))
    await asyncio.sleep(0.01)
    first.cancel()  # a save reloads: the loop is cancelled, its run goes on
    second = asyncio.create_task(manager._hems_loop("g", "h"))
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.sleep(0.01)
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)
    assert max(overlaps) == 1


@pytest.mark.asyncio
async def test_a_cancelled_early_wake_stays_marked_until_its_run_has_finished():
    manager = _manager()
    manager._graphs["g"] = ("G", True, _flow())
    release = asyncio.Event()

    async def slow_run(*args, **kwargs):
        await release.wait()
        return {}

    manager._execute_graph = slow_run
    wake = asyncio.create_task(manager._hems_wake_once("g", "h", 0.0))
    await asyncio.sleep(0.01)
    wake.cancel()
    await asyncio.gather(wake, return_exceptions=True)
    assert ("g", "h") in manager._hems_waking  # its run still schedules the follow-up wake
    release.set()
    await asyncio.sleep(0.01)
    assert ("g", "h") not in manager._hems_waking
    assert ("g", "h") not in manager._hems_runs


@pytest.mark.asyncio
async def test_a_finished_run_leaves_a_newer_run_registered():
    manager = _manager()
    old, new = asyncio.get_running_loop().create_future(), asyncio.get_running_loop().create_future()
    manager._hems_runs[("g", "h")] = new
    old.cancel()
    manager._hems_run_finished(("g", "h"), old, waking=False)
    assert manager._hems_runs[("g", "h")] is new


@pytest.mark.asyncio
async def test_two_waiters_never_start_runs_in_parallel():
    manager = _manager()
    manager._graphs["g"] = ("G", True, _flow())
    running, peak, release = 0, 0, asyncio.Event()

    async def slow_run(*args, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1
        return {}

    manager._execute_graph = slow_run
    first = asyncio.create_task(manager._hems_run("g", "h", "G", _flow(), waking=False))
    await asyncio.sleep(0.01)
    waiters = [asyncio.create_task(manager._hems_run("g", "h", "G", _flow(), waking=w)) for w in (False, True)]
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(first, *waiters)
    assert peak == 1


@pytest.mark.asyncio
async def test_stop_lets_a_run_in_flight_finish():
    manager = _manager()
    manager._event_bus.unsubscribe = MagicMock()
    finished = []

    async def slow_run():
        await asyncio.sleep(0.02)
        finished.append(True)

    manager._hems_runs[("g", "h")] = asyncio.ensure_future(slow_run())
    await manager.stop()
    assert finished == [True]


@pytest.mark.asyncio
async def test_after_stop_no_new_early_wake_is_scheduled():
    flow = _flow(interval_s=3600)
    manager = _manager()
    manager._event_bus.unsubscribe = MagicMock()
    await manager.stop()
    now = hems_clock()
    manager._hems_planned_wake[("g", "h")] = now + 3000
    manager._hysteresis["g"] = {"h": {"next_wake": now + 1}}
    manager._schedule_hems_early_wakes("g", flow, {"read": {"value": 1}})
    assert manager._hems_wake_tasks == {}
    manager._load_app_config = AsyncMock()
    manager._load_graphs = AsyncMock()
    manager._event_bus.subscribe = MagicMock()
    await manager.start()
    assert manager._hems_stopping is False
    await manager.stop()


@pytest.mark.asyncio
async def test_a_trigger_edge_to_a_write_is_a_pulse_when_the_block_sends_every_cycle():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(dp_grid)}),
                node("h", "hems_surplus", {"consumers": CONSUMERS, "only_on_change": False}),
                node("w", "datapoint_write", {"datapoint_id": str(dp_out)}),
            ],
            "edges": [edge("read", "h", "value", "grid"), edge("h", "w", "c_wash", "value")],
        }
    )
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    assert await _writes(manager, flow, dp_out, 2) == [True]  # once per firing, not per run


@pytest.mark.asyncio
async def test_a_level_whose_write_failed_is_tried_again():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _write_flow(dp_grid, dp_out)
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)

    async def flaky(event):
        if str(event.datapoint_id) == str(dp_out) and not flaky.failed:
            flaky.failed = True
            raise RuntimeError("bus down")

    flaky.failed = False
    manager._event_bus.publish.side_effect = flaky
    assert await _writes(manager, flow, dp_out, 1) == [True]  # attempted, failed
    assert "last_write_val" not in manager._node_state["g"]["w"]
    assert await _writes(manager, flow, dp_out, 1) == [True]  # tried again, written
    assert await _writes(manager, flow, dp_out, 1) == []  # and then not repeated


@pytest.mark.asyncio
async def test_a_failed_publish_of_an_ordinary_write_keeps_its_filter_state():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _write_flow(dp_grid, dp_out, hems_data={"only_on_change": False})
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    manager._node_state["g"] = {"w": {"last_write_val": False}}
    manager._event_bus.publish.side_effect = RuntimeError("bus down")
    await _writes(manager, flow, dp_out, 1)
    assert manager._node_state["g"]["w"]["last_write_val"] is True  # unchanged behaviour outside HEMS levels


@pytest.mark.asyncio
async def test_a_run_made_obsolete_by_a_save_leaves_the_block_as_it_was():
    from obs.logic.manager import _ObsoleteGraphExecution

    flow = _flow()
    manager = _manager()
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {}

    async def obsolete_run(*args, run_tag=None, **kwargs):
        executor = GraphExecutor(flow, manager._hysteresis["g"])
        executor.run_tag = run_tag
        executor.execute(input_overrides={"h": {"grid": -5000}})  # fires the trigger …
        assert manager._hysteresis["g"]["h"]["consumers"]["wash"]["armed"] is False
        raise _ObsoleteGraphExecution  # … then a save made the run obsolete before any write

    manager._execute_graph_impl = obsolete_run
    assert await manager._execute_graph("g", "G", flow, {}) == {}
    assert set(manager._hysteresis["g"]["h"]) <= {"held", "last_seen"}  # its decisions are gone, the meter readings stay


def test_an_obsolete_run_is_not_undone_over_a_newer_run():
    from obs.logic.executor import undo_hems_run

    flow = _flow()
    state: dict = {}
    mine, theirs = object(), object()
    for tag in (mine, theirs):
        executor = GraphExecutor(flow, state)
        executor.run_tag = tag
        executor.execute(input_overrides={"h": {"grid": -5000}})
    undo_hems_run(state["h"], mine)  # another run ticked after it: keep the newer state
    assert state["h"]["consumers"]["wash"]["armed"] is False
    undo_hems_run(state["h"], theirs)
    assert state["h"]["consumers"]["wash"]["armed"] is False  # undone to after "mine", which fired


@pytest.mark.asyncio
async def test_a_failed_write_restores_the_value_written_before():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _write_flow(dp_grid, dp_out)
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    assert await _writes(manager, flow, dp_out, 1) == [True]
    written_at = manager._node_state["g"]["w"]["last_write_ts"]
    manager._event_bus.publish.side_effect = RuntimeError("bus down")
    manager._hysteresis["g"]["h"]["last_out"]["c_boiler"] = False  # a new level that fails to publish
    manager._hysteresis["g"]["h"]["last_cycle"] = hems_clock()  # no cycle: the level stays False
    await _writes(manager, flow, dp_out, 1)
    assert manager._node_state["g"]["w"]["last_write_val"] is True
    assert manager._node_state["g"]["w"]["last_write_ts"] == written_at


@pytest.mark.asyncio
async def test_a_notification_on_a_trigger_is_sent_per_firing_not_at_the_pulse_end():
    dp_grid = uuid.uuid4()
    flow = _relay_flow(
        dp_grid,
        None,
        {"id": "wash", "mode": "trigger", "min_surplus_w": 1000, "on_delay_s": 0, "pulse_s": 1},
        [edge("h", "ma", "c_wash", "message")],
        [node("ma", "message_archive", {"archive_id": "Alerts", "title": "Wash"})],
    )
    manager = _manager({str(dp_grid): -2000})
    manager._graphs["g"] = ("G", True, flow)
    service = MagicMock()
    service.record = AsyncMock(return_value={"id": "entry-1"})
    with patch("obs.message_archive.get_message_archive_service", return_value=service):
        await manager._execute_graph("g", "G", flow, {})
        manager._hysteresis["g"]["h"]["consumers"]["wash"]["pulse_until"] = 0.0  # the pulse ends
        await manager._execute_graph("g", "G", flow, {"h": {}})
    assert service.record.await_count == 1


@pytest.mark.asyncio
async def test_planned_wakes_are_forgotten_on_stop_and_while_idle():
    manager = _loop_manager(enabled=False)
    manager._hems_planned_wake[("g", "h")] = 1.0
    await _run_loop(manager)
    assert ("g", "h") not in manager._hems_planned_wake
    manager._hems_planned_wake[("x", "y")] = 1.0
    manager._event_bus.unsubscribe = MagicMock()
    await manager.stop()
    assert manager._hems_planned_wake == {}


@pytest.mark.asyncio
async def test_the_off_of_a_removed_consumer_reaches_a_throttled_write_later():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _write_flow(dp_grid, dp_out, {"throttle_value": 60})
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)
    assert await _writes(manager, flow, dp_out, 1) == [True]
    flow.nodes[1].data["consumers"] = [CONSUMERS[1]]  # the boiler is removed; its edge stays
    assert await _writes(manager, flow, dp_out, 1) == []  # its one False is dropped by the throttle
    manager._node_state["g"]["w"]["last_write_ts"] = datetime.now(UTC) - timedelta(minutes=5)
    assert await _writes(manager, flow, dp_out, 2) == [False]  # but written once the throttle allows


@pytest.mark.asyncio
async def test_a_pulse_end_does_not_bypass_rising_edge_dedup_of_an_action():
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
        manager._hysteresis["g"]["h"]["consumers"]["wash"]["pulse_until"] = 0.0
        await manager._execute_graph("g", "G", flow, {"h": {}})
    assert start.call_count == 1


@pytest.mark.asyncio
async def test_a_loop_restarted_during_a_run_plans_its_next_wake_after_it():
    manager = _loop_manager()
    release = asyncio.Event()
    calls = []

    async def slow_run(*args, **kwargs):
        calls.append(1)
        await release.wait()
        return {}

    manager._execute_graph = slow_run
    first = asyncio.create_task(manager._hems_loop("g", "h"))
    await asyncio.sleep(0.01)
    first.cancel()  # saving another sheet reloads mid-run; this sheet's plan is kept
    manager._hems_planned_wake[("g", "h")] = hems_clock() - 1
    second = asyncio.create_task(manager._hems_loop("g", "h"))
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.sleep(0.02)
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)
    assert calls == [1]  # the restarted loop did not run the sheet again right away
    assert manager._hems_planned_wake[("g", "h")] > hems_clock() + 40  # it planned one interval (45 s) on


@pytest.mark.asyncio
async def test_a_restarted_loop_plans_from_the_runs_next_wake_even_if_the_graph_is_gone():
    manager = _loop_manager()
    release = asyncio.Event()

    async def slow_run(*args, **kwargs):
        await release.wait()
        manager._hysteresis.setdefault("g", {})["h"] = {"next_wake": hems_clock() + 3}
        return {}

    manager._execute_graph = slow_run
    first = asyncio.create_task(manager._hems_loop("g", "h"))
    await asyncio.sleep(0.01)
    first.cancel()
    del manager._graphs["g"]  # deleted meanwhile
    manager._hems_planned_wake[("g", "h")] = hems_clock() - 1  # a plan from before the reload
    second = asyncio.create_task(manager._hems_loop("g", "h"))
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.sleep(0.02)
    planned = manager._hems_planned_wake[("g", "h")]
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)
    assert planned <= hems_clock() + 3  # the pending wake, not the 30 s default


@pytest.mark.asyncio
async def test_an_obsolete_run_that_already_acted_keeps_its_ticks():
    from obs.logic.manager import _ObsoleteGraphExecution

    flow = _flow()
    manager = _manager()
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {}

    async def acted_then_obsolete(*args, run_tag=None, **kwargs):
        executor = GraphExecutor(flow, manager._hysteresis["g"])
        executor.run_tag = run_tag
        executor.execute(input_overrides={"h": {"grid": -5000}})  # fires the trigger
        manager._hems_acted_runs.add(run_tag)  # e.g. the API call already went out
        raise _ObsoleteGraphExecution

    manager._execute_graph_impl = acted_then_obsolete
    await manager._execute_graph("g", "G", flow, {})
    assert manager._hysteresis["g"]["h"]["consumers"]["wash"]["armed"] is False  # no second start
    assert manager._hems_acted_runs == set()


@pytest.mark.asyncio
async def test_a_run_whose_first_pass_failed_undoes_its_ticks():
    flow = FlowData.model_validate(
        {
            "nodes": [node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}), node("h", "hems_surplus", {"consumers": CONSUMERS})],
            "edges": [edge("read", "h", "value", "grid")],
        }
    )
    manager = _manager()
    manager._graphs["g"] = ("G", True, flow)
    original = GraphExecutor.execute

    def tick_then_fail(self, *args, **kwargs):
        original(self, input_overrides={"h": {"grid": -5000}})  # the block ticks and fires …
        raise RuntimeError("boom")  # … then the pass fails before anything is published

    with patch.object(GraphExecutor, "execute", tick_then_fail):
        assert await manager._execute_graph("g", "G", flow, {}) == {}
    assert set(manager._hysteresis["g"]["h"]) <= {"held", "last_seen"}


def test_a_run_that_acted_is_not_undone_after_an_error():
    manager = _manager()
    flow = _flow()
    manager._hysteresis["g"] = {"h": {"consumers": {}, "_run": (tag := object()), "_undo": {}}}
    manager._hems_acted_runs.add(tag)
    manager._undo_hems_run("g", flow, tag)
    assert manager._hysteresis["g"]["h"]["consumers"] == {}
    manager._undo_hems_run("g", flow, None)  # an untagged run has nothing to undo


@pytest.mark.asyncio
async def test_a_loop_restarted_after_its_own_sheet_was_saved_runs_the_new_config_right_away():
    manager = _loop_manager()
    release = asyncio.Event()
    calls = []

    async def slow_run(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            await release.wait()
        return {}

    manager._execute_graph = slow_run
    first = asyncio.create_task(manager._hems_loop("g", "h"))
    await asyncio.sleep(0.01)
    first.cancel()
    manager._hems_planned_wake.pop(("g", "h"), None)  # what invalidate_cache does on this sheet's save
    second = asyncio.create_task(manager._hems_loop("g", "h"))
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.sleep(0.02)
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)
    assert calls == [1, 1]  # the new configuration ran right after the old run


def test_a_run_tag_survives_deep_copies_of_the_state():
    import copy

    from obs.logic.executor import undo_hems_run

    flow = _flow()
    state: dict = {}
    executor = GraphExecutor(flow, state)
    executor.run_tag = 7
    executor.execute(input_overrides={"h": {"grid": -5000}})
    copied = copy.deepcopy(state)  # as a replay pass works on
    again = GraphExecutor(flow, copied)
    again.run_tag = 7
    again.execute(input_overrides={"h": {"grid": -5000}})
    undo_hems_run(copied["h"], 7)
    assert set(copied["h"]) <= {"held", "last_seen"}  # back to before the run's first tick, not to after it


@pytest.mark.asyncio
async def test_a_run_cancelled_before_it_acted_undoes_its_ticks():
    flow = _flow()
    manager = _manager()
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {}

    async def tick_then_cancelled(*args, run_tag=None, **kwargs):
        executor = GraphExecutor(flow, manager._hysteresis["g"])
        executor.run_tag = run_tag
        executor.execute(input_overrides={"h": {"grid": -5000}})
        raise asyncio.CancelledError

    manager._execute_graph_impl = tick_then_cancelled
    with pytest.raises(asyncio.CancelledError):
        await manager._execute_graph("g", "G", flow, {})
    assert set(manager._hysteresis["g"]["h"]) <= {"held", "last_seen"}


def test_undoing_a_run_keeps_what_the_meter_reported():
    from obs.logic.executor import undo_hems_run

    state = {
        "_run": 3,
        "_undo": {"last_seen": {"grid": 1.0}, "consumers": {}},
        "last_seen": {"grid": 9.0},
        "held": {"grid": [-1, 9.0]},
        "consumers": {"x": {}},
    }
    undo_hems_run(state, 3)
    assert state == {"last_seen": {"grid": 9.0}, "held": {"grid": [-1, 9.0]}, "consumers": {}}


@pytest.mark.asyncio
async def test_a_completed_run_leaves_no_undo_snapshot_behind():
    flow = _flow()
    manager = _manager()
    manager._graphs["g"] = ("G", True, flow)
    await manager._execute_graph("g", "G", flow, {"h": {"grid": -5000}})
    assert "_undo" not in manager._hysteresis["g"]["h"]
    assert "_run" not in manager._hysteresis["g"]["h"]


def test_forgetting_another_runs_undo_leaves_it_alone():
    from obs.logic.executor import forget_hems_undo

    state = {"_run": 2, "_undo": {}}
    forget_hems_undo(state, 1)
    assert state == {"_run": 2, "_undo": {}}


@pytest.mark.asyncio
async def test_a_run_cancelled_after_its_writes_keeps_its_ticks():
    dp_grid, dp_out = uuid.uuid4(), uuid.uuid4()
    flow = _write_flow(dp_grid, dp_out)
    manager = _manager({str(dp_grid): -5000})
    manager._graphs["g"] = ("G", True, flow)

    async def cancel_after_write(event):
        raise asyncio.CancelledError  # e.g. a reload cancels the loop while it publishes

    manager._event_bus.publish.side_effect = cancel_after_write
    with pytest.raises(asyncio.CancelledError):
        await manager._execute_graph("g", "G", flow, {})
    assert manager._hysteresis["g"]["h"]["last_out"]["c_boiler"] is True  # the run had started writing


@pytest.mark.asyncio
@pytest.mark.parametrize("other_state", [True, False])
async def test_a_restored_node_state_never_brings_back_hems_state(other_state):
    flow = FlowData.model_validate({"nodes": [node("h", "hems_surplus", {"consumers": CONSUMERS}), node("cf", "change_filter", {})], "edges": []})
    saved = {"h": {"last_cycle": 123456.0, "consumers": {"boiler": {"on": True}}}}
    if other_state:
        saved["cf"] = {"value": 1}
    manager = _manager()
    manager._db.fetchall = AsyncMock(
        return_value=[{"id": "g", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": json.dumps(saved)}]
    )
    await manager._load_graphs()
    assert "h" not in manager._hysteresis.get("g", {})
    assert ("cf" in manager._hysteresis.get("g", {})) is other_state


@pytest.mark.asyncio
async def test_stop_cancels_a_run_still_going_after_its_bound():
    manager = _manager()
    manager._event_bus.unsubscribe = MagicMock()
    hung = asyncio.ensure_future(asyncio.sleep(60))
    manager._hems_runs[("g", "h")] = hung
    with patch("obs.logic.manager._HEMS_STOP_TIMEOUT_S", 0.01):
        await manager.stop()
    await asyncio.sleep(0)
    assert hung.cancelled()


@pytest.mark.asyncio
async def test_a_reload_drops_the_plans_of_sheets_that_are_gone():
    manager = _manager()
    manager._load_graphs = AsyncMock()
    manager._graphs["kept"] = ("K", True, _flow())
    manager._hems_planned_wake.update({("kept", "h"): 1.0, ("gone", "h"): 2.0})
    manager._hems_wake_at[("gone", "h")] = 3.0
    await manager.reload()
    assert manager._hems_planned_wake == {("kept", "h"): 1.0}
    assert manager._hems_wake_at == {}


@pytest.mark.asyncio
async def test_a_malformed_node_state_restores_nothing():
    flow = _flow()
    manager = _manager()
    manager._db.fetchall = AsyncMock(
        return_value=[{"id": "g", "name": "G", "enabled": 1, "flow_data": flow.model_dump_json(), "node_state": "[1, 2]"}]
    )
    await manager._load_graphs()
    assert "g" not in manager._hysteresis


@pytest.mark.asyncio
async def test_a_missing_pulse_does_not_reach_the_block_as_a_value():
    # change_filter.changed -> plant_ok: on a run where the filter did not fire,
    # its False is a no-pulse placeholder, not "plant not OK".
    dp_grid, dp_plant = uuid.uuid4(), uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(dp_grid)}),
                node("plant", "datapoint_read", {"datapoint_id": str(dp_plant)}),
                node("cf", "change_filter", {}),
                node("h", "hems_surplus", {"consumers": CONSUMERS}),
            ],
            "edges": [edge("read", "h", "value", "grid"), edge("plant", "cf", "value", "in"), edge("cf", "h", "changed", "plant_ok")],
        }
    )
    manager = _manager({str(dp_grid): -5000, str(dp_plant): 1})
    manager._graphs["g"] = ("G", True, flow)
    await manager._execute_graph("g", "G", flow, {"plant": {"value": 1}})  # the filter fires: plant_ok True
    assert manager._hysteresis["g"]["h"]["status"] == "ok"
    await manager._execute_graph("g", "G", flow, {"read": {"value": -5000}})  # unrelated run
    assert manager._hysteresis["g"]["h"]["status"] == "ok"


@pytest.mark.asyncio
async def test_a_reload_drops_emptied_wake_task_sets():
    manager = _manager()
    manager._load_graphs = AsyncMock()
    manager._hems_wake_tasks.update({"old": set(), "busy": {MagicMock()}})
    await manager.reload()
    assert set(manager._hems_wake_tasks) == {"busy"}


def test_a_failed_tick_keeps_the_executors_undo_keys(monkeypatch):
    from obs.logic import hems_surplus as hs

    state = {"_run": 5, "_undo": {"x": 1}}
    monkeypatch.setattr(hs, "_evaluate", MagicMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        hs.evaluate({"consumers": []}, {"grid": 0}, state, 1.0)
    assert state == {"_run": 5, "_undo": {"x": 1}}
