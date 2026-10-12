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
from datetime import UTC, datetime
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
    with patch("obs.logic.manager.datetime") as fake_datetime:
        fake_datetime.now.return_value.timestamp.return_value = now
        sleeps = await _run_loop(manager)
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_loop_never_sleeps_less_than_a_tenth_of_a_second():
    manager = _loop_manager()
    now = 1_000_000.0
    manager._hysteresis = {"g": {"h": {"next_wake": now + 0.01}}}
    with patch("obs.logic.manager.datetime") as fake_datetime:
        fake_datetime.now.return_value.timestamp.return_value = now
        assert await _run_loop(manager) == [0.1]


@pytest.mark.asyncio
async def test_loop_ignores_a_wake_time_the_run_did_not_refresh():
    # The node was skipped or failed, so next_wake is still the old, past value:
    # the loop must fall back to its interval instead of spinning every 0.1 s.
    manager = _loop_manager()
    now = 1_000_000.0
    manager._hysteresis = {"g": {"h": {"next_wake": now - 50}}}
    with patch("obs.logic.manager.datetime") as fake_datetime:
        fake_datetime.now.return_value.timestamp.return_value = now
        assert await _run_loop(manager) == [45.0]


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


def test_a_directly_wired_write_stays_silent_while_the_output_is_withheld():
    flow = FlowData.model_validate(
        {
            "nodes": [node("h", "hems_surplus", {"consumers": CONSUMERS}), node("w", "datapoint_write", {"datapoint_id": str(uuid.uuid4())})],
            "edges": [edge("h", "w", "c_boiler", "value")],
        }
    )
    state: dict = {}
    first = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    assert first["w"]["_write_value"] is True
    again = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    assert again["w"].get("_write_value") is None


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


def test_logic_behind_a_never_sent_output_gets_no_value():
    # A consumer added inside the control interval has sent nothing yet, so
    # there is no last value to compute with.
    consumers = [dict(CONSUMERS[0])]
    flow = FlowData.model_validate(
        {
            "nodes": [node("h", "hems_surplus", {"consumers": consumers}), node("n", "not", {})],
            "edges": [edge("h", "n", "c_new", "in1")],
        }
    )
    state: dict = {}
    GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    flow.nodes[0].data["consumers"] = [*consumers, {"id": "new", "mode": "onoff"}]
    out = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    assert "c_new" not in state["h"]["last_out"]
    assert out["n"]["out"] is True  # the executor's ordinary missing-input default


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


@pytest.mark.parametrize("write_filter", [{"only_on_change": True}, {"min_delta": 1}, {"throttle_value": 60}])
def test_a_write_with_its_own_filter_gets_the_level_on_every_run(write_filter):
    # Its own throttle may drop the one run the block sent a change on, so the
    # level must reach it again later.
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("h", "hems_surplus", {"consumers": CONSUMERS}),
                node("w", "datapoint_write", {"datapoint_id": str(uuid.uuid4()), **write_filter}),
            ],
            "edges": [edge("h", "w", "c_boiler", "value")],
        }
    )
    state: dict = {}
    GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    again = GraphExecutor(flow, state).execute(input_overrides={"h": {"grid": -2500}})
    assert again["w"]["_write_value"] is True


@pytest.mark.parametrize(
    ("write_filter", "own"),
    [
        ({"only_on_change": "yes"}, False),  # the manager honours only True / "true"
        ({"only_on_change": "true"}, True),
        ({"min_delta": 0}, False),
        ({"min_delta": "0"}, False),
        ({"min_delta": "x"}, False),
        ({"throttle_value": "5"}, True),
        ({}, False),
    ],
)
def test_a_write_counts_as_self_filtering_exactly_when_the_manager_filters_it(write_filter, own):
    from obs.logic.executor import _write_filters_itself

    assert _write_filters_itself(write_filter, 50) is own


@pytest.mark.parametrize(("value", "own"), [(50, True), ("50", True), ("active", False), (None, False), (float("nan"), False), (10**400, False)])
def test_a_minimum_change_only_filters_values_it_can_compare_as_numbers(value, own):
    from obs.logic.executor import _write_filters_itself

    assert _write_filters_itself({"min_delta": 1}, value) is own


@pytest.mark.asyncio
async def test_an_event_run_wakes_the_block_early_for_a_pulse_its_loop_would_miss():
    flow = _flow(interval_s=3600)
    manager = _manager()
    manager._graphs["g"] = ("G", True, flow)
    now = datetime.now(UTC).timestamp()
    manager._hems_planned_wake[("g", "h")] = now + 3000
    manager._hysteresis["g"] = {"h": {"next_wake": now + 1}}
    with patch.object(manager, "_hems_wake_once", new=AsyncMock()) as wake:
        manager._schedule_hems_early_wakes("g", flow, {"read": {"value": 1}})
        await asyncio.sleep(0)
        wake.assert_called_once()
        assert 0 < wake.call_args.args[2] <= 1
        # a second event run replaces the pending wake instead of stacking it
        manager._schedule_hems_early_wakes("g", flow, {"read": {"value": 2}})
        await asyncio.sleep(0)
        assert wake.call_count == 2
    manager._cron_tasks.pop(("g", "h:wake")).cancel()


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
    now = datetime.now(UTC).timestamp()
    if planned is not None:
        manager._hems_planned_wake[("g", "h")] = now + planned
    manager._hysteresis["g"] = {"h": {"next_wake": None if next_wake is None else now + next_wake}}
    manager._schedule_hems_early_wakes("g", flow, overrides)
    assert ("g", "h:wake") not in manager._cron_tasks


@pytest.mark.asyncio
async def test_the_early_wake_runs_the_block_unless_the_graph_is_gone_or_disabled():
    flow = _flow()
    manager = _manager()
    manager._execute_graph = AsyncMock(return_value={})
    manager._graphs["g"] = ("G", True, flow)
    await manager._hems_wake_once("g", "h", 0)
    manager._execute_graph.assert_awaited_once_with("g", "G", flow, {"h": {}})
    manager._graphs["g"] = ("G", False, flow)
    await manager._hems_wake_once("g", "h", 0)
    manager._graphs["g"] = ("G", True, FlowData(nodes=[], edges=[]))  # node deleted
    await manager._hems_wake_once("g", "h", 0)
    del manager._graphs["g"]
    await manager._hems_wake_once("g", "h", 0)
    assert manager._execute_graph.await_count == 1


@pytest.mark.asyncio
async def test_the_loop_records_when_it_will_wake_next():
    manager = _loop_manager()
    now = 1_000_000.0
    with patch("obs.logic.manager.datetime") as fake_datetime:
        fake_datetime.now.return_value.timestamp.return_value = now
        await _run_loop(manager)
    assert manager._hems_planned_wake[("g", "h")] == now + 45


def test_invalidating_a_graph_forgets_its_planned_wakes():
    manager = _manager()
    manager._hems_planned_wake[("g", "h")] = 1.0
    manager._hems_planned_wake[("other", "h")] = 2.0
    manager.invalidate_cache("g")
    assert manager._hems_planned_wake == {("other", "h"): 2.0}
