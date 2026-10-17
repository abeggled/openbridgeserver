"""Execution tests for the Delay (timer_delay) and Pulse (timer_pulse) blocks (issue #1166)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic.executor import GraphExecutor
from obs.logic.graph_analysis import analyze_topology, topology_warnings
from obs.logic.manager import LogicManager, _ObsoleteGraphExecution, _TimerState
from obs.logic.models import FlowData
from tests.unit.conftest import edge, node


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("obs.logic.manager._TIMER_RETRY_DELAY_S", 0.0)


def _manager() -> LogicManager:
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(data_type="UNKNOWN")
    registry.get_value.return_value = None
    manager = LogicManager(AsyncMock(), AsyncMock(), registry)
    manager._event_bus.subscribe = MagicMock()
    manager._event_bus.unsubscribe = MagicMock()
    return manager


def _published(manager: LogicManager) -> list[tuple[str, object, int]]:
    return [(str(call.args[0].datapoint_id), call.args[0].value, call.args[0].logic_depth) for call in manager._event_bus.publish.await_args_list]


def _values(manager: LogicManager) -> list[object]:
    return [value for _dp, value, _depth in _published(manager)]


def _delay_flow(target: uuid.UUID, delay_s: object = 0.05) -> FlowData:
    """Read Object "changed" → Delay → Write Object trigger (value: constant 7)."""
    return FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                node("delay", "timer_delay", {"delay_s": delay_s}),
                node("value", "const_value", {"value": "7", "data_type": "number"}),
                node("write", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [
                edge("read", "delay", "changed", "trigger"),
                edge("delay", "write", "trigger", "trigger"),
                edge("value", "write", "value", "value"),
            ],
        },
    )


def _pulse_flow(target: uuid.UUID, duration_s: object = 0.05) -> FlowData:
    """Read Object "changed" → Pulse → Write Object value."""
    return FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                node("pulse", "timer_pulse", {"duration_s": duration_s}),
                node("write", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [
                edge("read", "pulse", "changed", "trigger"),
                edge("pulse", "write", "out", "value"),
            ],
        },
    )


def _event(value: object) -> dict[str, dict[str, object]]:
    return {"read": {"value": value, "changed": True}}


def _obsolete_run(manager: LogicManager, *results: dict) -> AsyncMock:
    """An _execute_graph mock: {} means a run a save made obsolete."""

    async def run(*_args: object, obsolete_report: dict[str, bool] | None = None, **_kwargs: object) -> dict:
        result = next(outcomes)
        if not result and obsolete_report is not None:
            obsolete_report.update(obsolete=True, acted=False)
        return result

    outcomes = iter(results)
    return AsyncMock(side_effect=run)


def _pending(kind: str, *, in_s: float = 0.0, need_on: bool = False, prev: bool = False) -> _TimerState:
    return _TimerState(kind, prev_trigger=prev, deadline=monotonic() + in_s, need_on=need_on)


# ── Executor ─────────────────────────────────────────────────────────────


def test_delay_executor_sends_its_trigger_only_on_the_firing_run() -> None:
    flow = FlowData.model_validate({"nodes": [node("delay", "timer_delay")], "edges": []})

    assert GraphExecutor(flow).execute({"delay": {"trigger": True}})["delay"] == {"trigger": False}
    assert GraphExecutor(flow).execute({"delay": {"_fire": True}})["delay"] == {"trigger": True}


def test_pulse_executor_reports_whether_the_pulse_is_on() -> None:
    flow = FlowData.model_validate({"nodes": [node("pulse", "timer_pulse")], "edges": []})

    assert GraphExecutor(flow).execute({"pulse": {"trigger": True}})["pulse"] == {"out": False}
    executor = GraphExecutor(flow)
    executor.active_pulses = frozenset({"pulse"})
    assert executor.execute()["pulse"] == {"out": True}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(2.5, 2.5), ("1.5", 1.5), (None, 1.0), ("", 1.0), ("bad", 1.0), (-1, 0.0), (float("inf"), 1.0), (10**400, 1.0)],
)
def test_timer_seconds_uses_the_default_when_unset_or_unusable(raw: object, expected: float) -> None:
    assert LogicManager._timer_seconds(raw, 1.0) == expected


# ── Delay ────────────────────────────────────────────────────────────────


def test_delay_fires_the_downstream_write_after_the_delay() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = _delay_flow(target)
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1), logic_depth=3)
        task = manager._timer_tasks[("graph", "delay")]
        manager._event_bus.publish.assert_not_awaited()

        await task
        # Shorter than _TIMER_NEW_CASCADE_S: the firing is one more step of the triggering cascade.
        assert _published(manager) == [(str(target), 7, 5)]
        assert manager._timer_tasks == {}
        assert manager._timers["graph"]["delay"].deadline is None

    asyncio.run(exercise())


def test_a_retrigger_starts_a_pending_delay_over() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = _delay_flow(target, 0.2)
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1))
        task = manager._timer_tasks[("graph", "delay")]
        deadline = manager._timers["graph"]["delay"].deadline
        await asyncio.sleep(0.1)
        # A second change pulse while the trigger is still high counts as a new trigger.
        await manager._execute_graph("graph", "Graph", flow, _event(2))
        # The waiting task sleeps on to the new deadline.
        assert manager._timer_tasks[("graph", "delay")] is task
        assert manager._timers["graph"]["delay"].deadline > deadline
        await asyncio.sleep(0.15)  # past the first deadline
        manager._event_bus.publish.assert_not_awaited()

        await task
        assert _published(manager) == [(str(target), 7, 2)]

    asyncio.run(exercise())


def test_a_sustained_trigger_starts_the_delay_only_once() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("level", "const_value", {"value": "true", "data_type": "bool"}), node("delay", "timer_delay", {"delay_s": 60})],
                "edges": [edge("level", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {})
        deadline = manager._timers["graph"]["delay"].deadline
        await manager._execute_graph("graph", "Graph", flow, {})

        assert manager._timers["graph"]["delay"].deadline == deadline
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_memory_level_does_not_restart_a_pending_delay() -> None:
    """Memory is a tick boundary: its committed level is no new pulse."""

    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("level", "const_value", {"value": "true", "data_type": "bool"}),
                    node("memory", "memory"),
                    node("delay", "timer_delay", {"delay_s": 60}),
                ],
                "edges": [edge("level", "memory", "value", "in"), edge("memory", "delay", "out", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {})  # commits the level into memory
        await manager._execute_graph("graph", "Graph", flow, {})
        deadline = manager._timers["graph"]["delay"].deadline
        await manager._execute_graph("graph", "Graph", flow, {})

        assert manager._timers["graph"]["delay"].deadline == deadline
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_level_reaching_the_trigger_twice_does_not_restart_a_pending_delay() -> None:
    """The pulse trace visits a source reached over two paths only once."""

    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("level", "const_value", {"value": "true", "data_type": "bool"}),
                    node("not", "not"),
                    node("or", "or"),
                    node("delay", "timer_delay", {"delay_s": 60}),
                ],
                "edges": [
                    edge("level", "or", "value", "in1"),
                    edge("level", "not", "value", "in"),
                    edge("not", "or", "out", "in2"),
                    edge("or", "delay", "out", "trigger"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {})
        deadline = manager._timers["graph"]["delay"].deadline
        await manager._execute_graph("graph", "Graph", flow, {})

        assert manager._timers["graph"]["delay"].deadline == deadline
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_delay_firing_retriggers_a_downstream_sequence_like_a_cron_tick() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("delay", "timer_delay", {"delay_s": 0}),
                    node("sequence", "value_sequence", {"restart_policy": "queue", "steps": [{"datapoint_id": str(target), "value": 1}]}),
                ],
                "edges": [edge("delay", "sequence", "trigger", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        # Two firings in a row: the second must not look like a sustained trigger.
        for _ in range(2):
            assert await manager._run_timer_graph("graph", "delay", "timer_delay", {"delay": {"_fire": True}}, 0) == "ran"
            await manager._sequence_tasks[("graph", "sequence")]

        assert manager._event_bus.publish.await_count == 2

    asyncio.run(exercise())


def test_a_pending_delay_survives_a_save_of_its_sheet() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = _delay_flow(target, 0.05)
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1))
        task = manager._timer_tasks[("graph", "delay")]
        manager.invalidate_cache("graph")
        await asyncio.sleep(0)
        assert not task.cancelled()  # it reads the sheet anew when it fires

        manager._graphs["graph"] = ("Graph", True, flow)  # reload()
        manager._resume_timers()
        assert manager._timer_tasks[("graph", "delay")] is task
        await task

        assert _published(manager) == [(str(target), 7, 2)]

    asyncio.run(exercise())


@pytest.mark.parametrize("change", ["disabled", "gone"])
def test_a_delay_into_an_inactive_sheet_stays_pending_for_reload(change: str) -> None:
    async def exercise() -> None:
        manager = _manager()
        if change == "disabled":
            manager._graphs["graph"] = ("Graph", False, _delay_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["delay"] = _pending("timer_delay")
        deadline = state.deadline

        await manager._run_timer_delay("graph", "delay")

        manager._event_bus.publish.assert_not_awaited()
        assert state.deadline == deadline

    asyncio.run(exercise())


def test_a_delay_of_a_removed_block_is_forgotten() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, FlowData.model_validate({"nodes": [node("delay", "timer_pulse")], "edges": []}))
        manager._timers["graph"] = {"delay": _pending("timer_delay")}

        await manager._run_timer_delay("graph", "delay")

        assert manager._timers["graph"] == {}
        manager._event_bus.publish.assert_not_awaited()

    asyncio.run(exercise())


def test_a_failed_firing_is_retried() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _pending("timer_delay")}
        # An obsolete run returns no outputs at all.
        manager._execute_graph = _obsolete_run(manager, {}, {}, {"delay": {}})  # type: ignore[method-assign]

        await manager._run_timer_delay("graph", "delay")

        assert manager._execute_graph.await_count == 3
        assert manager._timers["graph"]["delay"].deadline is None

    asyncio.run(exercise())


def test_a_delay_task_without_a_pending_delay_returns_at_once() -> None:
    async def exercise() -> None:
        manager = _manager()
        await manager._run_timer_delay("graph", "delay")
        manager._timers["graph"] = {"delay": _TimerState("timer_delay")}
        await manager._run_timer_delay("graph", "delay")
        manager._event_bus.publish.assert_not_awaited()

    asyncio.run(exercise())


# ── Pulse ────────────────────────────────────────────────────────────────


def test_pulse_sets_its_output_for_the_duration() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = _pulse_flow(target)
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1), logic_depth=2)
        task = manager._timer_tasks[("graph", "pulse")]
        assert manager._timers["graph"]["pulse"].deadline is not None

        await task
        # The run starting the pulse and the run ending it (a short pulse: the
        # same cascade); the triggering run's "off" is stale once the pulse has started.
        assert _published(manager) == [(str(target), True, 4), (str(target), False, 4)]
        assert manager._timers["graph"]["pulse"].deadline is None
        assert manager._timer_tasks == {}

    asyncio.run(exercise())


def test_a_retrigger_extends_a_running_pulse() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _pulse_flow(uuid.uuid4(), 0.2)
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1))
        task = manager._timer_tasks[("graph", "pulse")]
        await asyncio.sleep(0.1)
        deadline = manager._timers["graph"]["pulse"].deadline
        await manager._execute_graph("graph", "Graph", flow, _event(2))

        # The running task keeps the pulse and picks the extension up.
        assert manager._timer_tasks[("graph", "pulse")] is task
        assert manager._timers["graph"]["pulse"].deadline > deadline
        await asyncio.sleep(0.15)  # past the first end, before the extended one
        assert manager._timers["graph"]["pulse"].deadline is not None

        await task
        # No second start run while the pulse was already on.
        assert _values(manager) == [True, True, False]

    asyncio.run(exercise())


def test_a_retrigger_before_the_start_run_still_starts_the_pulse() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _pulse_flow(uuid.uuid4())
        manager._graphs["graph"] = ("Graph", True, flow)
        pulse = flow.nodes[1]

        manager._start_timer("graph", pulse, 0)
        manager._start_timer("graph", pulse, 0)  # before the task's first step
        await manager._timer_tasks[("graph", "pulse")]

        assert _values(manager) == [True, False]

    asyncio.run(exercise())


def test_a_retrigger_during_the_end_run_starts_the_pulse_again_after_it() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _pulse_flow(uuid.uuid4(), 0)
        manager._graphs["graph"] = ("Graph", True, flow)
        pulse = flow.nodes[1]
        execute = manager._execute_graph
        runs = 0

        async def execute_and_retrigger(*args: object, **kwargs: object) -> dict:
            nonlocal runs
            runs += 1
            result = await execute(*args, **kwargs)
            if runs == 2:  # a trigger while the end run is in flight
                manager._start_timer("graph", pulse, 0)
            return result

        manager._execute_graph = execute_and_retrigger  # type: ignore[method-assign]
        manager._start_timer("graph", pulse, 0)
        await manager._timer_tasks[("graph", "pulse")]

        # on, off (retriggered meanwhile), on again, off — in this order.
        assert _values(manager) == [True, False, True, False]

    asyncio.run(exercise())


def test_a_pulse_that_ran_out_meanwhile_is_only_ended() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(target))
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=-5, need_on=True)}

        manager._resume_timers()
        await manager._timer_tasks[("graph", "pulse")]

        assert _published(manager) == [(str(target), False, 2)]

    asyncio.run(exercise())


def test_a_resumed_pulse_not_yet_started_is_started() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4()))
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=0.05, need_on=True)}

        manager._resume_timers()
        await manager._timer_tasks[("graph", "pulse")]

        assert _values(manager) == [True, False]

    asyncio.run(exercise())


@pytest.mark.parametrize("need_on", [True, False])
def test_a_pulse_in_a_sheet_disabled_meanwhile_is_kept_for_reactivation(need_on: bool) -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", False, _pulse_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse", need_on=need_on)
        deadline = state.deadline

        await manager._run_timer_pulse("graph", "pulse")

        assert (state.deadline, state.need_on) == (deadline, need_on)
        manager._event_bus.publish.assert_not_awaited()

    asyncio.run(exercise())


def test_a_pulse_retriggered_while_its_sheet_went_inactive_keeps_the_new_end() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse")

        async def disable_and_retrigger(*_args: object, **_kwargs: object) -> str:
            state.deadline = 123.0
            return "inactive"

        manager._run_timer_graph = disable_and_retrigger  # type: ignore[method-assign]
        await manager._run_timer_pulse("graph", "pulse")

        assert state.deadline == 123.0

    asyncio.run(exercise())


@pytest.mark.parametrize("need_on", [True, False])
def test_a_pulse_of_a_removed_block_is_only_forgotten(need_on: bool) -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, FlowData.model_validate({"nodes": [node("other", "timer_pulse")], "edges": []}))
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", need_on=need_on)}

        await manager._run_timer_pulse("graph", "pulse")

        assert manager._timers["graph"] == {}
        manager._event_bus.publish.assert_not_awaited()
        manager._db.execute_and_commit.assert_awaited()

    asyncio.run(exercise())


def test_failed_pulse_runs_are_retried() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 0))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse", in_s=0.2, need_on=True)
        manager._execute_graph = _obsolete_run(manager, {}, {"pulse": {}}, {}, {"pulse": {}})  # type: ignore[method-assign]

        await manager._run_timer_pulse("graph", "pulse")

        # start failed, start, end failed, end
        assert manager._execute_graph.await_count == 4
        assert (state.deadline, state.need_on) == (None, False)

    asyncio.run(exercise())


def test_a_retrigger_during_a_failed_end_run_keeps_the_new_end() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 0))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse")
        outcomes = iter(["failed", "ran", "ran"])

        async def run(*_args: object, **_kwargs: object) -> str:
            outcome = next(outcomes)
            if outcome == "failed":
                state.deadline, state.need_on = monotonic(), True  # retriggered meanwhile
            return outcome

        manager._run_timer_graph = run  # type: ignore[method-assign]
        await manager._run_timer_pulse("graph", "pulse")

        # end failed (retriggered meanwhile), the retrigger's start, its end
        assert (state.deadline, state.need_on) == (None, False)

    asyncio.run(exercise())


def test_dropping_a_timer_replaced_meanwhile_keeps_the_replacement() -> None:
    async def exercise() -> None:
        manager = _manager()
        replacement = _TimerState("timer_pulse")
        manager._timers["graph"] = {"pulse": replacement}

        await manager._drop_timer("graph", "pulse", _TimerState("timer_pulse"))

        assert manager._timers["graph"]["pulse"] is replacement
        manager._db.execute_and_commit.assert_not_awaited()

    asyncio.run(exercise())


def test_a_pulse_task_without_a_pulse_returns_at_once() -> None:
    async def exercise() -> None:
        manager = _manager()
        await manager._run_timer_pulse("graph", "pulse")
        manager._timers["graph"] = {"pulse": _TimerState("timer_pulse")}
        await manager._run_timer_pulse("graph", "pulse")
        manager._event_bus.publish.assert_not_awaited()

    asyncio.run(exercise())


# ── Lifecycle and persistence ────────────────────────────────────────────


def test_the_trigger_level_survives_a_save_of_the_block() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("level", "const_value", {"value": "true", "data_type": "bool"}), node("delay", "timer_delay", {"delay_s": 60})],
                "edges": [edge("level", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._execute_graph("graph", "Graph", flow, {})
        deadline = manager._timers["graph"]["delay"].deadline
        manager.invalidate_cache("graph")
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._execute_graph("graph", "Graph", flow, {})

        assert manager._timers["graph"]["delay"].deadline == deadline  # the level is no new edge
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_block_retyped_with_the_same_id_starts_with_fresh_state() -> None:
    manager = _manager()
    manager._timers["graph"] = {"node": _TimerState("timer_delay", prev_trigger=True)}

    assert manager._timer_state("graph", "node", "timer_pulse") == _TimerState("timer_pulse")


def test_timer_state_is_persisted_and_adopted_after_a_restart() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("pulse", "timer_pulse", {"duration_s": 120}),
                    node("delay", "timer_delay"),
                    node("quiet", "timer_delay", {"persist_state": False}),
                    node("other", "memory"),
                ],
                "edges": [],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._hysteresis["graph"] = {"other": {"value": 1}}
        manager._timers["graph"] = {
            "pulse": _pending("timer_pulse", in_s=60, need_on=True, prev=True),
            "delay": _TimerState("timer_delay"),
            "quiet": _pending("timer_delay", in_s=60),
            "removed": _TimerState("timer_delay"),
        }

        await manager._persist_node_state("graph")
        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]

        assert set(saved) == {"pulse", "delay", "other"}
        assert saved["delay"] == {"timer_prev_trigger": False}
        assert saved["pulse"]["pulse_need_on"] is True
        assert abs(saved["pulse"]["timer_deadline"] - (datetime.now(UTC).timestamp() + 60)) < 5

        restarted = _manager()
        restarted._graphs["graph"] = ("Graph", True, flow)
        restarted._hysteresis["graph"] = {**saved, "quiet": "not a dict"}
        restarted._adopt_persisted_timer_state()

        assert restarted._hysteresis["graph"] == {"other": {"value": 1}}
        pulse = restarted._timers["graph"]["pulse"]
        assert (pulse.prev_trigger, pulse.need_on) == (True, True)
        assert 55 < pulse.deadline - monotonic() < 65
        assert restarted._timers["graph"]["delay"] == _TimerState("timer_delay")

    asyncio.run(exercise())


def test_adopting_keeps_live_state_and_tolerates_a_broken_deadline() -> None:
    manager = _manager()
    flow = FlowData.model_validate({"nodes": [node("live", "timer_pulse"), node("broken", "timer_delay")], "edges": []})
    manager._graphs = {"graph": ("Graph", True, flow), "empty": ("Empty", True, flow)}
    manager._hysteresis["graph"] = {"live": {"timer_deadline": 1.0}, "broken": {"timer_deadline": "soon"}}
    live = _TimerState("timer_pulse", prev_trigger=True)
    manager._timers["graph"] = {"live": live}
    before = monotonic()

    manager._adopt_persisted_timer_state()

    assert manager._timers["graph"]["live"] is live
    assert before <= manager._timers["graph"]["broken"].deadline <= monotonic()


def test_persisting_during_the_save_gap_keeps_the_timer_state() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._timers["graph"] = {"delay": _TimerState("timer_delay", prev_trigger=True)}
        manager._timer_dirty.add("graph")

        await manager._persist_node_state("graph")  # the graph entry is briefly missing

        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]
        assert saved == {"delay": {"timer_prev_trigger": True}}

    asyncio.run(exercise())


def test_disabling_drops_delays_with_their_level_but_keeps_pulses() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate({"nodes": [node("delay", "timer_delay"), node("pulse", "timer_pulse")], "edges": []})
        manager._graphs["graph"] = ("Graph", False, flow)
        manager._timers["graph"] = {"delay": _pending("timer_delay", in_s=60, prev=True), "pulse": _pending("timer_pulse", in_s=60)}
        task = asyncio.create_task(asyncio.sleep(60))
        manager._timer_tasks[("graph", "pulse")] = task

        delay_task = asyncio.create_task(asyncio.sleep(60))
        manager._timer_tasks[("graph", "delay")] = delay_task

        manager._resume_timers()
        await asyncio.sleep(0)

        assert set(manager._timers["graph"]) == {"pulse"}
        assert delay_task.cancelled()
        # The pulse's task finds the sheet inactive by itself and keeps the pulse.
        assert manager._timer_tasks == {("graph", "pulse"): task}
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_resuming_prunes_gone_sheets_and_blocks_and_skips_running_or_idle_timers() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {"nodes": [node("running", "timer_pulse"), node("idle", "timer_delay"), node("retyped", "timer_pulse")], "edges": []}
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers = {
            "graph": {
                "running": _pending("timer_pulse", in_s=60),
                "idle": _TimerState("timer_delay"),
                "retyped": _pending("timer_delay"),
                "removed": _pending("timer_pulse"),
            },
            "deleted": {"pulse": _pending("timer_pulse")},
        }
        running = asyncio.create_task(asyncio.sleep(60))
        manager._timer_tasks[("graph", "running")] = running

        manager._resume_timers()

        assert set(manager._timers) == {"graph"}
        assert set(manager._timers["graph"]) == {"running", "idle"}
        assert manager._timer_tasks == {("graph", "running"): running}
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_finished_task_does_not_block_resuming_a_pulse() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 60))
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60)}
        finished = asyncio.create_task(asyncio.sleep(0))
        await finished
        manager._timer_tasks[("graph", "pulse")] = finished

        manager._resume_timers()

        assert manager._timer_tasks[("graph", "pulse")] is not finished
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_deleting_or_resetting_a_sheet_forgets_its_timers() -> None:
    async def exercise() -> None:
        manager = _manager()
        for graph_id in ("graph", "other"):
            manager._timers[graph_id] = {"pulse": _pending("timer_pulse", in_s=60)}
        task = asyncio.create_task(asyncio.sleep(60))
        manager._timer_tasks[("graph", "pulse")] = task

        await manager.reset_node_state("graph")
        await asyncio.sleep(0)
        assert task.cancelled()
        assert set(manager._timers) == {"other"}

        manager.remove_graph("other")
        assert manager._timers == {}

    asyncio.run(exercise())


def test_reload_adopts_and_resumes_timers() -> None:
    async def exercise() -> None:
        manager = _manager()

        async def load_graphs() -> None:
            manager._graphs = {"graph": ("Graph", True, _delay_flow(uuid.uuid4(), 60))}
            manager._hysteresis["graph"] = {"delay": {"timer_prev_trigger": True, "timer_deadline": datetime.now(UTC).timestamp() + 60}}

        manager._load_graphs = load_graphs  # type: ignore[method-assign]
        await manager.reload()

        assert ("graph", "delay") in manager._timer_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_start_adopts_and_resumes_a_persisted_pulse() -> None:
    async def exercise() -> None:
        manager = _manager()

        async def load_graphs() -> None:
            manager._graphs = {"graph": ("Graph", True, _pulse_flow(uuid.uuid4(), 60))}
            manager._hysteresis["graph"] = {"pulse": {"timer_deadline": datetime.now(UTC).timestamp() + 60}}

        manager._load_graphs = load_graphs  # type: ignore[method-assign]
        manager._load_app_config = AsyncMock()  # type: ignore[method-assign]
        await manager.start()

        assert ("graph", "pulse") in manager._timer_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_stop_cancels_pending_timers_and_waits_for_their_runs() -> None:
    async def exercise() -> None:
        manager = _manager()
        task = asyncio.create_task(asyncio.sleep(60))
        manager._timer_tasks[("graph", "delay")] = task
        finished: list[bool] = []

        async def run() -> None:
            await asyncio.sleep(0.01)
            finished.append(True)

        manager._timer_runs[("graph", "delay")] = asyncio.ensure_future(run())

        await manager.stop()
        await asyncio.sleep(0)

        assert task.cancelled()
        assert manager._timer_tasks == {}
        assert finished == [True]

    asyncio.run(exercise())


def test_stop_cancels_timer_runs_past_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("obs.logic.manager._HEMS_STOP_TIMEOUT_S", 0.01)

    async def exercise() -> None:
        manager = _manager()
        hanging = asyncio.ensure_future(asyncio.sleep(60))
        manager._timer_runs[("graph", "delay")] = hanging
        manager._timer_runs[("graph", "done")] = done = asyncio.get_running_loop().create_future()
        done.set_result(None)

        await manager.stop()
        await asyncio.sleep(0)

        assert hanging.cancelled()

    asyncio.run(exercise())


def test_timer_runs_of_one_block_do_not_overlap() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        release = asyncio.Event()
        order: list[str] = []

        async def run(*args: object, **_kwargs: object) -> dict:
            order.append(f"start {args[3]}")
            await release.wait()
            order.append(f"end {args[3]}")
            return {"delay": {}}

        manager._execute_graph = run  # type: ignore[method-assign]
        first = asyncio.create_task(manager._run_timer_graph("graph", "delay", "timer_delay", {"n": 1}, 0))  # type: ignore[dict-item]
        await asyncio.sleep(0)
        # Two waiters on the same run in flight: only one may start next.
        second = asyncio.create_task(manager._run_timer_graph("graph", "delay", "timer_delay", {"n": 2}, 0))  # type: ignore[dict-item]
        third = asyncio.create_task(manager._run_timer_graph("graph", "delay", "timer_delay", {"n": 3}, 0))  # type: ignore[dict-item]
        await asyncio.sleep(0)
        release.set()
        assert await asyncio.gather(first, second, third) == ["ran", "ran", "ran"]

        assert order == ["start {'n': 1}", "end {'n': 1}", "start {'n': 2}", "end {'n': 2}", "start {'n': 3}", "end {'n': 3}"]
        assert manager._timer_runs == {}

    asyncio.run(exercise())


def test_cancelling_a_timer_does_not_cut_its_run_short() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        started, release = asyncio.Event(), asyncio.Event()
        finished: list[bool] = []

        async def slow_run(*_args: object, **_kwargs: object) -> dict:
            started.set()
            await release.wait()
            finished.append(True)
            return {}

        manager._execute_graph = slow_run  # type: ignore[method-assign]
        task = asyncio.create_task(manager._run_timer_graph("graph", "delay", "timer_delay", {}, 0))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        await asyncio.sleep(0)

        assert task.cancelled()
        assert finished == [True]

    asyncio.run(exercise())


def test_a_failing_timer_run_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._execute_graph = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

        # It may have acted before raising, so it is not retried.
        assert await manager._run_timer_graph("graph", "delay", "timer_delay", {}, 0) == "ran"

    with caplog.at_level(logging.ERROR, logger="obs.logic.manager"):
        asyncio.run(exercise())

    assert any(record.message == "Timer run failed" for record in caplog.records)


def test_a_cancelled_or_superseded_timer_run_is_not_reported(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        manager = _manager()
        loop = asyncio.get_running_loop()
        cancelled = loop.create_future()
        cancelled.cancel()
        manager._timer_runs[("graph", "delay")] = current = loop.create_future()
        manager._timer_run_finished(("graph", "delay"), cancelled)
        succeeded = loop.create_future()
        succeeded.set_result({})
        manager._timer_run_finished(("graph", "delay"), succeeded)

        assert manager._timer_runs == {("graph", "delay"): current}

    with caplog.at_level(logging.ERROR, logger="obs.logic.manager"):
        asyncio.run(exercise())

    assert caplog.records == []


def test_stop_without_runs_returns_at_once() -> None:
    async def exercise() -> None:
        manager = _manager()
        await manager.stop()
        assert manager._timer_runs == {}

    asyncio.run(exercise())


def test_a_firing_that_raised_is_not_repeated() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _pending("timer_delay")}
        manager._execute_graph = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

        await manager._run_timer_delay("graph", "delay")

        assert manager._execute_graph.await_count == 1
        assert manager._timers["graph"]["delay"].deadline is None

    asyncio.run(exercise())


def test_a_retrigger_during_a_failed_firing_keeps_the_new_deadline() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["delay"] = _pending("timer_delay")
        outcomes = iter(["inactive", "failed", "ran"])
        deadlines: list[float | None] = []

        async def run(*_args: object, **_kwargs: object) -> str:
            outcome = next(outcomes)
            if outcome != "ran":
                state.deadline = monotonic()  # retriggered while the run was in flight
            deadlines.append(state.deadline)
            return outcome

        manager._run_timer_graph = run  # type: ignore[method-assign]
        await manager._run_timer_delay("graph", "delay")  # inactive: the retrigger's deadline stays
        assert state.deadline == deadlines[0]
        await manager._run_timer_delay("graph", "delay")  # failed: fires for the retrigger, then done

        assert state.deadline is None

    asyncio.run(exercise())


def test_the_pulse_start_is_persisted_as_done_by_its_own_run() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 60))
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60, need_on=True)}
        execute = manager._execute_graph
        persisted: list[bool] = []

        async def run(*args: object, **kwargs: object) -> dict:
            result = await execute(*args, **kwargs)
            persisted.append(json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]["pulse"]["pulse_need_on"])
            return result

        manager._execute_graph = run  # type: ignore[method-assign]
        task = asyncio.create_task(manager._run_timer_pulse("graph", "pulse"))
        while not persisted:
            await asyncio.sleep(0.01)
        task.cancel()

        assert persisted == [False]

    asyncio.run(exercise())


def test_a_pulse_start_into_an_inactive_sheet_is_still_due() -> None:
    async def exercise() -> None:
        manager = _manager()
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse", need_on=True)

        await manager._run_timer_pulse("graph", "pulse")

        assert state.need_on is True

    asyncio.run(exercise())


def test_a_slower_run_does_not_overwrite_a_pulse_level_that_changed_meanwhile() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = _pulse_flow(target, 60)
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60)}
        publish = manager._event_bus.publish
        publish.side_effect = lambda _event: manager._timers["graph"]["pulse"].__setattr__("deadline", None)  # the pulse ends meanwhile

        # The run saw the pulse on and computed out=True; by its write the pulse has ended.
        unrelated_write = FlowData.model_validate(
            {
                "nodes": [
                    # Writes go out in node order: the unrelated one first.
                    node("const", "const_value", {"value": "1", "data_type": "number"}),
                    node("w2", "datapoint_write", {"datapoint_id": str(uuid.uuid4())}),
                    *[n.model_dump() for n in flow.nodes],
                ],
                "edges": [*[e.model_dump() for e in flow.edges], edge("const", "w2", "value", "value")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, unrelated_write)
        await manager._execute_graph("graph", "Graph", unrelated_write, {})

        # The unrelated write went out first (and ended the pulse); the stale pulse write did not.
        assert [str(call.args[0].datapoint_id) != str(target) for call in publish.await_args_list] == [True]

    asyncio.run(exercise())


def test_a_low_level_drops_an_idle_timer_state_and_persists_that() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("level", "const_value", {"value": "false", "data_type": "bool"}), node("delay", "timer_delay")],
                "edges": [edge("level", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"delay": _TimerState("timer_delay", prev_trigger=True)}

        await manager._execute_graph("graph", "Graph", flow, {})
        assert "graph" not in manager._timers
        # The fall is persisted: a restart must not take the input as still high.
        assert json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"] == {}

        manager._db.execute_and_commit.reset_mock()
        await manager._execute_graph("graph", "Graph", flow, {})
        manager._db.execute_and_commit.assert_not_awaited()  # nothing to persist while low and idle

    asyncio.run(exercise())


@pytest.mark.parametrize("busy", ["deadline", "need_on", "task", "other"])
def test_a_low_level_keeps_a_busy_timer_state(busy: str) -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate({"nodes": [node("pulse", "timer_pulse"), node("other", "timer_pulse")], "edges": []})
        state = _TimerState("timer_pulse", prev_trigger=True, deadline=1.0 if busy == "deadline" else None, need_on=busy == "need_on")
        manager._timers["graph"] = {"pulse": state}
        if busy == "other":
            manager._timers["graph"]["other"] = _TimerState("timer_pulse", deadline=1.0)
        if busy == "task":
            manager._timer_tasks[("graph", "pulse")] = asyncio.create_task(asyncio.sleep(60))

        manager._set_timer_level("graph", flow.nodes[0], False)

        assert ("pulse" in manager._timers["graph"]) is (busy != "other")
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_disabling_persists_the_dropped_delay() -> None:
    async def exercise() -> None:
        manager = _manager()

        async def load_graphs() -> None:
            manager._graphs = {"graph": ("Graph", False, _delay_flow(uuid.uuid4(), 60))}

        manager._timers["graph"] = {"delay": _pending("timer_delay", in_s=60, prev=True)}
        manager._load_graphs = load_graphs  # type: ignore[method-assign]
        await manager.reload()

        assert manager._timers == {}
        assert json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"] == {}
        assert manager._timer_dirty == set()

    asyncio.run(exercise())


def test_dirty_timers_of_an_unloaded_sheet_wait_for_its_reload() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._timer_dirty.add("saving")  # briefly unloaded by a save

        await manager._persist_dirty_timers()

        assert manager._timer_dirty == {"saving"}
        manager._db.execute_and_commit.assert_not_awaited()

    asyncio.run(exercise())


def test_a_failed_save_keeps_the_timer_state_dirty() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _TimerState("timer_delay", prev_trigger=True)}
        manager._timer_dirty.add("graph")
        manager._db.execute_and_commit.side_effect = RuntimeError("disk full")

        await manager._persist_node_state("graph")

        assert manager._timer_dirty == {"graph"}

    asyncio.run(exercise())


def test_a_failed_save_of_hysteresis_alone_marks_no_timers_dirty() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._hysteresis["graph"] = {"other": {"value": 1}}
        manager._db.execute_and_commit.side_effect = RuntimeError("disk full")

        await manager._persist_node_state("graph")

        assert manager._timer_dirty == set()

    asyncio.run(exercise())


def test_an_idle_timer_with_a_held_trigger_costs_no_write_per_run() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("level", "const_value", {"value": "true", "data_type": "bool"}), node("delay", "timer_delay", {"delay_s": 0})],
                "edges": [edge("level", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._execute_graph("graph", "Graph", flow, {})
        await manager._timer_tasks[("graph", "delay")]
        manager._db.execute_and_commit.reset_mock()

        await manager._execute_graph("graph", "Graph", flow, {})

        manager._db.execute_and_commit.assert_not_awaited()

    asyncio.run(exercise())


def test_a_manual_run_of_a_disabled_sheet_starts_no_timer() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 60)
        manager._graphs["graph"] = ("Graph", False, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1))

        assert manager._timer_tasks == {}

    asyncio.run(exercise())


def test_an_event_write_following_a_changed_pulse_is_kept() -> None:
    async def exercise() -> None:
        manager = _manager()
        target, level_target, other_target = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("pulse", "timer_pulse"),
                    node("event_write", "datapoint_write", {"datapoint_id": str(target)}),
                    node("level_write", "datapoint_write", {"datapoint_id": str(level_target)}),
                    node("other_write", "datapoint_write", {"datapoint_id": str(other_target)}),
                ],
                "edges": [
                    edge("read", "event_write", "changed", "trigger"),
                    edge("pulse", "event_write", "out", "value"),
                    edge("pulse", "level_write", "out", "value"),
                    edge("pulse", "other_write", "out", "value"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60)}

        async def end_pulse(_event: object) -> None:
            manager._timers["graph"]["pulse"].deadline = None  # the pulse ends during the first write

        manager._event_bus.publish = AsyncMock(side_effect=end_pulse)
        await manager._execute_graph("graph", "Graph", flow, _event(1))

        # The event write goes out; both level writes after it are stale.
        assert [call.args[0].datapoint_id for call in manager._event_bus.publish.await_args_list] == [target]

    asyncio.run(exercise())


def test_a_retrigger_during_the_firing_run_fires_again_afterwards() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 0)
        manager._graphs["graph"] = ("Graph", True, flow)
        delay = flow.nodes[1]
        runs = 0

        async def run(*_args: object, **_kwargs: object) -> dict:
            nonlocal runs
            runs += 1
            if runs == 1:
                manager._start_timer("graph", delay, 0)  # the task is busy firing: it must not be cancelled
            return {"delay": {}}

        manager._execute_graph = run  # type: ignore[method-assign]
        manager._start_timer("graph", delay, 0)
        task = manager._timer_tasks[("graph", "delay")]
        await task

        assert runs == 2
        assert not task.cancelled()

    asyncio.run(exercise())


def test_a_shorter_deadline_wakes_a_sleeping_timer() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 60)
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._start_timer("graph", flow.nodes[1], 0)
        task = manager._timer_tasks[("graph", "delay")]
        await asyncio.sleep(0)

        flow.nodes[1].data["delay_s"] = 0  # saved with a shorter delay, then retriggered
        manager._start_timer("graph", flow.nodes[1], 0)
        await asyncio.wait_for(task, 1)

        assert manager._event_bus.publish.await_count == 1

    asyncio.run(exercise())


def test_a_run_that_failed_before_acting_is_retried_a_few_times(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _pending("timer_delay")}
        manager._execute_graph = AsyncMock(return_value={})  # type: ignore[method-assign]

        await manager._run_timer_delay("graph", "delay")

        assert manager._execute_graph.await_count == 3
        assert manager._timers["graph"]["delay"].errors == 0

    with caplog.at_level(logging.WARNING, logger="obs.logic.manager"):
        asyncio.run(exercise())

    assert any("giving up" in record.message for record in caplog.records)


def test_no_timer_starts_once_stopping() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 0)
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager.stop()

        manager._start_timer("graph", flow.nodes[1], 0)

        assert manager._timer_tasks == {}
        assert manager._timers["graph"]["delay"].deadline is not None  # kept and persisted for the next start

    asyncio.run(exercise())


def test_a_restored_deadline_is_capped_at_the_configured_duration() -> None:
    manager = _manager()
    flow = FlowData.model_validate(
        {"nodes": [node("pulse", "timer_pulse", {"duration_s": 30}), node("delay", "timer_delay", {"delay_s": 10})], "edges": []}
    )
    manager._graphs["graph"] = ("Graph", True, flow)
    far = datetime.now(UTC).timestamp() + 10 * 365 * 86400  # the clock jumped back before the restart
    manager._hysteresis["graph"] = {"pulse": {"timer_deadline": far}, "delay": {"timer_deadline": far}}

    manager._adopt_persisted_timer_state()

    assert manager._timers["graph"]["pulse"].deadline - monotonic() <= 30
    assert manager._timers["graph"]["delay"].deadline - monotonic() <= 10


def test_a_manual_run_of_a_disabled_sheet_records_no_level() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 60)
        manager._graphs["graph"] = ("Graph", False, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1))

        assert manager._timers == {}

    asyncio.run(exercise())


def test_an_obsolete_run_still_starts_its_delay_as_the_sheet_now_stands() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node("value", "const_value", {"value": "7", "data_type": "number"}),
                    node("write", "datapoint_write", {"datapoint_id": str(target)}),  # written in this run
                ],
                "edges": [edge("read", "delay", "changed", "trigger"), edge("value", "write", "value", "value")],
            },
        )
        saved = _delay_flow(target, 0)
        manager._graphs["graph"] = ("Graph", True, flow)

        async def publish(_event: object) -> None:
            manager._ical_cache_generations["graph"] = object()  # a save during the write
            manager._graphs["graph"] = ("Graph", True, saved)

        manager._event_bus.publish = AsyncMock(side_effect=publish)
        await manager._execute_graph("graph", "Graph", flow, _event(1))

        state = manager._timers["graph"]["delay"]
        assert state.deadline is not None and state.deadline - monotonic() < 1  # the saved delay_s=0
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


@pytest.mark.parametrize("change", ["removed", "disabled", "unloaded"])
def test_the_current_timer_node_follows_a_save(change: str) -> None:
    manager = _manager()
    flow = _delay_flow(uuid.uuid4())
    run_node = flow.nodes[1]
    if change == "removed":
        manager._graphs["graph"] = ("Graph", True, FlowData.model_validate({"nodes": [], "edges": []}))
    elif change == "disabled":
        manager._graphs["graph"] = ("Graph", False, flow)

    assert manager._current_timer_node("graph", run_node) is (run_node if change == "unloaded" else None)


def test_a_run_failing_in_its_writes_takes_the_delay_level_back() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("level", "const_value", {"value": "true", "data_type": "bool"}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node("other", "timer_delay", {"delay_s": 60}),
                    node("newer", "timer_delay", {"delay_s": 60}),
                    node("value", "const_value", {"value": "7", "data_type": "number"}),
                    node("write", "datapoint_write", {"datapoint_id": str(uuid.uuid4())}),
                ],
                "edges": [
                    edge("level", "delay", "value", "trigger"),
                    edge("level", "other", "value", "trigger"),
                    edge("level", "newer", "value", "trigger"),
                    edge("value", "write", "value", "value"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        async def publish(_event: object) -> None:
            manager._timers["graph"]["other"].prev_trigger = False  # a later run saw it low
            manager._timer_level_seqs["graph"]["newer"] += 1  # a later run saw it high too
            raise asyncio.CancelledError

        manager._event_bus.publish = AsyncMock(side_effect=publish)
        with pytest.raises(asyncio.CancelledError):
            await manager._execute_graph("graph", "Graph", flow, {})

        assert manager._timer_tasks == {}
        assert set(manager._timers["graph"]) == {"other", "newer"}  # "delay" back to low: no state needed
        assert manager._timers["graph"]["other"].prev_trigger is False
        assert manager._timers["graph"]["newer"].prev_trigger is True  # the later run's level stays

    asyncio.run(exercise())


def test_reload_drops_but_does_not_readopt_a_sheets_persisted_timer_state() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 60)

        async def load_graphs() -> None:
            manager._graphs = {"graph": ("Graph", True, flow)}
            manager._hysteresis["graph"] = {"delay": {"timer_prev_trigger": True}}  # the stale row

        manager._load_graphs = load_graphs  # type: ignore[method-assign]
        await manager.reload()
        manager._timers.clear()  # dropped in memory, row not yet rewritten
        await manager.reload()

        assert manager._timers == {}
        assert manager._hysteresis["graph"] == {}

        manager.remove_graph("graph")
        assert "graph" not in manager._timers_adopted

    asyncio.run(exercise())


def test_a_delay_removed_by_a_save_during_the_run_does_not_start() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node("value", "const_value", {"value": "7", "data_type": "number"}),
                    node("write", "datapoint_write", {"datapoint_id": str(uuid.uuid4())}),
                ],
                "edges": [edge("read", "delay", "changed", "trigger"), edge("value", "write", "value", "value")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        async def publish(_event: object) -> None:
            manager._graphs["graph"] = ("Graph", True, FlowData.model_validate({"nodes": [], "edges": []}))

        manager._event_bus.publish = AsyncMock(side_effect=publish)
        await manager._execute_graph("graph", "Graph", flow, _event(1))

        assert manager._timer_tasks == {}

    asyncio.run(exercise())


def test_a_run_during_the_save_gap_still_starts_its_delay() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 60)  # the sheet is briefly unloaded by a save

        await manager._execute_graph("graph", "Graph", flow, _event(1))

        assert ("graph", "delay") in manager._timer_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_run_in_flight_brings_no_timers_back_after_a_reset() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node("value", "const_value", {"value": "7", "data_type": "number"}),
                    node("write", "datapoint_write", {"datapoint_id": str(target)}),
                ],
                "edges": [edge("read", "delay", "changed", "trigger"), edge("value", "write", "value", "value")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        async def reset_mid_run(_event: object) -> None:
            await manager.reset_node_state("graph")

        manager._event_bus.publish = AsyncMock(side_effect=reset_mid_run)
        # The reset lands during the run's write, after it recorded the level.
        await manager._execute_graph("graph", "Graph", flow, {"read": {"value": 1, "changed": True}})

        assert manager._timer_tasks == {}
        assert manager._timers == {}  # the reset dropped the level; the run starts no delay

    asyncio.run(exercise())


def test_a_timer_behind_a_pulse_that_changed_meanwhile_is_left_to_the_pulse_run() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("const", "const_value", {"value": "1", "data_type": "number"}),
                    node("w", "datapoint_write", {"datapoint_id": str(uuid.uuid4())}),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                ],
                "edges": [edge("const", "w", "value", "value"), edge("pulse", "delay", "out", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60)}

        async def end_pulse(_event: object) -> None:
            pass

        manager._event_bus.publish = AsyncMock(side_effect=end_pulse)
        execute_impl = manager._execute_graph_impl

        async def ending_during_run(*args: object, **kwargs: object) -> dict:
            # The pulse ends after this run took its snapshot but before its timer section.
            original = manager._active_pulses
            calls = 0

            def active(graph_id: str) -> frozenset[str]:
                nonlocal calls
                calls += 1
                return original(graph_id) if calls == 1 else frozenset()

            manager._active_pulses = active  # type: ignore[method-assign]
            try:
                return await execute_impl(*args, **kwargs)
            finally:
                manager._active_pulses = original  # type: ignore[method-assign]

        manager._execute_graph_impl = ending_during_run  # type: ignore[method-assign]
        await manager._execute_graph("graph", "Graph", flow, {})

        # The delay saw out=True from the stale level: neither started nor recorded.
        assert "delay" not in manager._timers["graph"]
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_timer_task_cancelled_while_waiting_for_a_run_stays_pending() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["delay"] = _pending("timer_delay")
        in_flight = asyncio.get_running_loop().create_future()
        manager._timer_runs[("graph", "delay")] = in_flight

        task = asyncio.create_task(manager._run_timer_delay("graph", "delay"))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0)
        in_flight.set_result({})

        assert state.deadline is not None

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["timer_delay", "timer_pulse"])
def test_a_deadline_moved_while_waiting_for_a_run_is_slept_on(kind: str) -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4()) if kind == "timer_delay" else _pulse_flow(uuid.uuid4())
        manager._graphs["graph"] = ("Graph", True, flow)
        state = manager._timers.setdefault("graph", {})[flow.nodes[1].id] = _pending(kind)
        key = ("graph", flow.nodes[1].id)
        in_flight = asyncio.get_running_loop().create_future()
        manager._timer_runs[key] = in_flight
        run = manager._run_timer_delay if kind == "timer_delay" else manager._run_timer_pulse

        task = asyncio.create_task(run(*key))
        await asyncio.sleep(0.01)
        state.deadline = monotonic() + 60  # retriggered while waiting
        in_flight.set_result({})
        await asyncio.sleep(0.01)

        assert not task.done()
        manager._event_bus.publish.assert_not_awaited()
        task.cancel()

    asyncio.run(exercise())


def test_a_restarted_pulse_while_waiting_for_its_end_run_starts_again() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse")
        in_flight = asyncio.get_running_loop().create_future()
        manager._timer_runs[("graph", "pulse")] = in_flight

        task = asyncio.create_task(manager._run_timer_pulse("graph", "pulse"))
        await asyncio.sleep(0.01)
        state.need_on = True  # ended and retriggered by another run meanwhile
        in_flight.set_result({})
        await task

        assert _values(manager) == [True, False]

    asyncio.run(exercise())


def test_a_failed_run_during_a_save_is_not_taken_for_obsolete() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))

        async def failing(*_args: object, **_kwargs: object) -> dict:
            manager._ical_cache_generations["graph"] = object()  # a save at the same time
            return {}  # an execution error, logged by _execute_graph_impl

        manager._execute_graph = failing  # type: ignore[method-assign]

        assert await manager._run_timer_graph("graph", "delay", "timer_delay", {}, 0) == "error"

    asyncio.run(exercise())


def test_a_run_in_flight_brings_no_timers_back_after_a_bulk_delete() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node("value", "const_value", {"value": "7", "data_type": "number"}),
                    node("write", "datapoint_write", {"datapoint_id": str(uuid.uuid4())}),
                ],
                "edges": [edge("read", "delay", "changed", "trigger"), edge("value", "write", "value", "value")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        async def load_nothing() -> None:
            manager._graphs = {}

        manager._load_graphs = load_nothing  # type: ignore[method-assign]

        async def delete_all_during_the_write(_event: object) -> None:
            await manager.reload()  # DELETE FROM logic_graphs + reload()

        manager._event_bus.publish = AsyncMock(side_effect=delete_all_during_the_write)
        await manager._execute_graph("graph", "Graph", flow, _event(1))

        assert manager._timers == {} and manager._timer_tasks == {}
        assert "graph" not in manager._timer_epochs and "graph" not in manager._timers_adopted

    asyncio.run(exercise())


def test_stop_persists_timer_state_a_failed_save_left_behind() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _pending("timer_delay", in_s=60)}
        manager._timer_dirty.add("graph")

        await manager.stop()

        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]
        assert "timer_deadline" in saved["delay"]

    asyncio.run(exercise())


def test_deleting_a_sheet_drops_its_timer_epoch() -> None:
    manager = _manager()
    manager._timer_epochs["graph"] = 3

    manager.remove_graph("graph")

    assert "graph" not in manager._timer_epochs


def test_a_firing_obsoleted_again_and_again_is_retried_until_it_runs() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _pending("timer_delay")}
        manager._execute_graph = _obsolete_run(manager, *[{}] * 5, {"delay": {}})  # type: ignore[method-assign]

        await manager._run_timer_delay("graph", "delay")

        assert manager._execute_graph.await_count == 6
        assert manager._timers["graph"]["delay"].deadline is None

    asyncio.run(exercise())


def test_a_pulse_stays_off_while_its_end_is_retried() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 0))
        manager._timers["graph"] = {"pulse": _pending("timer_pulse")}
        levels: list[frozenset[str]] = []
        run = _obsolete_run(manager, {}, {}, {"pulse": {}})

        async def recording(*args: object, **kwargs: object) -> dict:
            levels.append(manager._active_pulses("graph"))
            return await run(*args, **kwargs)

        manager._execute_graph = recording  # type: ignore[method-assign]
        await manager._run_timer_pulse("graph", "pulse")

        assert levels == [frozenset()] * 3

    asyncio.run(exercise())


@pytest.mark.parametrize("when", ["during the run", "during the pause"])
def test_a_retrigger_while_an_end_is_retried_starts_the_pulse_again(when: str) -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _pulse_flow(uuid.uuid4(), 0)
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"pulse": _pending("timer_pulse")}
        runs: list[str] = []
        obsolete_once = _obsolete_run(manager, {})

        async def run(*_args: object, **_kwargs: object) -> dict:
            runs.append("on" if manager._active_pulses("graph") else "off")
            if len(runs) == 1:
                result = await obsolete_once(**_kwargs)
                if when == "during the run":
                    manager._start_timer("graph", flow.nodes[1], 0)
                return result
            return {"pulse": {}}

        manager._execute_graph = run  # type: ignore[method-assign]
        if when == "during the pause":
            wait = manager._wait_for_timer_run

            async def retrigger_in_pause(key: tuple[str, str]) -> None:
                if runs == ["off"] and manager._timers["graph"]["pulse"].deadline is None:
                    manager._start_timer("graph", flow.nodes[1], 0)
                await wait(key)

            manager._wait_for_timer_run = retrigger_in_pause  # type: ignore[method-assign]
        await manager._run_timer_pulse("graph", "pulse")

        assert runs == ["off", "on", "off"]

    asyncio.run(exercise())


def test_a_pulse_input_change_does_not_restart_a_delay_behind_the_pulse() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("cf", "change_filter"),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                ],
                "edges": [edge("cf", "pulse", "changed", "trigger"), edge("pulse", "delay", "out", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60, prev=True), "delay": _pending("timer_delay", in_s=60, prev=True)}
        deadline = manager._timers["graph"]["delay"].deadline

        # A new change extends the pulse; its output stays on, so the delay behind it keeps its start.
        await manager._execute_graph("graph", "Graph", flow, {"cf": {"in": 1}})

        assert manager._timers["graph"]["delay"].deadline == deadline
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_pulse_end_cancelled_during_its_retry_pause_stays_due() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 0))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse")
        manager._execute_graph = _obsolete_run(manager, {})  # type: ignore[method-assign]
        paused, never = asyncio.Event(), asyncio.Event()
        wait = manager._wait_for_timer_run

        async def block_in_pause(key: tuple[str, str]) -> None:
            if not manager._execute_graph.await_count:
                return await wait(key)
            paused.set()  # the retry pause after the obsolete end run
            await never.wait()

        manager._wait_for_timer_run = block_in_pause  # type: ignore[method-assign]
        task = asyncio.create_task(manager._run_timer_pulse("graph", "pulse"))
        await asyncio.wait_for(paused.wait(), 1)
        assert state.deadline is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert state.deadline is not None and "graph" in manager._timer_dirty

    asyncio.run(exercise())


def test_a_pulse_end_retriggered_during_its_cancelled_pause_keeps_the_new_end() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 0))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse")
        manager._execute_graph = _obsolete_run(manager, {})  # type: ignore[method-assign]
        paused, never = asyncio.Event(), asyncio.Event()
        wait = manager._wait_for_timer_run

        async def retrigger_in_pause(key: tuple[str, str]) -> None:
            if not manager._execute_graph.await_count:
                return await wait(key)
            state.deadline = 123.0  # retriggered in the pause
            paused.set()
            await never.wait()

        manager._wait_for_timer_run = retrigger_in_pause  # type: ignore[method-assign]
        task = asyncio.create_task(manager._run_timer_pulse("graph", "pulse"))
        await asyncio.wait_for(paused.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert state.deadline == 123.0

    asyncio.run(exercise())


def test_a_deleted_sheets_run_in_flight_starts_nothing_even_after_a_new_run_began() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 60)
        manager._graphs["graph"] = ("Graph", True, flow)
        flow.edges.append(flow.edges[2].model_copy(update={"id": "x", "target": "write2"}))
        flow.nodes.append(flow.nodes[3].model_copy(update={"id": "write2"}))
        flow.edges[-1] = flow.edges[-1].model_copy(update={"targetHandle": "value"})

        async def delete_and_rerun(_event: object) -> None:
            manager.remove_graph("graph")
            manager._timer_epochs.setdefault("graph", object())  # a run of the deleted sheet begins

        manager._event_bus.publish = AsyncMock(side_effect=delete_and_rerun)
        await manager._execute_graph("graph", "Graph", flow, _event(1))

        assert manager._timers == {} and manager._timer_tasks == {}

    asyncio.run(exercise())


def test_an_obsolete_run_that_already_acted_is_not_repeated() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _pending("timer_delay")}
        calls = 0

        async def acted_then_obsolete(*_args: object, obsolete_report: dict[str, bool], **_kwargs: object) -> dict:
            nonlocal calls
            calls += 1
            obsolete_report.update(obsolete=True, acted=True)  # e.g. a Wake-on-LAN packet went out before the save
            return {}

        manager._execute_graph = acted_then_obsolete  # type: ignore[method-assign]
        await manager._run_timer_delay("graph", "delay")

        assert calls == 1
        assert manager._timers["graph"]["delay"].deadline is None

    asyncio.run(exercise())


def test_execute_graph_reports_an_obsolete_run_that_acted() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4())
        reported: list[dict[str, bool]] = []

        for acted in (False, True):

            async def obsolete_impl(*_args: object, run_tag: int, acted: bool = acted, **_kwargs: object) -> dict:
                if acted:
                    manager._hems_acted_runs.add(run_tag)
                raise _ObsoleteGraphExecution

            manager._execute_graph_impl = obsolete_impl  # type: ignore[method-assign]
            report: dict[str, bool] = {}
            assert await manager._execute_graph("graph", "Graph", flow, {}, obsolete_report=report) == {}
            reported.append({key: value for key, value in report.items() if key != "run_tag"})
        assert await manager._execute_graph("graph", "Graph", flow, {}) == {}  # nobody asking

        assert reported == [{"obsolete": True, "acted": False}, {"obsolete": True, "acted": True}]

    asyncio.run(exercise())


def test_a_fall_while_a_timer_is_pending_is_persisted() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("level", "const_value", {"value": "false", "data_type": "bool"}), node("pulse", "timer_pulse", {"duration_s": 60})],
                "edges": [edge("level", "pulse", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60, prev=True)}

        await manager._execute_graph("graph", "Graph", flow, {})

        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]
        assert saved["pulse"]["timer_prev_trigger"] is False

    asyncio.run(exercise())


def test_stop_persists_a_pulse_end_its_cancelled_task_marked_as_due() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["pulse"] = _TimerState("timer_pulse")

        async def pause() -> None:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                state.deadline = monotonic()  # as the end-retry pause does
                manager._timer_dirty.add("graph")
                raise

        manager._timer_tasks[("graph", "pulse")] = asyncio.create_task(pause())
        await asyncio.sleep(0)

        await manager.stop()

        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]
        assert "timer_deadline" in saved["pulse"]

    asyncio.run(exercise())


def test_an_event_into_a_running_pulse_sends_no_second_notification() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                    node("ma", "message_archive", {"archive_id": "Alerts", "title": "Motion", "message": "on"}),
                ],
                "edges": [edge("read", "pulse", "value", "trigger"), edge("pulse", "ma", "out", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        service = MagicMock()
        service.record = AsyncMock(return_value={"id": "entry"})
        with patch("obs.message_archive.get_message_archive_service", return_value=service):
            await manager._execute_graph("graph", "Graph", flow, {"read": {"value": True, "changed": True}})
            await manager._run_timer_graph("graph", "pulse", "timer_pulse", {"pulse": {}}, 0)  # the start run
            service.record.assert_awaited_once()
            # Motion goes off while the pulse is still on: no news for the archive.
            await manager._execute_graph("graph", "Graph", flow, {"read": {"value": False, "changed": True}})

        service.record.assert_awaited_once()
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


@pytest.mark.parametrize("boundary", ["memory", "timer_delay"])
def test_a_write_behind_a_boundary_is_not_stale_when_a_pulse_starts(boundary: str) -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        handle = "out" if boundary == "memory" else "trigger"
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                    node("b", boundary, {"delay_s": 60} if boundary == "timer_delay" else {"initial_value": "7", "data_type": "number"}),
                    node("write", "datapoint_write", {"datapoint_id": str(target)}),
                ],
                "edges": [
                    edge("read", "pulse", "changed", "trigger"),
                    edge("pulse", "b", "out", "in" if boundary == "memory" else "trigger"),
                    edge("b", "write", handle, "value"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1))

        # The pulse started in this run, but the boundary's output does not follow it:
        # memory's committed value is written; an idle delay sends nothing at all.
        written = [call.args[0].datapoint_id for call in manager._event_bus.publish.await_args_list]
        assert written[:1] == ([target] if boundary == "memory" else [])
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_adopting_skips_a_block_that_opted_out_of_persistence() -> None:
    manager = _manager()
    flow = FlowData.model_validate({"nodes": [node("delay", "timer_delay", {"persist_state": False})], "edges": []})
    manager._graphs["graph"] = ("Graph", True, flow)
    manager._hysteresis["graph"] = {"delay": {"timer_prev_trigger": True, "timer_deadline": 1.0}}

    manager._adopt_persisted_timer_state()

    assert manager._timers == {}
    assert manager._hysteresis["graph"] == {}


@pytest.mark.parametrize("retriggered", [False, True])
def test_a_pulse_end_run_cut_short_by_stop_stays_due(retriggered: bool) -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 0))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse")
        started, never = asyncio.Event(), asyncio.Event()

        async def endless_end_run(*_args: object, **_kwargs: object) -> dict:
            if retriggered:
                state.deadline = 123.0
            started.set()
            await never.wait()
            return {}

        manager._execute_graph = endless_end_run  # type: ignore[method-assign]
        manager._spawn_timer("graph", "pulse", "timer_pulse")
        await asyncio.wait_for(started.wait(), 1)
        # As stop() does: the task first, then the run past the bound.
        manager._cancel_timer_tasks()
        runs = list(manager._timer_runs.values())
        for run in runs:
            run.cancel()
        await asyncio.wait(runs)

        assert state.deadline == 123.0 if retriggered else state.deadline is not None and "graph" in manager._timer_dirty

    asyncio.run(exercise())


def test_a_repeated_true_telegram_extends_a_running_pulse() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("pir", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}), node("pulse", "timer_pulse", {"duration_s": 60})],
                "edges": [edge("pir", "pulse", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._execute_graph("graph", "Graph", flow, {"pir": {"value": True, "changed": True}})
        deadline = manager._timers["graph"]["pulse"].deadline
        await asyncio.sleep(0.01)

        # The motion detector sends "1" again while someone is still there.
        await manager._execute_graph("graph", "Graph", flow, {"pir": {"value": True, "changed": True}})
        extended = manager._timers["graph"]["pulse"].deadline
        # An unrelated run (the value is only re-seeded) does not extend it.
        await manager._execute_graph("graph", "Graph", flow, {})

        assert extended > deadline
        assert manager._timers["graph"]["pulse"].deadline == extended
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["timer_delay", "timer_pulse"])
def test_a_firing_or_start_run_cut_short_by_stop_stays_due(kind: str) -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4()) if kind == "timer_delay" else _pulse_flow(uuid.uuid4(), 60)
        node_id = flow.nodes[1].id
        manager._graphs["graph"] = ("Graph", True, flow)
        state = manager._timers.setdefault("graph", {})[node_id] = _pending(
            kind, need_on=kind == "timer_pulse", in_s=0 if kind == "timer_delay" else 60
        )
        started, never = asyncio.Event(), asyncio.Event()

        async def endless(*_args: object, **_kwargs: object) -> dict:
            started.set()
            await never.wait()
            return {}

        manager._execute_graph = endless  # type: ignore[method-assign]
        manager._spawn_timer("graph", node_id, kind)
        await asyncio.wait_for(started.wait(), 1)
        assert (state.deadline is None) if kind == "timer_delay" else (state.need_on is False)

        stop = asyncio.create_task(manager.stop())
        await asyncio.sleep(0)
        for run in list(manager._timer_runs.values()):
            run.cancel()  # past the stop bound
        await stop

        assert (state.deadline is not None) if kind == "timer_delay" else (state.need_on is True)
        assert manager._db.execute_and_commit.await_count == 1  # persisted as still due

    asyncio.run(exercise())


def test_a_run_that_completes_after_its_task_is_cancelled_is_not_repeated() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["delay"] = _pending("timer_delay")
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(*_args: object, **_kwargs: object) -> dict:
            started.set()
            await release.wait()
            return {"delay": {}}

        manager._execute_graph = slow  # type: ignore[method-assign]
        manager._spawn_timer("graph", "delay", "timer_delay")
        await asyncio.wait_for(started.wait(), 1)
        manager._cancel_timer_tasks()
        release.set()
        await asyncio.sleep(0.01)

        assert state.deadline is None  # it fired; nothing to repeat after a restart

    asyncio.run(exercise())


def test_a_save_shortening_the_duration_applies_to_a_pending_timer() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 3600)
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._start_timer("graph", flow.nodes[1], 0)
        task = manager._timer_tasks[("graph", "delay")]

        flow.nodes[1].data["delay_s"] = 0  # saved with a much shorter delay
        manager._resume_timers()
        await asyncio.wait_for(task, 1)

        assert manager._event_bus.publish.await_count == 1

    asyncio.run(exercise())


def test_a_save_cancelled_mid_write_keeps_the_timer_state_dirty() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _pending("timer_delay", in_s=60)}
        manager._timer_dirty.add("graph")
        manager._db.execute_and_commit.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await manager._persist_node_state("graph")

        assert manager._timer_dirty == {"graph"}

    asyncio.run(exercise())


def test_a_cancelled_save_of_hysteresis_alone_marks_no_timers_dirty() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._hysteresis["graph"] = {"other": {"value": 1}}
        manager._db.execute_and_commit.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await manager._persist_node_state("graph")

        assert manager._timer_dirty == set()

    asyncio.run(exercise())


@pytest.mark.parametrize("trigger_from", ["pulse", "behind the pulse", "elsewhere"])
def test_a_write_triggered_by_a_stale_pulse_level_is_skipped(trigger_from: str) -> None:
    async def exercise() -> None:
        manager = _manager()
        target, other = uuid.uuid4(), uuid.uuid4()
        trigger_edge = {
            "pulse": edge("pulse", "write", "out", "trigger"),
            "behind the pulse": edge("or", "write", "out", "trigger"),
            "elsewhere": edge("level", "write", "value", "trigger"),
        }[trigger_from]
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("const", "const_value", {"value": "1", "data_type": "number"}),
                    node("w2", "datapoint_write", {"datapoint_id": str(other)}),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                    node("or", "or"),
                    node("level", "const_value", {"value": "true", "data_type": "bool"}),
                    node("write", "datapoint_write", {"datapoint_id": str(target)}),
                ],
                "edges": [
                    edge("const", "w2", "value", "value"),
                    edge("pulse", "or", "out", "in1"),
                    edge("const", "write", "value", "value"),
                    trigger_edge,
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60)}

        async def end_pulse(_event: object) -> None:
            manager._timers["graph"]["pulse"].deadline = None  # the pulse ends during the first write

        manager._event_bus.publish = AsyncMock(side_effect=end_pulse)
        await manager._execute_graph("graph", "Graph", flow, {})

        written = [call.args[0].datapoint_id for call in manager._event_bus.publish.await_args_list]
        # A trigger that follows the pulse fired from its old level: skipped.
        assert written == ([other, target] if trigger_from == "elsewhere" else [other])

    asyncio.run(exercise())


def test_a_cut_firing_keeps_a_deadline_a_retrigger_set_meanwhile() -> None:
    manager = _manager()
    state = _TimerState("timer_delay", deadline=123.0)
    manager._timers["graph"] = {"delay": state}

    manager._timer_cut("graph", state, "deadline")

    assert state.deadline == 123.0 and "graph" in manager._timer_dirty


@pytest.mark.parametrize(("delay_s", "depth"), [(0, 5), (0.01, 5), (0.05, 1)])
def test_a_delay_starts_a_new_cascade_only_after_a_real_wait(delay_s: float, depth: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("obs.logic.manager._TIMER_NEW_CASCADE_S", 0.05)

    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = _delay_flow(target, delay_s)
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1), logic_depth=3)
        await manager._timer_tasks[("graph", "delay")]

        assert _published(manager) == [(str(target), 7, depth)]

    asyncio.run(exercise())


def test_a_timer_whose_block_is_gone_counts_one_more_step() -> None:
    manager = _manager()
    state = _TimerState("timer_delay", depth=5)

    assert manager._timer_run_depth("graph", "delay", state) == 6


def test_stopping_does_not_hold_a_pulse_end_whose_run_completes() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(target, 0))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse")
        execute = manager._execute_graph
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_end_run(*args: object, **kwargs: object) -> dict:
            started.set()
            await release.wait()
            return await execute(*args, **kwargs)

        manager._execute_graph = slow_end_run  # type: ignore[method-assign]
        manager._spawn_timer("graph", "pulse", "timer_pulse")
        await asyncio.wait_for(started.wait(), 1)
        stop = asyncio.create_task(manager.stop())
        await asyncio.sleep(0)
        release.set()
        await stop

        assert _values(manager) == [False]  # the "off" went out
        assert state.deadline is None

    asyncio.run(exercise())


def test_stop_waits_for_runs_it_cut_short_before_saving(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("obs.logic.manager._HEMS_STOP_TIMEOUT_S", 0.05)

    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _pending("timer_delay")}
        never = asyncio.Event()

        async def endless(*_args: object, **_kwargs: object) -> dict:
            await never.wait()
            return {}

        manager._execute_graph = endless  # type: ignore[method-assign]
        manager._spawn_timer("graph", "delay", "timer_delay")
        await asyncio.sleep(0.01)

        await manager.stop()

        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]
        assert "timer_deadline" in saved["delay"]

    asyncio.run(exercise())


@pytest.mark.parametrize(("wiring", "restarted"), [("direct", True), ("behind a condition", False), ("false telegram", False)])
def test_only_a_true_telegram_wired_straight_to_the_trigger_restarts_a_delay(wiring: str, restarted: bool) -> None:
    async def exercise() -> None:
        manager = _manager()
        if wiring == "behind a condition":
            flow = FlowData.model_validate(
                {
                    "nodes": [
                        node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                        node("cmp", "compare", {"operator": ">", "operand": 25}),
                        node("delay", "timer_delay", {"delay_s": 60}),
                    ],
                    "edges": [edge("read", "cmp", "value", "in1"), edge("cmp", "delay", "out", "trigger")],
                },
            )
            values = (30, 31)
        else:
            flow = FlowData.model_validate(
                {
                    "nodes": [node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}), node("delay", "timer_delay", {"delay_s": 60})],
                    "edges": [edge("read", "delay", "value", "trigger")],
                },
            )
            values = (True, True) if wiring == "direct" else (True, "1")
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._execute_graph("graph", "Graph", flow, {"read": {"value": values[0], "changed": True}})
        deadline = manager._timers["graph"]["delay"].deadline
        await asyncio.sleep(0.01)
        if wiring == "false telegram":
            await manager._execute_graph("graph", "Graph", flow, {"read": {"value": False, "changed": True}})
        else:
            await manager._execute_graph("graph", "Graph", flow, {"read": {"value": values[1], "changed": True}})

        timer = manager._timers["graph"]["delay"]
        assert (timer.deadline > deadline) is restarted
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_pulse_a_save_removed_meanwhile_does_not_start() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _pulse_flow(uuid.uuid4(), 60)
        # The run still holds the old sheet; the saved one has no pulse any more.
        manager._graphs["graph"] = ("Graph", True, FlowData.model_validate({"nodes": [], "edges": []}))

        await manager._execute_graph("graph", "Graph", flow, _event(1))

        assert manager._timer_tasks == {}

    asyncio.run(exercise())


def test_a_pulse_end_after_a_real_wait_starts_a_new_cascade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("obs.logic.manager._TIMER_NEW_CASCADE_S", 0.05)

    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = _pulse_flow(target, 0.05)
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1), logic_depth=2)
        await manager._timer_tasks[("graph", "pulse")]

        assert _published(manager) == [(str(target), True, 4), (str(target), False, 1)]

    asyncio.run(exercise())


def test_a_repeated_telegram_counts_with_the_value_the_block_received() -> None:
    async def exercise() -> None:
        manager = _manager()
        # An inverted motion sensor: raw 0 means present.
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("pir", "datapoint_read", {"datapoint_id": str(uuid.uuid4()), "value_formula": "1-x"}),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                ],
                "edges": [edge("pir", "pulse", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._execute_graph("graph", "Graph", flow, {"pir": {"value": 0, "changed": True}})
        deadline = manager._timers["graph"]["pulse"].deadline
        await asyncio.sleep(0.01)

        await manager._execute_graph("graph", "Graph", flow, {"pir": {"value": 0, "changed": True}})

        assert manager._timers["graph"]["pulse"].deadline > deadline
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_stop_does_not_hang_on_a_cut_run_that_will_not_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("obs.logic.manager._HEMS_STOP_TIMEOUT_S", 0.01)
    monkeypatch.setattr("obs.logic.manager._TIMER_STOP_CUT_WAIT_S", 0.01)

    async def exercise() -> None:
        manager = _manager()
        release = asyncio.Event()

        async def stubborn() -> None:
            # Like a run waiting on a Python-script worker thread: it ignores cancellation.
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue

        run = asyncio.ensure_future(stubborn())
        manager._timer_runs[("graph", "delay")] = run

        await asyncio.wait_for(manager.stop(), 1)

        assert not run.done()
        release.set()
        await run

    asyncio.run(exercise())


def test_a_short_timer_loop_within_one_sheet_is_stopped() -> None:
    async def exercise() -> None:
        manager = _manager()
        # pulse -> memory -> delay -> OR -> pulse: no data point event in between.
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("or", "or"),
                    node("pulse", "timer_pulse", {"duration_s": 0.001}),
                    node("memory", "memory"),
                    node("delay", "timer_delay", {"delay_s": 0.001}),
                ],
                "edges": [
                    edge("read", "or", "changed", "in1"),
                    edge("delay", "or", "trigger", "in2"),
                    edge("or", "pulse", "out", "trigger"),
                    edge("pulse", "memory", "out", "in"),
                    edge("memory", "delay", "out", "trigger"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        runs = 0
        execute = manager._execute_graph

        async def counting(*args: object, **kwargs: object) -> dict:
            nonlocal runs
            runs += 1
            return await execute(*args, **kwargs)

        manager._execute_graph = counting  # type: ignore[method-assign]
        await manager._execute_graph("graph", "Graph", flow, _event(1))
        await asyncio.sleep(0.5)

        assert manager._timer_tasks == {}  # it stopped by itself
        assert runs < 60

    asyncio.run(exercise())


def test_a_run_cut_short_after_it_acted_is_not_repeated() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["delay"] = _pending("timer_delay")
        acted, never = asyncio.Event(), asyncio.Event()

        async def acting_impl(*_args: object, run_tag: int, **_kwargs: object) -> dict:
            manager._hems_acted_runs.add(run_tag)  # e.g. the write went out
            acted.set()
            await never.wait()
            return {}

        manager._execute_graph_impl = acting_impl  # type: ignore[method-assign]
        manager._spawn_timer("graph", "delay", "timer_delay")
        await asyncio.wait_for(acted.wait(), 1)
        manager._cancel_timer_tasks()
        runs = list(manager._timer_runs.values())
        for run in runs:
            run.cancel()  # cut short by stop()
        await asyncio.wait(runs)

        assert state.deadline is None  # it fired: no repeat after a restart

    asyncio.run(exercise())


def test_an_idle_delay_sends_nothing_to_a_value_or_through_logic() -> None:
    async def exercise() -> None:
        manager = _manager()
        target, inverted = uuid.uuid4(), uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("other", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("delay", "timer_delay"),
                    node("not", "not"),
                    node("w", "datapoint_write", {"datapoint_id": str(target)}),
                    node("w2", "datapoint_write", {"datapoint_id": str(inverted)}),
                ],
                "edges": [
                    edge("delay", "w", "trigger", "value"),
                    edge("delay", "not", "trigger", "in1"),
                    edge("not", "w2", "out", "trigger"),
                    edge("other", "w2", "value", "value"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        # An unrelated event: the delay did not fire, so neither write goes out.
        await manager._execute_graph("graph", "Graph", flow, {"other": {"value": 5, "changed": True}})
        manager._event_bus.publish.assert_not_awaited()
        await manager._execute_graph("graph", "Graph", flow, {"delay": {"_fire": True}})

        assert _published(manager) == [(str(target), True, 1)]

    asyncio.run(exercise())


def test_a_new_telegram_after_a_start_refused_at_the_cascade_limit_starts_the_pulse() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _pulse_flow(uuid.uuid4(), 60)
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {"read": {"value": True, "changed": True}}, logic_depth=10)
        assert manager._timer_tasks == {}

        await manager._execute_graph("graph", "Graph", flow, {"read": {"value": True, "changed": True}})
        assert ("graph", "pulse") in manager._timer_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_start_refused_at_the_cascade_limit_uses_up_its_edge() -> None:
    """Otherwise any later run would restart a runaway loop at depth 0."""

    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("level", "const_value", {"value": "true", "data_type": "bool"}), node("delay", "timer_delay", {"delay_s": 0.5})],
                "edges": [edge("level", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {}, logic_depth=10)
        await manager._execute_graph("graph", "Graph", flow, {})

        assert manager._timer_tasks == {}
        assert manager._timers["graph"]["delay"].prev_trigger is True

    asyncio.run(exercise())


def test_a_late_start_retry_after_the_pulse_ran_out_only_ends_it() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 0))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse", need_on=True)
        levels: list[bool] = []
        run = _obsolete_run(manager, {}, {"pulse": {}})

        async def recording(*args: object, **kwargs: object) -> dict:
            levels.append(bool(manager._active_pulses("graph")))
            return await run(*args, **kwargs)

        manager._execute_graph = recording  # type: ignore[method-assign]
        await manager._run_timer_pulse("graph", "pulse")

        # The start failed; by the retry the pulse had run out: only the end run, no "on" blip.
        assert levels == [True, False]
        assert state.need_on is False

    asyncio.run(exercise())


def test_a_pulse_end_that_failed_before_acting_is_retried_until_it_ends() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 0))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse")
        manager._execute_graph = AsyncMock(side_effect=[{}, {"pulse": {}}])  # type: ignore[method-assign]

        await manager._run_timer_pulse("graph", "pulse")

        assert manager._execute_graph.await_count == 2
        assert (state.deadline, state.errors) == (None, 0)

    asyncio.run(exercise())


def test_a_pulse_start_cancelled_in_its_retry_pause_stays_due() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _pulse_flow(uuid.uuid4(), 60))
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse", in_s=60, need_on=True)
        failed = asyncio.Event()
        run = _obsolete_run(manager, {})

        async def obsolete_then_signal(*args: object, **kwargs: object) -> dict:
            result = await run(*args, **kwargs)
            failed.set()
            return result

        manager._execute_graph = obsolete_then_signal  # type: ignore[method-assign]
        monkey_delay = 60.0
        import obs.logic.manager as manager_module

        original = manager_module._TIMER_RETRY_DELAY_S
        manager_module._TIMER_RETRY_DELAY_S = monkey_delay
        try:
            task = asyncio.create_task(manager._run_timer_pulse("graph", "pulse"))
            await asyncio.wait_for(failed.wait(), 1)
            await asyncio.sleep(0.01)  # into the retry pause
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            manager_module._TIMER_RETRY_DELAY_S = original

        assert state.need_on is True and "graph" in manager._timer_dirty

    asyncio.run(exercise())


@pytest.mark.parametrize("failing", ["source", "timer"])
def test_a_run_with_a_failed_trigger_records_no_level(failing: str) -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("src", "const_value", {"value": "true", "data_type": "bool"}), node("delay", "timer_delay", {"delay_s": 60})],
                "edges": [edge("src", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._execute_graph("graph", "Graph", flow, {})
        deadline = manager._timers["graph"]["delay"].deadline
        execute = GraphExecutor._eval_node

        def failing_eval(self: GraphExecutor, node: object, inputs: dict) -> dict:
            if node.id == ("src" if failing == "source" else "delay"):  # type: ignore[attr-defined]
                raise RuntimeError("boom")
            return execute(self, node, inputs)  # type: ignore[arg-type]

        with patch.object(GraphExecutor, "_eval_node", failing_eval):
            await manager._execute_graph("graph", "Graph", flow, {})
        await manager._execute_graph("graph", "Graph", flow, {})

        # The failed run was no falling edge, so the good one is no new rise.
        assert manager._timers["graph"]["delay"].deadline == deadline
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_successful_run_resets_the_error_count() -> None:
    state = _TimerState("timer_delay", errors=2)

    assert LogicManager._timer_retry_due("graph", "delay", state, "ran") is False
    assert state.errors == 0


def test_a_read_object_without_a_value_yet_is_no_falling_edge() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}), node("delay", "timer_delay", {"delay_s": 60})],
                "edges": [edge("read", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        # Restored after a restart: the input was high.
        manager._timers["graph"] = {"delay": _TimerState("timer_delay", prev_trigger=True)}

        await manager._execute_graph("graph", "Graph", flow, {})  # the Read Object has no value yet
        assert manager._timers["graph"]["delay"].prev_trigger is True
        await manager._execute_graph("graph", "Graph", flow, {"read": {"value": True, "changed": False}})

        assert manager._timer_tasks == {}  # still high: no new rising edge

    asyncio.run(exercise())


def test_the_upstream_trace_stops_at_tick_boundaries_and_visits_each_block_once() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("unseeded", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("memory", "memory", {"initial_value": "true", "data_type": "bool"}),
                    node("level", "const_value", {"value": "true", "data_type": "bool"}),
                    node("or", "or"),
                    node("and", "and"),
                    node("delay", "timer_delay", {"delay_s": 60}),
                ],
                "edges": [
                    edge("unseeded", "memory", "value", "in"),
                    edge("memory", "or", "out", "in1"),
                    edge("level", "or", "value", "in2"),
                    edge("or", "and", "out", "in1"),
                    edge("or", "and", "out", "in2"),
                    edge("and", "delay", "out", "trigger"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {})

        # The unseeded Read Object sits behind memory: the level counts and starts the delay.
        assert ("graph", "delay") in manager._timer_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def _self_retriggering_delay(delay_s: float) -> FlowData:
    """Read "changed" → OR → Delay, and the Delay's own output back into the OR."""
    return FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                node("or", "or"),
                node("delay", "timer_delay", {"delay_s": delay_s}),
            ],
            "edges": [
                edge("read", "or", "changed", "in1"),
                edge("delay", "or", "trigger", "in2"),
                edge("or", "delay", "out", "trigger"),
            ],
        },
    )


def test_a_feedback_loop_through_a_timer_is_no_cycle() -> None:
    flow = _self_retriggering_delay(1)

    analysis = analyze_topology(flow)

    assert analysis.skipped_node_ids == set()
    assert topology_warnings(flow) == []


def test_a_delay_retriggering_itself_keeps_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("obs.logic.manager._TIMER_NEW_CASCADE_S", 0.01)

    async def exercise() -> None:
        manager = _manager()
        flow = _self_retriggering_delay(0.02)
        manager._graphs["graph"] = ("Graph", True, flow)
        fires = 0
        execute = manager._execute_graph

        async def counting(*args: object, **kwargs: object) -> dict:
            nonlocal fires
            overrides = args[3]
            fires += bool(overrides.get("delay", {}).get("_fire"))  # type: ignore[union-attr]
            return await execute(*args, **kwargs)

        manager._execute_graph = counting  # type: ignore[method-assign]
        await manager._execute_graph("graph", "Graph", flow, _event(1))
        for _ in range(500):  # poll, not a fixed window: a loaded runner is slower
            if fires > 10:
                break
            await asyncio.sleep(0.01)

        # A keep-alive: every firing re-arms the delay, past the cascade limit of ten.
        assert fires > 10
        assert ("graph", "delay") in manager._timer_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_short_delay_retriggering_itself_is_stopped_by_the_cascade_limit() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _self_retriggering_delay(0)
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, _event(1))
        await asyncio.sleep(0.2)

        assert manager._timer_tasks == {}

    asyncio.run(exercise())


def test_a_timer_evaluated_before_its_trigger_source_still_records_the_level() -> None:
    flow = _self_retriggering_delay(60)
    # The tick boundary lets the delay run before the OR feeding it.
    order = [node.id for node in analyze_topology(flow).order]
    assert order.index("delay") < order.index("or")

    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, flow)

        outputs = await manager._execute_graph("graph", "Graph", flow, _event(1))

        assert "__error__" not in outputs["delay"]
        assert manager._timers["graph"]["delay"].prev_trigger is True
        assert ("graph", "delay") in manager._timer_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_delay_that_did_not_fire_starts_no_timer_through_not() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("other", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node("not", "not"),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                ],
                "edges": [edge("delay", "not", "trigger", "in1"), edge("not", "pulse", "out", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {"other": {"value": 1, "changed": True}})

        assert ("graph", "pulse") not in manager._timer_tasks

    asyncio.run(exercise())


def test_a_definite_high_level_starts_the_timer_though_another_input_has_no_value_yet() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("a", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("b", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("or", "or"),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                ],
                "edges": [edge("a", "or", "value", "in1"), edge("b", "or", "value", "in2"), edge("or", "pulse", "out", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {"a": {"value": True, "changed": True}})

        assert ("graph", "pulse") in manager._timer_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


@pytest.mark.parametrize("settled_before_cancel", [False, True])
def test_a_firing_that_failed_after_its_task_was_cancelled_stays_due(settled_before_cancel: bool) -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        state = manager._timers.setdefault("graph", {})["delay"] = _pending("timer_delay")
        started, release = asyncio.Event(), asyncio.Event()

        async def failing(*_args: object, **_kwargs: object) -> dict:
            started.set()
            await release.wait()
            return {}  # failed before it published anything

        manager._execute_graph = failing  # type: ignore[method-assign]
        manager._spawn_timer("graph", "delay", "timer_delay")
        await asyncio.wait_for(started.wait(), 1)
        task = manager._timer_tasks[("graph", "delay")]
        if settled_before_cancel:
            release.set()
            await asyncio.sleep(0)  # the run is done; its task has not resumed yet
        task.cancel()
        release.set()
        await asyncio.sleep(0.01)

        assert state.deadline is not None and "graph" in manager._timer_dirty

    asyncio.run(exercise())


def test_a_reload_leaves_the_retry_pause_of_a_short_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("obs.logic.manager._TIMER_RETRY_DELAY_S", 1.0)
    manager = _manager()
    manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4(), 0))
    state = _pending("timer_delay", in_s=0.9)
    state.retry_at = state.deadline  # re-armed for a retry
    manager._timers["graph"] = {"delay": state}
    manager._timer_tasks[("graph", "delay")] = MagicMock(done=MagicMock(return_value=False))
    deadline = state.deadline

    manager._resume_timers()

    assert state.deadline == deadline


@pytest.mark.parametrize("duration_s", [0, 0.2])
def test_a_save_shortening_a_pending_delay_below_the_retry_pause_applies_it(monkeypatch: pytest.MonkeyPatch, duration_s: float) -> None:
    monkeypatch.setattr("obs.logic.manager._TIMER_RETRY_DELAY_S", 1.0)

    async def exercise() -> None:
        manager = _manager()
        flow = _delay_flow(uuid.uuid4(), 3600)
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._start_timer("graph", flow.nodes[1], 0)
        task = manager._timer_tasks[("graph", "delay")]
        started = monotonic()

        flow.nodes[1].data["delay_s"] = duration_s
        manager._resume_timers()
        await asyncio.wait_for(task, 0.8)

        assert manager._event_bus.publish.await_count == 1
        assert monotonic() - started < 0.8

    asyncio.run(exercise())


def test_a_pending_delay_from_an_earlier_retry_gets_the_saved_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("obs.logic.manager._TIMER_RETRY_DELAY_S", 1.0)
    manager = _manager()
    manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4(), 0))
    state = _pending("timer_delay", in_s=0.9)
    state.retry_at = monotonic() - 5  # retried once; retriggered since
    manager._timers["graph"] = {"delay": state}
    manager._timer_tasks[("graph", "delay")] = MagicMock(done=MagicMock(return_value=False))

    manager._resume_timers()

    assert state.deadline <= monotonic()


@pytest.mark.parametrize("acted", [False, True])
def test_stop_saves_a_timer_whose_run_will_not_settle_as_due_unless_it_acted(monkeypatch: pytest.MonkeyPatch, acted: bool) -> None:
    monkeypatch.setattr("obs.logic.manager._HEMS_STOP_TIMEOUT_S", 0.01)
    monkeypatch.setattr("obs.logic.manager._TIMER_STOP_CUT_WAIT_S", 0.01)

    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._timers["graph"] = {"delay": _pending("timer_delay")}
        started, release = asyncio.Event(), asyncio.Event()

        async def stubborn(*_args, obsolete_report, **_kwargs) -> dict:
            # Like a run waiting on a Python-script worker thread: it ignores cancellation.
            obsolete_report["run_tag"] = tag = object()
            if acted:
                manager._hems_acted_runs.add(tag)
            started.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return {"delay": {}}

        manager._execute_graph = stubborn
        manager._spawn_timer("graph", "delay", "timer_delay")
        await started.wait()

        await asyncio.wait_for(manager.stop(), 1)

        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]["delay"]
        assert ("timer_deadline" in saved) is not acted
        release.set()
        await asyncio.gather(*manager._timer_runs.values(), return_exceptions=True)
        assert manager._timer_cut_hooks == {}

    asyncio.run(exercise())


def test_a_timer_run_stopped_before_it_started_stays_due() -> None:
    manager = _manager()
    state = _TimerState("timer_delay")
    manager._timers["graph"] = {"delay": state}

    manager._timer_cut_unless_acted({}, lambda: manager._timer_cut("graph", state, "deadline"))

    assert state.deadline is not None


def test_the_debug_view_shows_the_trigger_level_of_a_timer() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _self_retriggering_delay(60)
        manager._graphs["graph"] = ("Graph", True, flow)
        capture: dict = {}

        outputs = await manager._execute_graph("graph", "Graph", flow, _event(1), debug_input_capture=capture)

        assert capture["delay"]["trigger"]["effective"] is True
        assert outputs["delay"]["_triggered"] is True
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_the_upstream_trace_of_a_low_level_visits_a_shared_source_once() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("src", "const_value", {"value": "false", "data_type": "bool"}),
                    node("and", "and"),
                    node("delay", "timer_delay", {"delay_s": 60}),
                ],
                "edges": [edge("src", "and", "value", "in1"), edge("src", "and", "value", "in2"), edge("and", "delay", "out", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"delay": _TimerState("timer_delay", prev_trigger=True)}

        await manager._execute_graph("graph", "Graph", flow, {})

        assert "graph" not in manager._timers  # a known low level: the fall is recorded

    asyncio.run(exercise())


def test_a_start_refused_at_the_cascade_limit_is_persisted() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("level", "const_value", {"value": "true", "data_type": "bool"}), node("delay", "timer_delay", {"delay_s": 0.5})],
                "edges": [edge("level", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {}, logic_depth=10)

        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]
        assert saved == {"delay": {"timer_prev_trigger": True}}

    asyncio.run(exercise())


def test_the_debug_view_shows_an_overridden_timer_trigger_beside_its_incoming_value() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [node("level", "const_value", {"value": "false", "data_type": "bool"}), node("delay", "timer_delay", {"delay_s": 60})],
                "edges": [edge("level", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        _outputs, capture = await manager.execute_graph_debug("graph", {"delay": {"trigger": True}})

        assert capture["delay"]["trigger"] == {"incoming": False, "effective": True, "overridden": True}
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_fall_is_recorded_though_another_input_has_no_value_yet() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("a", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("b", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("or", "or"),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                ],
                "edges": [edge("a", "or", "value", "in1"), edge("b", "or", "value", "in2"), edge("or", "pulse", "out", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {"a": {"value": True, "changed": True}})
        await manager._execute_graph("graph", "Graph", flow, {"a": {"value": False, "changed": True}})
        assert manager._timers["graph"]["pulse"].prev_trigger is False
        deadline = manager._timers["graph"]["pulse"].deadline
        await asyncio.sleep(0.01)
        await manager._execute_graph("graph", "Graph", flow, {"a": {"value": True, "changed": True}})

        assert manager._timers["graph"]["pulse"].deadline > deadline  # retriggered
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_delay_firing_behind_a_pulse_that_ended_meanwhile_is_still_written() -> None:
    async def exercise() -> None:
        manager = _manager()
        target, other = uuid.uuid4(), uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("const", "const_value", {"value": "1", "data_type": "number"}),
                    node("w2", "datapoint_write", {"datapoint_id": str(other)}),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node("write", "datapoint_write", {"datapoint_id": str(target)}),
                ],
                "edges": [
                    edge("const", "w2", "value", "value"),
                    edge("pulse", "delay", "out", "trigger"),
                    edge("delay", "write", "trigger", "trigger"),
                    edge("const", "write", "value", "value"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60, prev=True), "delay": _TimerState("timer_delay", prev_trigger=True)}

        async def end_pulse(_event: object) -> None:
            manager._timers["graph"]["pulse"].deadline = None  # the pulse ends during the first write

        manager._event_bus.publish = AsyncMock(side_effect=end_pulse)
        await manager._execute_graph("graph", "Graph", flow, {"delay": {"_fire": True}})

        # The delay's firing does not follow the pulse level: it is written.
        assert [call.args[0].datapoint_id for call in manager._event_bus.publish.await_args_list] == [other, target]
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_reload_drops_dirty_marks_of_sheets_that_are_gone() -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._timer_dirty.add("gone")

        async def load_nothing() -> None:
            manager._graphs = {}

        manager._load_graphs = load_nothing  # type: ignore[method-assign]
        await manager.reload()

        assert manager._timer_dirty == set()

    asyncio.run(exercise())


def test_a_pulse_restarting_itself_through_not_writes_its_off_too() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("pulse", "timer_pulse", {"duration_s": 0.02}),
                    node("not", "not"),
                    node("write", "datapoint_write", {"datapoint_id": str(target)}),
                ],
                "edges": [edge("pulse", "not", "out", "in1"), edge("not", "pulse", "out", "trigger"), edge("pulse", "write", "out", "value")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {})
        await asyncio.sleep(0.15)
        manager._cancel_timer_tasks()

        values = _values(manager)
        # A blinker: every end run writes its "off" though it restarts the pulse at once.
        assert True in values and False in values[values.index(True) :]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("kind", "config", "depth", "started"),
    [
        ("timer_pulse", {"duration_s": 60}, 9, False),  # its start run would be at the limit
        ("timer_pulse", {"duration_s": 60}, 8, True),
        ("timer_delay", {"delay_s": 0.5}, 9, False),  # a short delay fires one step further
        ("timer_delay", {"delay_s": 60}, 10, True),  # a long one starts a new cascade
    ],
)
def test_a_timer_runs_no_sheet_at_the_cascade_limit(kind: str, config: dict, depth: int, started: bool) -> None:
    manager = _manager()
    timer = FlowData.model_validate({"nodes": [node("timer", kind, config)], "edges": []}).nodes[0]
    manager._spawn_timer = MagicMock()  # type: ignore[method-assign]

    assert manager._start_timer("graph", timer, depth) is started


def test_a_blinker_restarts_a_delay_behind_it_on_every_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("pulse", "timer_pulse", {"duration_s": 0.03}),
                    node("not", "not"),
                    node("delay", "timer_delay", {"delay_s": 0}),
                    node("value", "const_value", {"value": "1", "data_type": "number"}),
                    node("write", "datapoint_write", {"datapoint_id": str(target)}),
                ],
                "edges": [
                    edge("pulse", "not", "out", "in1"),
                    edge("not", "pulse", "out", "trigger"),
                    edge("pulse", "delay", "out", "trigger"),
                    edge("delay", "write", "trigger", "trigger"),
                    edge("value", "write", "value", "value"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager._execute_graph("graph", "Graph", flow, {})
        await asyncio.sleep(0.3)
        manager._cancel_timer_tasks()

        writes = [call for call in manager._event_bus.publish.await_args_list if call.args[0].datapoint_id == target]
        assert len(writes) >= 2  # the delay fired again for a later pulse

    asyncio.run(exercise())


def test_a_slower_run_starts_no_sequence_from_a_pulse_level_that_changed_meanwhile() -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("const", "const_value", {"value": "1", "data_type": "number"}),
                    node("w2", "datapoint_write", {"datapoint_id": str(uuid.uuid4())}),
                    node("pulse", "timer_pulse", {"duration_s": 60}),
                    node("sequence", "value_sequence", {"steps": [{"datapoint_id": str(target), "value": 1}]}),
                ],
                "edges": [edge("const", "w2", "value", "value"), edge("pulse", "sequence", "out", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"pulse": _pending("timer_pulse", in_s=60)}
        execute_impl = manager._execute_graph_impl

        async def ending_during_run(*args: object, **kwargs: object) -> dict:
            # The pulse ends after this run took its snapshot but before its sequence step.
            original = manager._active_pulses
            calls = 0

            def active(graph_id: str) -> frozenset[str]:
                nonlocal calls
                calls += 1
                return original(graph_id) if calls == 1 else frozenset()

            manager._active_pulses = active  # type: ignore[method-assign]
            try:
                return await execute_impl(*args, **kwargs)
            finally:
                manager._active_pulses = original  # type: ignore[method-assign]

        manager._execute_graph_impl = ending_during_run  # type: ignore[method-assign]
        await manager._execute_graph("graph", "Graph", flow, {})

        assert ("graph", "sequence") not in manager._sequence_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_timer_run_at_the_cascade_limit_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        manager = _manager()
        manager._graphs["graph"] = ("Graph", True, _delay_flow(uuid.uuid4()))
        manager._execute_graph = AsyncMock()  # type: ignore[method-assign]

        assert await manager._run_timer_graph("graph", "delay", "timer_delay", {}, 10) == "ran"
        manager._execute_graph.assert_not_awaited()

    with caplog.at_level(logging.WARNING, logger="obs.logic.manager"):
        asyncio.run(exercise())

    assert any("timer run" in record.message for record in caplog.records)


def test_saving_a_sheet_with_a_feedback_loop_through_a_timer_reports_no_cycle(caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("or", "or"),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node("write", "datapoint_write", {"datapoint_id": str(target)}),
                ],
                "edges": [
                    edge("read", "or", "value", "in1"),
                    edge("delay", "or", "trigger", "in2"),
                    edge("or", "delay", "out", "trigger"),
                    edge("or", "write", "out", "value"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)

        await manager.initialize_graph("graph")

    with caplog.at_level(logging.WARNING):
        asyncio.run(exercise())

    assert not any("cycle" in record.message for record in caplog.records)


def test_a_cut_run_of_a_forgotten_timer_marks_nothing_dirty() -> None:
    manager = _manager()
    state = _TimerState("timer_delay")

    manager._timer_cut("graph", state, "deadline")

    assert state.deadline is None and manager._timer_dirty == set()


@pytest.mark.parametrize("pending_before", [False, True])
def test_a_slower_run_starts_no_delay_after_a_newer_run_saw_the_fall(pending_before: bool) -> None:
    async def exercise() -> None:
        manager = _manager()
        source = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(source)}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node("const", "const_value", {"value": "1", "data_type": "number"}),
                    node("write", "datapoint_write", {"datapoint_id": str(uuid.uuid4())}),
                ],
                "edges": [edge("read", "delay", "value", "trigger"), edge("const", "write", "value", "value")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        earlier = None
        if pending_before:
            # A delay still pending keeps its state through the fall.
            manager._timers["graph"] = {"delay": _pending("timer_delay", in_s=60)}
            earlier = manager._timers["graph"]["delay"].deadline
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def publish(_event: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()

        manager._event_bus.publish.side_effect = publish

        def value_event(value: bool) -> SimpleNamespace:
            return SimpleNamespace(datapoint_id=source, value=value, logic_depth=0, initialization=False, suppress_action_triggers=False)

        first = asyncio.create_task(manager._on_value_event(value_event(True)))
        await entered.wait()
        await manager._on_value_event(value_event(False))
        release.set()
        await first

        state = manager._timers.get("graph", {}).get("delay")
        if pending_before:
            assert state is not None and state.deadline == earlier and state.prev_trigger is False
        else:
            assert state is None
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_pulse_is_held_for_its_duration_from_the_start_runs_writes_on() -> None:
    import httpx

    async def exercise() -> None:
        manager = _manager()
        target = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                    node("pulse", "timer_pulse", {"duration_s": 0.05}),
                    node("api", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"}),
                    node("write", "datapoint_write", {"datapoint_id": str(target)}),
                ],
                "edges": [
                    edge("read", "pulse", "changed", "trigger"),
                    edge("pulse", "api", "out", "trigger"),
                    edge("pulse", "write", "out", "value"),
                ],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        seen: list[tuple[object, float]] = []

        async def publish(event: object) -> None:
            if str(event.datapoint_id) == str(target):  # type: ignore[attr-defined]
                seen.append((event.value, monotonic()))  # type: ignore[attr-defined]

        async def slow_request(*_args: object, **_kwargs: object) -> tuple[httpx.Response, bytes]:
            await asyncio.sleep(0.15)  # longer than the pulse
            return httpx.Response(200, content=b"OK", request=httpx.Request("GET", "http://93.184.216.34/")), b"OK"

        manager._event_bus.publish.side_effect = publish
        with (
            patch("obs.logic.manager.httpx.AsyncClient") as client_cls,
            patch("obs.logic.manager._send_api_client_request", side_effect=slow_request),
        ):
            client_cls.return_value.__aenter__ = AsyncMock()
            client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await manager._execute_graph("graph", "Graph", flow, _event(1))
            await manager._timer_tasks[("graph", "pulse")]

        assert [value for value, _at in seen] == [True, False]
        assert seen[1][1] - seen[0][1] >= 0.045

    asyncio.run(exercise())


def test_a_pulse_started_during_the_save_gap_keeps_its_deadline() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _pulse_flow(uuid.uuid4(), 60)
        manager._graphs["graph"] = ("Graph", True, flow)
        state = manager._timers.setdefault("graph", {})["pulse"] = _pending("timer_pulse", in_s=60, need_on=True)
        deadline = state.deadline

        async def unloading_run(*_args: object, **_kwargs: object) -> dict:
            manager._graphs.pop("graph")  # a save unloads the sheet meanwhile
            return {"pulse": {}}

        manager._execute_graph = AsyncMock(side_effect=unloading_run)  # type: ignore[method-assign]
        task = asyncio.create_task(manager._run_timer_pulse("graph", "pulse"))
        for _ in range(5):
            await asyncio.sleep(0)

        assert state.need_on is False and state.deadline == deadline
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def test_a_run_records_no_level_for_a_delay_a_save_removed_meanwhile() -> None:
    async def exercise() -> None:
        manager = _manager()
        empty = FlowData.model_validate({"nodes": [], "edges": []})
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("level", "const_value", {"value": "true", "data_type": "bool"}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                    node(
                        "notify",
                        "notify_message",
                        {"adapter_instance_id": "a", "providers": [{"provider": "x", "target": "y"}], "message": "x"},
                    ),
                ],
                "edges": [edge("level", "delay", "value", "trigger"), edge("level", "notify", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        entered, release = asyncio.Event(), asyncio.Event()
        adapter = SimpleNamespace(adapter_type="MESSAGE")

        async def send(*_args: object, **_kwargs: object) -> list[SimpleNamespace]:
            entered.set()
            await release.wait()
            return [SimpleNamespace(ok=True, provider="x", target="y", detail="")]

        adapter.send_notification = send

        def loading(loaded: FlowData):
            async def load_graphs() -> None:
                manager._graphs = {"graph": ("Graph", True, loaded)}

            return load_graphs

        with patch("obs.adapters.registry.get_instance_by_id", return_value=adapter):
            old = asyncio.create_task(manager._execute_graph("graph", "Graph", flow, {}))
            await entered.wait()
            manager._load_graphs = loading(empty)  # type: ignore[method-assign]
            await manager.reinitialize_graph("graph")
            release.set()
            await old

        assert "delay" not in manager._timers.get("graph", {})

        # Re-added with the same id, its first high level arms it.
        manager._load_graphs = loading(flow)  # type: ignore[method-assign]
        await manager.reinitialize_graph("graph")
        flow.nodes[2].data["adapter_instance_id"] = ""
        await manager._execute_graph("graph", "Graph", flow, {})
        assert ("graph", "delay") in manager._timer_tasks
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def _value_event(source: uuid.UUID, value: object, depth: int = 0) -> SimpleNamespace:
    return SimpleNamespace(datapoint_id=source, value=value, logic_depth=depth, initialization=False, suppress_action_triggers=False)


def _slow_action_flow(source: uuid.UUID, kind: str, action_trigger: str) -> FlowData:
    """Read Object → timer, beside a host check whose trigger comes from *action_trigger*."""
    config = {"delay_s": 60} if kind == "timer_delay" else {"duration_s": 60}
    return FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(source)}),
                node("timer", kind, config),
                node("const", "const_value", {"value": "true", "data_type": "bool"}),
                node("ping", "host_check", {"host": "example.com"}),
            ],
            "edges": [edge("read", "timer", "value", "trigger"), edge(action_trigger, "ping", "value", "trigger")],
        },
    )


async def _overlapping_runs(manager: LogicManager, source: uuid.UUID, older: bool, newer: bool) -> None:
    """The older event's run waits on its host check while the newer one completes."""
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def slow_ping(*_args: object) -> tuple[bool, float]:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return True, 1.0

    with patch("obs.logic.manager._ping_host", side_effect=slow_ping):
        old = asyncio.create_task(manager._on_value_event(_value_event(source, older)))
        await entered.wait()
        await manager._on_value_event(_value_event(source, newer))
        release.set()
        await old


def test_a_slower_true_run_starts_no_pulse_after_a_newer_run_saw_the_fall() -> None:
    async def exercise() -> None:
        manager = _manager()
        source = uuid.uuid4()
        manager._graphs["graph"] = ("Graph", True, _slow_action_flow(source, "timer_pulse", "read"))

        await _overlapping_runs(manager, source, older=True, newer=False)

        assert manager._timers.get("graph", {}).get("timer") is None
        assert manager._timer_tasks == {}

    asyncio.run(exercise())


def test_a_slower_false_run_keeps_the_newer_runs_high_level() -> None:
    async def exercise() -> None:
        manager = _manager()
        source = uuid.uuid4()
        manager._graphs["graph"] = ("Graph", True, _slow_action_flow(source, "timer_delay", "const"))

        await _overlapping_runs(manager, source, older=False, newer=True)

        state = manager._timers["graph"]["timer"]
        assert state.prev_trigger is True and state.deadline is not None
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def _compare_flow(source: uuid.UUID) -> FlowData:
    """Read Object → compare (> 0) → Delay trigger."""
    return FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(source)}),
                node("cmp", "compare", {"operator": ">", "operand": 0}),
                node("delay", "timer_delay", {"delay_s": 60}),
            ],
            "edges": [edge("read", "cmp", "value", "in1"), edge("cmp", "delay", "out", "trigger")],
        },
    )


@pytest.mark.parametrize("old_level", [True, False])
def test_rewiring_a_timers_trigger_resets_its_edge_state(old_level: bool) -> None:
    async def exercise() -> None:
        manager = _manager()
        values: dict[uuid.UUID, object] = {}
        manager._registry.get_value.side_effect = lambda dp: SimpleNamespace(value=values.get(dp), ts=None)
        old_source, new_source = uuid.uuid4(), uuid.uuid4()
        manager._graphs["graph"] = ("Graph", True, _compare_flow(old_source))
        values[old_source] = 1
        await manager._on_value_event(_value_event(old_source, 1))
        state = manager._timers["graph"]["delay"]
        before = state.deadline
        if not old_level:
            values[old_source] = 0
            await manager._on_value_event(_value_event(old_source, 0))
            assert state.prev_trigger is False
        values[new_source] = 0
        rewired = _compare_flow(new_source)

        async def load_graphs() -> None:
            manager._graphs = {"graph": ("Graph", True, rewired)}

        manager._load_graphs = load_graphs  # type: ignore[method-assign]
        await manager.reinitialize_graph("graph")
        assert state.prev_trigger is False
        await asyncio.sleep(0.02)
        values[new_source] = 1
        await manager._on_value_event(_value_event(new_source, 1))

        assert manager._timers["graph"]["delay"].deadline > before + 0.01  # the first rise restarts it
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_run_on_the_old_wiring_records_no_level_after_a_save_rewired_the_trigger() -> None:
    async def exercise() -> None:
        manager = _manager()
        old_flow, new_flow = _compare_flow(uuid.uuid4()), _compare_flow(uuid.uuid4())
        manager._graphs["graph"] = ("Graph", True, new_flow)

        await manager._execute_graph("graph", "Graph", old_flow, {"read": {"value": 1, "changed": True}})

        assert manager._timers == {}
        assert manager._timer_tasks == {}

    asyncio.run(exercise())


def test_a_run_on_a_replaced_sheet_with_the_same_wiring_still_records_its_level() -> None:
    async def exercise() -> None:
        manager = _manager()
        source = uuid.uuid4()
        old_flow, new_flow = _compare_flow(source), _compare_flow(source)
        manager._graphs["graph"] = ("Graph", True, new_flow)

        await manager._execute_graph("graph", "Graph", old_flow, {"read": {"value": 1, "changed": True}})

        assert manager._timers["graph"]["delay"].prev_trigger is True
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_level_recorded_on_another_input_is_no_baseline() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _compare_flow(uuid.uuid4())
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._timers["graph"] = {"delay": _TimerState("timer_delay", prev_trigger=True, trigger_sig="another input")}

        await manager._execute_graph("graph", "Graph", flow, {"read": {"value": 1, "changed": True}})

        state = manager._timers["graph"]["delay"]
        assert state.deadline is not None  # a rise, not a level still high
        assert state.trigger_sig == LogicManager._timer_trigger_signature(flow, "delay")
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_the_trigger_signature_follows_what_feeds_the_trigger() -> None:
    def signature(edges: list[dict], read_dp: str = "a") -> str:
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": read_dp}),
                    node("other", "datapoint_read", {"datapoint_id": "b"}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                ],
                "edges": edges,
            },
        )
        return LogicManager._timer_trigger_signature(flow, "delay")

    direct = [edge("read", "delay", "value", "trigger")]
    assert signature([]) == signature([edge("read", "delay", "value", "reset")])  # nothing on the trigger
    assert signature(direct) == signature(direct + [edge("other", "delay", "value", "reset")])
    assert signature(direct) != signature(direct, read_dp="c")
    labelled = FlowData.model_validate(
        {
            "nodes": [node("read", "datapoint_read", {"datapoint_id": "a", "label": "Renamed"}), node("delay", "timer_delay", {"delay_s": 60})],
            "edges": direct,
        },
    )
    assert LogicManager._timer_trigger_signature(labelled, "delay") == signature(direct)  # a rename is cosmetic
    assert signature(direct) != signature([edge("other", "delay", "value", "trigger")])
    # A feedback loop through the block itself, and an edge from a block that is gone.
    assert signature([edge("delay", "read", "trigger", "in"), *direct])
    assert signature([edge("missing", "delay", "value", "trigger")]) != signature([])


def _stop_after_load(manager: LogicManager, flow: FlowData) -> None:
    async def load_graphs() -> None:
        manager._graphs = {"graph": ("Graph", True, flow)}

    manager._load_graphs = load_graphs  # type: ignore[method-assign]


def test_a_cascade_limited_timer_run_persists_its_completion() -> None:
    async def exercise() -> None:
        manager = _manager()
        source = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    node("read", "datapoint_read", {"datapoint_id": str(source)}),
                    node("delay", "timer_delay", {"delay_s": 60}),
                ],
                "edges": [edge("read", "delay", "value", "trigger")],
            },
        )
        manager._graphs["graph"] = ("Graph", True, flow)
        await manager._on_value_event(_value_event(source, True, depth=9))
        flow.nodes[1].data["delay_s"] = 0.01  # a save shortens it below a new cascade
        _stop_after_load(manager, flow)
        await manager.reload()
        await manager._timer_tasks[("graph", "delay")]

        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]["delay"]
        assert manager._timers["graph"]["delay"].deadline is None
        assert "timer_deadline" not in saved
        assert "graph" not in manager._timer_dirty

    asyncio.run(exercise())


def test_a_cascade_limited_timer_run_of_an_unloaded_sheet_keeps_its_mark() -> None:
    async def exercise() -> None:
        manager = _manager()

        assert await manager._run_timer_graph("graph", "delay", "timer_delay", {}, 10) == "ran"

        assert "graph" in manager._timer_dirty
        manager._db.execute_and_commit.assert_not_awaited()

    asyncio.run(exercise())


def test_an_opted_out_timer_row_is_cleared_before_persistence_is_re_enabled() -> None:
    def flow(persist: bool) -> FlowData:
        return FlowData.model_validate({"nodes": [node("delay", "timer_delay", {"delay_s": 60, "persist_state": persist})], "edges": []})

    async def exercise() -> None:
        stale = {"timer_prev_trigger": True, "timer_deadline": datetime.now(UTC).timestamp() + 3600, "pulse_need_on": False}
        manager = _manager()
        manager._graphs = {"graph": ("Graph", True, flow(False))}
        manager._hysteresis = {"graph": {"delay": dict(stale)}}

        manager._adopt_persisted_timer_state()
        assert manager._timers == {}
        assert "graph" in manager._timer_dirty
        await manager._persist_dirty_timers()

        saved = json.loads(manager._db.execute_and_commit.await_args.args[1][0])["state"]
        assert "delay" not in saved

    asyncio.run(exercise())


def test_a_delay_runs_from_its_trigger_not_from_the_end_of_a_slow_run() -> None:
    async def exercise() -> None:
        manager = _manager()
        source = uuid.uuid4()
        flow = _slow_action_flow(source, "timer_delay", "const")
        flow.nodes[1].data["delay_s"] = 0.1
        manager._graphs["graph"] = ("Graph", True, flow)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_ping(*_args: object) -> tuple[bool, float]:
            entered.set()
            await release.wait()
            return True, 1.0

        with patch("obs.logic.manager._ping_host", side_effect=slow_ping):
            run = asyncio.create_task(manager._on_value_event(_value_event(source, True)))
            await entered.wait()
            await asyncio.sleep(0.15)
            release.set()
            await run

        deadline = manager._timers["graph"]["timer"].deadline
        assert deadline is None or deadline <= monotonic()  # due (or fired) already
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def _restored_flow() -> FlowData:
    """Three Delays behind Read Object → compare (> 0): one directly, one behind
    a "changed" pulse, one behind a Read Object without a value."""
    return FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                node("unset", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                node("cmp", "compare", {"operator": ">", "operand": 0}),
                node("delay", "timer_delay", {"delay_s": 60}),
                node("pulsed", "timer_delay", {"delay_s": 60}),
                node("unknown", "timer_delay", {"delay_s": 60}),
            ],
            "edges": [
                edge("read", "cmp", "value", "in1"),
                edge("cmp", "delay", "out", "trigger"),
                edge("read", "pulsed", "changed", "trigger"),
                edge("unset", "unknown", "value", "trigger"),
            ],
        },
    )


def _restore(manager: LogicManager, flow: FlowData, value: object, saved: dict[str, dict]) -> None:
    read_dp = uuid.UUID(flow.nodes[0].data["datapoint_id"])
    manager._registry.get_value.side_effect = lambda dp: SimpleNamespace(value=value if dp == read_dp else None, ts=None)
    manager._graphs = {"graph": ("Graph", True, flow)}
    manager._hysteresis = {"graph": saved}
    manager._adopt_persisted_timer_state()


def test_a_restored_high_level_whose_input_fell_meanwhile_is_taken_back() -> None:
    async def exercise() -> None:
        manager = _manager()
        flow = _restored_flow()
        high = {"timer_prev_trigger": True}
        _restore(manager, flow, 0, {"delay": dict(high), "pulsed": dict(high), "unknown": dict(high)})

        assert set(manager._timers["graph"]) == {"pulsed", "unknown"}  # no level here: kept
        assert "graph" in manager._timer_dirty
        await manager._execute_graph("graph", "Graph", flow, {"read": {"value": 1, "changed": False}})
        assert manager._timers["graph"]["delay"].deadline is not None  # its rise counts
        manager._cancel_timer_tasks()

    asyncio.run(exercise())


def test_a_restored_high_level_whose_input_is_still_high_stays() -> None:
    manager = _manager()
    _restore(manager, _restored_flow(), 5, {"delay": {"timer_prev_trigger": True}})

    assert manager._timers["graph"]["delay"].prev_trigger is True


def test_a_restored_pending_delay_whose_input_fell_keeps_its_deadline() -> None:
    manager = _manager()
    deadline = datetime.now(UTC).timestamp() + 30
    _restore(manager, _restored_flow(), 0, {"delay": {"timer_prev_trigger": True, "timer_deadline": deadline, "pulse_need_on": False}})

    state = manager._timers["graph"]["delay"]
    assert state.prev_trigger is False and state.deadline is not None


def test_a_restored_level_without_a_known_input_stays() -> None:
    manager = _manager()
    _restore(manager, _restored_flow(), None, {"delay": {"timer_prev_trigger": True}})

    assert manager._timers["graph"]["delay"].prev_trigger is True


def test_a_restored_level_stays_when_its_input_cannot_be_evaluated(caplog: pytest.LogCaptureFixture) -> None:
    manager = _manager()
    with patch.object(GraphExecutor, "execute", side_effect=RuntimeError("boom")), caplog.at_level(logging.WARNING, logger="obs.logic.manager"):
        _restore(manager, _restored_flow(), 0, {"delay": {"timer_prev_trigger": True}})

    assert manager._timers["graph"]["delay"].prev_trigger is True
    assert any("restored timer triggers" in record.message for record in caplog.records)


def test_a_restored_level_behind_a_failed_block_stays() -> None:
    manager = _manager()
    with patch.object(GraphExecutor, "execute", return_value={"cmp": {"out": False, "__error__": "boom"}}):
        _restore(manager, _restored_flow(), 0, {"delay": {"timer_prev_trigger": True}})

    assert manager._timers["graph"]["delay"].prev_trigger is True


def test_a_restored_level_of_a_read_object_with_an_invalid_id_stays() -> None:
    manager = _manager()
    flow = _restored_flow()
    flow.nodes[0].data["datapoint_id"] = "not-a-uuid"
    manager._registry.get_value.side_effect = ValueError
    manager._graphs = {"graph": ("Graph", True, flow)}
    manager._hysteresis = {"graph": {"delay": {"timer_prev_trigger": True}}}
    manager._adopt_persisted_timer_state()

    assert manager._timers["graph"]["delay"].prev_trigger is True


def test_renaming_a_block_upstream_of_a_held_delay_is_no_new_rise() -> None:
    async def exercise() -> None:
        manager = _manager()
        source, other = uuid.uuid4(), uuid.uuid4()
        flow = _compare_flow(source)
        flow.nodes.append(FlowData.model_validate({"nodes": [node("other", "datapoint_read", {"datapoint_id": str(other)})], "edges": []}).nodes[0])
        manager._graphs["graph"] = ("Graph", True, flow)
        manager._registry.get_value.side_effect = lambda dp: SimpleNamespace(value=1 if dp == source else 0, ts=None)
        await manager._on_value_event(_value_event(source, 1))
        before = manager._timers["graph"]["delay"].deadline
        renamed = flow.model_copy(deep=True)
        renamed.nodes[1].data["label"] = "Cosmetic name"
        manager.update_cached_graph("graph", "Graph", True, renamed)
        await asyncio.sleep(0.02)
        await manager._on_value_event(_value_event(other, 0))

        assert manager._timers["graph"]["delay"].deadline == before
        manager._cancel_timer_tasks()

    asyncio.run(exercise())
