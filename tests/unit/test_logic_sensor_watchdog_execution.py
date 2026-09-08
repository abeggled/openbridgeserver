"""Manager-level execution behaviour of the ``sensor_watchdog`` function block.

Node-definition and isolated-executor tests live in
``tests/unit/logic/nodes/timer/test_sensor_watchdog.py`` and
``tests/unit/test_sensor_watchdog.py``. Neither exercises a real
``datapoint_read`` -> ``sensor_watchdog`` wiring through LogicManager's own
registry-seeding path — which is exactly where the block originally shipped
broken (issue #1218 follow-up): a real Read Object re-supplies its current
value on every graph execution (event-driven, cron, and this node's own
autonomous watchdog loop), so "value is not None" alone can never detect
staleness — only the Read Object's "changed" output, wired into
``in_N_changed``, means a genuine new telegram arrived. This file drives the
block the way production actually does, through ``LogicManager._execute_graph``
with its real registry-seeding path, instead of hand-crafting the executor's
``inputs`` dict.
"""

from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.logic.manager import LogicManager
from obs.logic.models import FlowData
from tests.unit.conftest import edge, node


def _manager(values: dict[str, object] | None = None) -> LogicManager:
    """Manager whose registry seeds only the given DataPoint ids.

    Mirrors tests/unit/test_logic_edge_detect_execution.py's helper: the real
    registry creates an empty ValueState (value=None) as soon as a DataPoint is
    registered, well before any adapter writes a real value — a bare MagicMock
    would hand out a truthy attribute instead and hide the unseeded case.
    """
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(data_type="UNKNOWN")
    seeded = values or {}
    registry.get_value.side_effect = lambda dp_id: SimpleNamespace(value=seeded.get(str(dp_id)), ts=None)
    return LogicManager(AsyncMock(), AsyncMock(), registry)


_WATCHDOG_DATA = {"inputs": [{"label": "Sensor A", "timeout_s": 10, "fault_value": "OFFLINE"}]}


def _flow(dp_id: uuid.UUID, target: uuid.UUID, watchdog_data: dict | None = None) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [
                node("read", "datapoint_read", {"datapoint_id": str(dp_id)}),
                node("sw", "sensor_watchdog", watchdog_data if watchdog_data is not None else _WATCHDOG_DATA),
                node("w", "datapoint_write", {"datapoint_id": str(target)}),
            ],
            "edges": [
                edge("read", "sw", "value", "in_1"),
                edge("read", "sw", "changed", "in_1_changed"),
                edge("sw", "w", "out_1", "value"),
            ],
        }
    )


def _backdate_last_seen(manager: LogicManager, graph_id: str, node_id: str, seconds_ago: float, index: str = "1") -> None:
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=seconds_ago)
    manager._hysteresis.setdefault(graph_id, {}).setdefault(node_id, {}).setdefault("last_seen", {})[index] = past.isoformat()


@pytest.mark.asyncio
async def test_real_datapoint_read_wiring_detects_staleness_and_stays_stale_across_reticks():
    """The exact scenario that shipped broken: a real sensor value that stops
    updating must be detected once its timeout elapses, and must STAY detected
    on every subsequent autonomous re-check — even though a real Read Object
    keeps re-supplying its (unchanged) last known value on every one of those
    re-checks."""
    dp = uuid.uuid4()
    target = uuid.uuid4()
    manager = _manager({str(dp): 1})
    flow = _flow(dp, target)
    manager._graphs["g"] = ("G", True, flow)

    # Tick 1: the genuine event that seeds the sensor's last-seen baseline —
    # mirrors how _on_value_event overrides the triggering datapoint_read
    # node's own output with changed=True.
    await manager._execute_graph("g", "G", flow, {"read": {"value": 1, "changed": True}})
    assert manager._hysteresis["g"]["sw"]["last_seen"]["1"]

    # The sensor's DataPoint never receives another event. Simulate the
    # timeout having elapsed.
    _backdate_last_seen(manager, "g", "sw", seconds_ago=11)

    # Tick 2: an autonomous re-check with NO overrides at all — exactly what
    # LogicManager._watchdog_loop does. The registry still seeds "read" with
    # its last known value (1) and changed=False, because nothing new
    # actually arrived.
    before = manager._event_bus.publish.await_count
    await manager._execute_graph("g", "G", flow, {})
    assert manager._event_bus.publish.await_count > before
    written = manager._event_bus.publish.await_args.args[0].value
    assert written == "OFFLINE"

    # Tick 3: another autonomous re-check. Before the fix, "read" re-supplying
    # its current (still non-None) value on this pass alone would have reset
    # the staleness clock, silently un-faulting the sensor forever — the
    # user-reported bug this test guards against.
    await manager._execute_graph("g", "G", flow, {})
    assert manager._hysteresis["g"]["sw"]["stale"]["1"] is True


@pytest.mark.asyncio
async def test_real_datapoint_read_wiring_recovers_only_on_a_genuine_new_event():
    dp = uuid.uuid4()
    target = uuid.uuid4()
    manager = _manager({str(dp): 1})
    flow = _flow(dp, target)
    manager._graphs["g"] = ("G", True, flow)

    await manager._execute_graph("g", "G", flow, {"read": {"value": 1, "changed": True}})
    _backdate_last_seen(manager, "g", "sw", seconds_ago=11)
    await manager._execute_graph("g", "G", flow, {})
    assert manager._hysteresis["g"]["sw"]["stale"]["1"] is True

    # A registry-seeded re-check (no real event) must not recover it either.
    await manager._execute_graph("g", "G", flow, {})
    assert manager._hysteresis["g"]["sw"]["stale"]["1"] is True

    # A genuine new event for the sensor's own DataPoint recovers it.
    await manager._execute_graph("g", "G", flow, {"read": {"value": 2, "changed": True}})
    assert manager._hysteresis["g"]["sw"]["stale"]["1"] is False
