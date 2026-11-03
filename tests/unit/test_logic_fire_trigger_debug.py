"""A debug run fires a Trigger (timer_cron) block on demand like a cron tick (#1276)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from obs.logic.manager import InvalidFireTriggerError
from tests.unit.conftest import edge, node
from tests.unit.test_host_check import _flow, _make_manager


def _manager():
    target = uuid.uuid4()
    flow = _flow(
        [
            node("cron", "timer_cron", {"cron": "0 7 * * *"}),
            node("value", "const_value", {"value": "42", "data_type": "number"}),
            node("write", "datapoint_write", {"datapoint_id": str(target)}),
        ],
        [
            edge("value", "write", "value", "value"),
            edge("cron", "write", "trigger", "trigger"),
        ],
    )
    manager = _make_manager()
    manager._graphs["g"] = ("test", True, flow)
    manager._node_state["g"] = {}
    return manager


@pytest.mark.asyncio
async def test_fired_trigger_drives_its_triggered_write():
    manager = _manager()
    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        out, _inputs = await manager.execute_graph_debug("g", {}, fire_triggers=["cron"])
    assert out["cron"]["trigger"] is True
    assert out["write"]["_write_value"] == 42


@pytest.mark.asyncio
async def test_debug_run_without_fire_leaves_the_trigger_idle():
    manager = _manager()
    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        out, _inputs = await manager.execute_graph_debug("g", {})
    assert out["cron"]["trigger"] is False


@pytest.mark.asyncio
async def test_firing_a_trigger_whose_level_is_overridden_is_rejected():
    manager = _manager()
    with pytest.raises(InvalidFireTriggerError, match="überschrieben: cron"):
        await manager.execute_graph_debug("g", {"cron": {"trigger": False}}, fire_triggers=["cron"])


@pytest.mark.asyncio
async def test_firing_a_block_that_is_no_trigger_is_rejected():
    manager = _manager()
    with pytest.raises(InvalidFireTriggerError, match="value, nope"):
        await manager.execute_graph_debug("g", {}, fire_triggers=["value", "nope"])
