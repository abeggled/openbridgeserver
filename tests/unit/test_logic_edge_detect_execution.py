"""Manager-level execution behaviour of the ``edge_detect`` function block.

Assertions about the node definition live in
``tests/unit/logic/nodes/logic/test_edge_detect.py``; the dispatcher branch is
covered by ``TestEdgeDetectNode`` in ``tests/unit/test_executor.py``. This file
covers the part neither of those can show: what a real graph run publishes to a
downstream Write Object.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic.manager import LogicManager
from obs.logic.models import FlowData
from tests.unit.conftest import edge, node


def _manager(values: dict[str, object] | None = None) -> LogicManager:
    """Manager whose registry seeds only the given DataPoint ids.

    The real registry creates an empty ValueState (value=None) as soon as a
    DataPoint is registered, long before any adapter writes a real value — a
    bare MagicMock would hand out a truthy attribute instead and hide the
    unseeded case entirely.
    """
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(data_type="UNKNOWN")
    seeded = values or {}
    registry.get_value.side_effect = lambda dp_id: SimpleNamespace(value=seeded.get(str(dp_id)), ts=None)
    return LogicManager(AsyncMock(), AsyncMock(), registry)


def _write_flow(target: uuid.UUID, data: dict | None = None) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [node("ed", "edge_detect", data or {}), node("w", "datapoint_write", {"datapoint_id": str(target)})],
            "edges": [edge("ed", "w", "out", "value")],
        }
    )


async def _run(manager: LogicManager, flow: FlowData, value: object) -> object | None:
    """Execute one graph run and return the value written, or None."""
    before = manager._event_bus.publish.await_count
    await manager._execute_graph("g", "G", flow, {"ed": {"in": value}})
    if manager._event_bus.publish.await_count == before:
        return None
    return manager._event_bus.publish.await_args.args[0].value


@pytest.mark.asyncio
async def test_write_object_is_driven_only_by_edges_not_by_every_run():
    manager = _manager()
    flow = _write_flow(uuid.uuid4())
    manager._graphs["g"] = ("G", True, flow)

    # First value only seeds the level — a save/startup must not actuate.
    assert await _run(manager, flow, False) is None
    assert await _run(manager, flow, True) is True
    # Repeated identical level: "out" stays absent, so nothing is written.
    assert await _run(manager, flow, True) is None
    assert await _run(manager, flow, False) is False
    assert await _run(manager, flow, False) is None


@pytest.mark.asyncio
async def test_falling_set_to_trigger_only_writes_on_the_rising_edge():
    manager = _manager()
    flow = _write_flow(uuid.uuid4(), {"on_falling": "trigger"})
    manager._graphs["g"] = ("G", True, flow)

    assert await _run(manager, flow, False) is None
    assert await _run(manager, flow, True) is True
    assert await _run(manager, flow, False) is None


@pytest.mark.asyncio
async def test_remembered_level_is_persisted_so_a_restart_resumes_edgeless():
    manager = _manager()
    flow = _write_flow(uuid.uuid4())
    manager._graphs["g"] = ("G", True, flow)

    await _run(manager, flow, True)

    assert manager._hysteresis["g"]["ed"] == {"value": True}
    manager._db.execute_and_commit.assert_awaited()


@pytest.mark.asyncio
async def test_unrelated_branch_run_does_not_corrupt_the_remembered_level():
    """Issue #1090: on an event-driven run, a Change Filter on an *unrelated*
    branch reports changed=False because it was not re-evaluated with fresh
    data, not because its signal went low. Committing that no-pulse
    placeholder as a level would make the next real pulse look like a rising
    edge and publish a write that never happened."""
    manager = _manager()
    dp_a, dp_b, target = str(uuid.uuid4()), str(uuid.uuid4()), uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("rA", "datapoint_read", {"datapoint_id": dp_a}),
                node("cf", "change_filter"),
                node("ed", "edge_detect"),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
                node("rB", "datapoint_read", {"datapoint_id": dp_b}),
            ],
            "edges": [
                edge("rA", "cf", "value", "in"),
                edge("cf", "ed", "changed", "in"),
                edge("ed", "w", "out", "value"),
            ],
        }
    )
    manager._graphs["g"] = ("G", True, flow)

    await manager._execute_graph("g", "G", flow, {"rA": {"value": 1, "changed": True}})
    assert manager._hysteresis["g"]["ed"] == {"value": True}

    # A run driven purely by the other branch must leave the level alone.
    await manager._execute_graph("g", "G", flow, {"rB": {"value": 9, "changed": True}})

    assert manager._hysteresis["g"]["ed"] == {"value": True}
    manager._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_unseeded_read_object_does_not_seed_a_level_or_fire_on_first_value():
    """A Read Object without a value emits None. That is "nothing arrived",
    not a low level — otherwise the first real value would look like a rising
    edge and write, contradicting "the first value produces no edge"."""
    manager = _manager()
    dp, target = str(uuid.uuid4()), uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("r", "datapoint_read", {"datapoint_id": dp}),
                node("ed", "edge_detect"),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [edge("r", "ed", "value", "in"), edge("ed", "w", "out", "value")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)

    # Graph run while the Read Object still has no value at all.
    await manager._execute_graph("g", "G", flow, {})
    assert "ed" not in manager._hysteresis.get("g", {})

    # The first real value only seeds the level — no edge, no write.
    await manager._execute_graph("g", "G", flow, {"r": {"value": True, "changed": True}})

    assert manager._hysteresis["g"]["ed"] == {"value": True}
    manager._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_level_survives_an_unrelated_run_without_a_downstream_write_object():
    """Same corruption as above, but on a sheet whose only stateful node is the
    Edge Detect itself. A downstream Write Object would already force the
    pre-execute snapshot that the correction pass restores state from; without
    one, only Edge Detect's own membership in the manager's stateful-relay set
    can, so this is what pins that registration down."""
    dp_a, dp_b = str(uuid.uuid4()), str(uuid.uuid4())
    # Both Read Objects seeded: no unseeded-read rollback, no async node and no
    # Write Object, so nothing else asks for the snapshot.
    manager = _manager({dp_a: 1, dp_b: 9})
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("rA", "datapoint_read", {"datapoint_id": dp_a}),
                node("cf", "change_filter"),
                node("ed", "edge_detect"),
                node("rB", "datapoint_read", {"datapoint_id": dp_b}),
            ],
            "edges": [edge("rA", "cf", "value", "in"), edge("cf", "ed", "changed", "in")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)

    await manager._execute_graph("g", "G", flow, {"rA": {"value": 1, "changed": True}})
    assert manager._hysteresis["g"]["ed"] == {"value": True}

    await manager._execute_graph("g", "G", flow, {"rB": {"value": 9, "changed": True}})

    assert manager._hysteresis["g"]["ed"] == {"value": True}


@pytest.mark.asyncio
async def test_consecutive_edges_each_retrigger_an_async_action():
    """Both trigger outputs combined through OR to run one action on either
    edge: each edge is a discrete event, so the second must not be swallowed by
    the async node's rising-edge deduplication as a sustained trigger."""
    manager = _manager()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("ed", "edge_detect"),
                node("o", "or", {"input_count": 2}),
                node("hc", "host_check", {"host": "127.0.0.1"}),
            ],
            "edges": [
                edge("ed", "o", "rising", "in1"),
                edge("ed", "o", "falling", "in2"),
                edge("o", "hc", "out", "trigger"),
            ],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": False}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with (
        patch("obs.api.v1.websocket.get_ws_manager", return_value=ws),
        patch("obs.logic.manager._ping_host", new=AsyncMock(return_value=(True, 1.0))) as ping,
    ):
        rising = await manager._execute_graph("g", "G", flow, {"ed": {"in": True}})
        falling = await manager._execute_graph("g", "G", flow, {"ed": {"in": False}})

    # The OR reports a sustained True across both runs — only the pulse
    # provenance can tell the two edges apart.
    assert rising["o"]["out"] is True
    assert falling["o"]["out"] is True
    assert ping.await_count == 2


@pytest.mark.asyncio
async def test_a_tick_without_an_edge_does_not_bypass_the_async_dedup():
    """The mirror of the case above: a cron tick reaching Edge Detection must
    not count as a pulse having propagated through it when no edge occurred.
    host_check's trigger is held true by a constant, but a sustained value
    alone must not fire an action on a tick where nothing happened — verified
    to match a non-pulsing Change Filter in the same wiring."""
    manager = _manager()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("cron", "timer_cron", {"cron": "* * * * *"}),
                node("ed", "edge_detect"),
                node("const", "const_value", {"value": "1", "data_type": "bool"}),
                node("o", "or", {"input_count": 2}),
                node("hc", "host_check", {"host": "127.0.0.1"}),
            ],
            "edges": [
                # Into "reset", a trigger port — a cron pulse propagates
                # through it, so the traversal goes on to look at whether the
                # edge outputs carry a pulse of their own.
                edge("cron", "ed", "trigger", "reset"),
                edge("ed", "o", "rising", "in1"),
                edge("const", "o", "value", "in2"),
                edge("o", "hc", "out", "trigger"),
            ],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": True}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with (
        patch("obs.api.v1.websocket.get_ws_manager", return_value=ws),
        patch("obs.logic.manager._ping_host", new=AsyncMock(return_value=(True, 1.0))) as ping,
    ):
        first = await manager._execute_graph("g", "G", flow, {"cron": {"trigger": True}})
        await manager._execute_graph("g", "G", flow, {"cron": {"trigger": True}})

    assert first["ed"] == {"rising": False, "falling": False}
    # Sustained trigger with no pulse behind it: the action does not run.
    assert ping.await_count == 0


@pytest.mark.asyncio
async def test_a_held_node_does_not_commit_an_unresolved_async_placeholder():
    """host_check emits reachable=False as a placeholder until it has actually
    run. Committing that as a level would report a falling edge that never
    happened — and the action it drives runs irreversibly, long before the
    replay could correct it."""
    manager = _manager()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("c", "const_value", {"value": "true", "data_type": "bool"}),
                node("a", "host_check", {"host": "source"}),
                node("ed", "edge_detect", {"on_rising": "off", "on_falling": "trigger"}),
                node("b", "host_check", {"host": "downstream"}),
            ],
            "edges": [
                edge("c", "a", "value", "trigger"),
                edge("a", "ed", "reachable", "in"),
                edge("ed", "b", "falling", "trigger"),
            ],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": True}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with (
        patch("obs.api.v1.websocket.get_ws_manager", return_value=ws),
        patch("obs.logic.manager._ping_host", new=AsyncMock(return_value=(True, 1.0))) as ping,
    ):
        outputs = await manager._execute_graph("g", "G", flow, {})

    # The real result equals the stored level, so there is no edge at all.
    assert outputs["ed"] == {"rising": False, "falling": False}
    assert manager._hysteresis["g"]["ed"] == {"value": True}
    assert [c.args[0] for c in ping.await_args_list] == ["source"]


@pytest.mark.asyncio
async def test_consecutive_edges_each_restart_a_value_sequence():
    """A sequence has its own reverse pulse trace, separate from the one the
    host_check path uses — both must recognise an edge as retriggerable."""
    manager = _manager()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("ed", "edge_detect", {"data_type": "bool", "value_rising": "true", "value_falling": "true"}),
                node("seq", "value_sequence", {"steps": "[]", "run_mode": "restart"}),
            ],
            "edges": [edge("ed", "seq", "out", "trigger")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": False}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws), patch.object(manager, "_start_value_sequence") as start:
        rising = await manager._execute_graph("g", "G", flow, {"ed": {"in": True}})
        falling = await manager._execute_graph("g", "G", flow, {"ed": {"in": False}})

    # Both edges deliver an identical truthy value — only the pulse provenance
    # distinguishes them from one sustained trigger.
    assert rising["ed"]["out"] is True
    assert falling["ed"]["out"] is True
    assert start.call_count == 2


@pytest.mark.asyncio
async def test_a_held_nodes_idle_trigger_cannot_be_inverted_into_an_action():
    """A held node still emits rising=False, and a NOT downstream turns that
    into a truthy trigger. Edge Detection is a pulse origin, so the manager
    neutralizes the trigger of an action whose provenance reaches a pulse that
    did not fire — exactly as it already did for Change Filter."""
    manager = _manager()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("c", "const_value", {"value": "true", "data_type": "bool"}),
                node("src", "host_check", {"host": "source"}),
                node("ed", "edge_detect", {"on_rising": "trigger", "on_falling": "off"}),
                node("n", "not", {}),
                node("dst", "host_check", {"host": "downstream"}),
            ],
            "edges": [
                edge("c", "src", "value", "trigger"),
                edge("src", "ed", "reachable", "in"),
                edge("ed", "n", "rising", "in1"),
                edge("n", "dst", "out", "trigger"),
            ],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": False}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with (
        patch("obs.api.v1.websocket.get_ws_manager", return_value=ws),
        patch("obs.logic.manager._ping_host", new=AsyncMock(return_value=(True, 1.0))) as ping,
    ):
        await manager._execute_graph("g", "G", flow, {})

    assert [c.args[0] for c in ping.await_args_list] == ["source"]


@pytest.mark.asyncio
async def test_a_non_firing_trigger_is_not_committed_by_a_second_edge_detect():
    """One Edge Detection node's trigger feeding another: on a pass without an
    edge the source emits False on a discrete handle, which the consumer must
    not record as a real low level."""
    manager = _manager()
    target = uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("a", "edge_detect"),
                node("b", "edge_detect"),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [edge("a", "b", "rising", "in"), edge("b", "w", "out", "value")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"a": {"value": False}, "b": {"value": True}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        await manager._execute_graph("g", "G", flow, {"a": {"in": False}})

    assert manager._hysteresis["g"]["b"] == {"value": True}
    manager._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_the_handles_whose_pulse_is_missing_are_neutralized():
    """Two Edge Detection nodes feeding one stateful consumer, one firing and
    one not: the correction must neutralize only the handle behind the pulse
    that did not fire, and leave the real one alone."""
    manager = _manager()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("a", "edge_detect", {"data_type": "number", "value_rising": "10"}),
                node("b", "edge_detect", {"data_type": "number", "value_rising": "20"}),
                node("avg", "avg_multi", {"input_count": 2}),
            ],
            "edges": [edge("a", "avg", "out", "in_1"), edge("b", "avg", "out", "in_2")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"a": {"value": False}, "b": {"value": False}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        # a sees a rising edge and sends 10; b repeats its level and sends nothing.
        outputs = await manager._execute_graph("g", "G", flow, {"a": {"in": True}, "b": {"in": False}})

    assert outputs["a"]["out"] == 10.0
    assert "out" not in outputs["b"]


@pytest.mark.asyncio
async def test_a_pulse_on_one_handle_does_not_validate_the_other():
    """A falling edge says nothing about the "rising" handle, which stays
    False. Asking only whether the source node fired somewhere would accept
    that placeholder as a real low level for a consumer wired to "rising"."""
    manager = _manager()
    target = uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("a", "edge_detect"),
                node("b", "edge_detect"),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [edge("a", "b", "rising", "in"), edge("b", "w", "out", "value")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"a": {"value": True}, "b": {"value": True}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        outputs = await manager._execute_graph("g", "G", flow, {"a": {"in": False}})

    # a genuinely pulses — but on "falling", not on the wired "rising".
    assert outputs["a"]["falling"] is True
    assert outputs["a"]["rising"] is False
    assert manager._hysteresis["g"]["b"] == {"value": True}
    manager._event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_pulse_on_the_wired_handle_is_delivered():
    """The other side of the same check: the consumer must still see a real
    pulse on the handle it is actually wired to."""
    manager = _manager()
    target = uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("a", "edge_detect"),
                node("b", "edge_detect"),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [edge("a", "b", "rising", "in"), edge("b", "w", "out", "value")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    # b is low, so a real rising pulse on "rising" is an edge for b too.
    manager._hysteresis["g"] = {"a": {"value": False}, "b": {"value": False}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        outputs = await manager._execute_graph("g", "G", flow, {"a": {"in": True}})

    assert outputs["a"]["rising"] is True
    assert manager._hysteresis["g"]["b"] == {"value": True}
    assert [c.args[0].value for c in manager._event_bus.publish.await_args_list] == [True]


@pytest.mark.asyncio
async def test_a_missing_pulse_inverted_into_reset_does_not_clear_the_level():
    """The reset handle is a trigger port, so a no-pulse placeholder that a
    synchronous node inverts into True would clear the remembered level for
    good — and the next real transition would only re-seed it."""
    manager = _manager()
    flow = FlowData.model_validate(
        {
            "nodes": [node("a", "edge_detect"), node("n", "not", {}), node("b", "edge_detect")],
            "edges": [edge("a", "n", "rising", "in1"), edge("n", "b", "out", "reset")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    # a repeats its level, so "rising" carries the placeholder, not a pulse.
    manager._hysteresis["g"] = {"a": {"value": True}, "b": {"value": True}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        await manager._execute_graph("g", "G", flow, {"a": {"in": True}})

    assert manager._hysteresis["g"]["b"] == {"value": True}


@pytest.mark.asyncio
async def test_a_pulse_on_one_handle_does_not_release_an_action_on_the_other():
    """The action-trigger provenance must be per handle too: a falling edge
    says nothing about "rising", whose False a NOT downstream turns into a
    truthy trigger."""
    manager = _manager()
    flow = FlowData.model_validate(
        {
            "nodes": [node("ed", "edge_detect"), node("n", "not", {}), node("hc", "host_check", {"host": "downstream"})],
            "edges": [edge("ed", "n", "rising", "in1"), edge("n", "hc", "out", "trigger")],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": True}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with (
        patch("obs.api.v1.websocket.get_ws_manager", return_value=ws),
        patch("obs.logic.manager._ping_host", new=AsyncMock(return_value=(True, 1.0))) as ping,
    ):
        outputs = await manager._execute_graph("g", "G", flow, {"ed": {"in": False}})

    assert outputs["ed"]["falling"] is True
    assert outputs["ed"]["rising"] is False
    assert ping.await_count == 0


@pytest.mark.asyncio
async def test_a_numeric_edge_value_is_probed_with_its_real_value():
    """The fan-in independence probe tries False/True; a numeric edge value of
    10 against `in1 > 5` is decisive for neither, so the probe would call the
    Compare independent of the pulse and publish its idle placeholder."""
    manager = _manager()
    target = uuid.uuid4()
    flow = FlowData.model_validate(
        {
            "nodes": [
                node("ed", "edge_detect", {"data_type": "number", "value_rising": "10", "value_falling": "10"}),
                node("c", "const_value", {"value": "5", "data_type": "number"}),
                node("cmp", "compare", {"operator": ">"}),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [
                edge("ed", "cmp", "out", "in1"),
                edge("c", "cmp", "value", "in2"),
                edge("cmp", "w", "out", "value"),
            ],
        }
    )
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": True}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        outputs = await manager._execute_graph("g", "G", flow, {"ed": {"in": True}})

    assert outputs["ed"] == {"rising": False, "falling": False}
    manager._event_bus.publish.assert_not_awaited()


def _merge_flow(target: uuid.UUID, source_dp: str) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [
                node("ed", "edge_detect", {"on_rising": "trigger"}),
                node("read", "datapoint_read", {"datapoint_id": source_dp}),
                node("merge", "merge", {"input_count": 2}),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [
                edge("ed", "merge", "rising", "in1"),
                edge("read", "merge", "value", "in2"),
                edge("merge", "w", "out", "value"),
            ],
        }
    )


@pytest.mark.asyncio
async def test_an_idle_pulse_does_not_overwrite_the_remembered_merge_input():
    """Merge records every wired port's value plus which one is active. An
    unrelated event must not let the pulse port's idle False replace the pulse
    value Merge is still relaying."""
    source = str(uuid.uuid4())
    manager = _manager({source: 7})
    flow = _merge_flow(uuid.uuid4(), source)
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": False}, "merge": {"values": {"in1": False, "in2": 7}, "active": "in2"}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        await manager._execute_graph("g", "G", flow, {"ed": {"in": True}})
        after_pulse = manager._hysteresis["g"]["merge"].copy()
        outputs = await manager._execute_graph("g", "G", flow, {"read": {"value": 7, "changed": True}})

    assert after_pulse == {"values": {"in1": True, "in2": 7}, "active": "in1"}
    assert manager._hysteresis["g"]["merge"] == after_pulse
    assert outputs["merge"]["out"] is True


@pytest.mark.asyncio
async def test_a_genuinely_fresh_merge_input_still_wins_over_an_idle_pulse():
    """The counterpart: replaying the idle port with its remembered value must
    not freeze Merge — a port that really did change still becomes active."""
    source = str(uuid.uuid4())
    manager = _manager({source: 7})
    flow = _merge_flow(uuid.uuid4(), source)
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": False}, "merge": {"values": {"in1": False, "in2": 7}, "active": "in2"}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        await manager._execute_graph("g", "G", flow, {"ed": {"in": True}})
        manager._registry.get_value.side_effect = lambda dp_id: SimpleNamespace(value=9 if str(dp_id) == source else None, ts=None)
        outputs = await manager._execute_graph("g", "G", flow, {"read": {"value": 9, "changed": True}})

    assert manager._hysteresis["g"]["merge"] == {"values": {"in1": True, "in2": 9}, "active": "in2"}
    assert outputs["merge"]["out"] == 9


@pytest.mark.asyncio
async def test_an_idle_pulse_on_a_merge_without_prior_state_stays_absent():
    """No remembered value to replay: the port must fall back to "nothing
    arrived" rather than relaying the placeholder."""
    source = str(uuid.uuid4())
    manager = _manager({source: 7})
    flow = _merge_flow(uuid.uuid4(), source)
    manager._graphs["g"] = ("G", True, flow)
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        outputs = await manager._execute_graph("g", "G", flow, {"read": {"value": 7, "changed": True}})

    assert manager._hysteresis["g"]["merge"] == {"values": {"in1": None, "in2": 7}, "active": "in2"}
    assert outputs["merge"]["out"] == 7


def _disabled_out_flow(target: uuid.UUID, source_dp: str, data: dict) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [
                node("ed", "edge_detect", data),
                node("rb", "datapoint_read", {"datapoint_id": source_dp}),
                node("sum", "math_formula", {"formula": "a + b"}),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [
                edge("ed", "sum", "out", "in1"),
                edge("rb", "sum", "value", "in2"),
                edge("sum", "w", "result", "value"),
            ],
        }
    )


@pytest.mark.asyncio
async def test_a_handle_that_can_never_fire_does_not_suppress_independent_inputs():
    """With both directions silent, "out" never appears at all — it is not a
    pulse whose absence needs correcting, and treating it as one blanked out a
    Formula fed entirely by its own fresh input."""
    source = str(uuid.uuid4())
    manager = _manager({source: 5})
    flow = _disabled_out_flow(uuid.uuid4(), source, {"on_rising": "off", "on_falling": "off"})
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": False}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        outputs = await manager._execute_graph("g", "G", flow, {"rb": {"value": 5, "changed": True}})

    assert outputs["sum"]["result"] == 5.0
    assert [c.args[0].value for c in manager._event_bus.publish.await_args_list] == [5.0]


@pytest.mark.asyncio
async def test_trigger_only_directions_also_never_send_a_value():
    """A "trigger" direction pulses without sending, so "out" is just as
    permanently absent as with "off" — the same exemption has to apply."""
    source = str(uuid.uuid4())
    manager = _manager({source: 5})
    flow = _disabled_out_flow(uuid.uuid4(), source, {"on_rising": "trigger", "on_falling": "off"})
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": False}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        await manager._execute_graph("g", "G", flow, {"rb": {"value": 5, "changed": True}})

    assert [c.args[0].value for c in manager._event_bus.publish.await_args_list] == [5.0]


@pytest.mark.asyncio
async def test_a_direction_that_still_sends_keeps_the_missing_pulse_correction():
    """The boundary: as soon as ONE direction sends, "out" is a real pulse
    handle again and its idle absence must still suppress the descendant —
    otherwise the placeholder reaches the actuator."""
    source = str(uuid.uuid4())
    manager = _manager({source: 5})
    flow = _disabled_out_flow(uuid.uuid4(), source, {"on_rising": "value", "on_falling": "off"})
    manager._graphs["g"] = ("G", True, flow)
    manager._hysteresis["g"] = {"ed": {"value": False}}
    ws = SimpleNamespace(has_logic_debug_subscribers=lambda _gid: False)

    with patch("obs.api.v1.websocket.get_ws_manager", return_value=ws):
        await manager._execute_graph("g", "G", flow, {"rb": {"value": 5, "changed": True}})

    manager._event_bus.publish.assert_not_awaited()
