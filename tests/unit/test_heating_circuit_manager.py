"""Unit tests for the heating_circuit history pre-fill in LogicManager._execute_graph.

The manager queries history for missing T1/T2/T3 slots when the server starts
after the slot's threshold hour has already passed.  These tests verify:

  - Slots missing for today are filled from history when past their threshold hour
  - Slots already captured today are NOT re-queried
  - Slots whose threshold hour hasn't passed yet are NOT pre-filled
  - Empty history results leave slots as None
  - History plugin exceptions are caught and execution continues normally
  - Graphs without a datapoint_read → heating_circuit edge are skipped cleanly
  - All three slots filled at once (hour >= 21) triggers daily_avg computation
"""

from __future__ import annotations

import asyncio
import datetime as real_dt
import uuid
import zoneinfo
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic.manager import LogicManager
from obs.logic.models import FlowData

_ZURICH = zoneinfo.ZoneInfo("Europe/Zurich")


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------


def _make_manager() -> LogicManager:
    """Return a LogicManager with all external deps mocked."""
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.execute_and_commit = AsyncMock()
    event_bus = AsyncMock()
    registry = MagicMock()
    registry.get_value.return_value = None
    return LogicManager(db, event_bus, registry)


def _flow_dp_to_hc(dp_id: uuid.UUID) -> FlowData:
    """Graph: datapoint_read (dp) → heating_circuit (hc), value input."""
    return FlowData.model_validate(
        {
            "nodes": [
                {
                    "id": "dp",
                    "type": "datapoint_read",
                    "position": {"x": 0, "y": 0},
                    "data": {"datapoint_id": str(dp_id)},
                },
                {
                    "id": "hc",
                    "type": "heating_circuit",
                    "position": {"x": 200, "y": 0},
                    "data": {"threshold_temp": 14.0, "hysteresis": 2.0},
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "dp",
                    "target": "hc",
                    "sourceHandle": "value",
                    "targetHandle": "value",
                }
            ],
        }
    )


def _run(
    manager: LogicManager,
    flow: FlowData,
    graph_id: str = "g1",
    overrides: dict | None = None,
    initial_hyst: dict | None = None,
) -> dict:
    """Run _execute_graph synchronously and return outputs."""
    manager._graphs[graph_id] = ("test", True, flow)
    manager._node_state[graph_id] = {}
    if initial_hyst:
        manager._hysteresis[graph_id] = initial_hyst
    return asyncio.run(
        manager._execute_graph(graph_id, "test", flow, overrides or {}),
    )


@contextmanager
def _fixed_now(dt: real_dt.datetime):
    """Patch datetime.datetime.now() inside the manager's local import.

    The manager does ``import datetime as _hc_dt`` inside the function body and
    then calls ``_hc_dt.datetime.now()``.  Because ``_hc_dt`` is the *module*,
    ``_hc_dt.datetime`` resolves to whatever ``datetime.datetime`` is in the
    module's namespace.  Replacing it with a subclass that overrides ``now()``
    gives us full control over the "current" time for the history pre-fill,
    without affecting the ``from datetime import datetime`` binding used for
    ``execute_now``.
    """

    class _FixedDt(real_dt.datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            # Always return dt directly; tests pass timezone-aware datetimes so
            # _hc_now.hour and _hc_now.date() are already correct in app timezone.
            return dt

    with patch.object(real_dt, "datetime", _FixedDt):
        yield


def _mock_plugin(temp: float | None = 8.5) -> MagicMock:
    plugin = MagicMock()
    plugin.query = AsyncMock(return_value=[{"v": temp}] if temp is not None else [])
    return plugin


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHeatingCircuitHistoryPrefill:
    """Manager injects _history_t1/t2/t3 into aug_overrides for the executor."""

    def test_t1_filled_from_history_when_past_0700(self):
        """At 10:00 (past T1 threshold=07:00) a missing T1 is fetched from history."""
        dp_id = uuid.uuid4()
        flow = _flow_dp_to_hc(dp_id)
        manager = _make_manager()
        plugin = _mock_plugin(6.5)

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 10, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, flow)

        assert out["hc"]["t1"] == pytest.approx(6.5)

    def test_t1_not_queried_when_already_captured_today(self):
        """History is not queried for a slot that is already captured for today."""
        dp_id = uuid.uuid4()
        flow = _flow_dp_to_hc(dp_id)
        manager = _make_manager()
        plugin = _mock_plugin(6.5)
        today = "2025-01-01"

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 10, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(
                manager,
                flow,
                initial_hyst={
                    "hc": {
                        "last_value": None,
                        "t1": 10.0,
                        "t1_date": today,
                        "t2": None,
                        "t2_date": None,
                        "t3": None,
                        "t3_date": None,
                        "daily_temps": [],
                        "daily_avg": None,
                        "daily_avg_date": None,
                        "monthly_avg": None,
                        "heating_mode": 0,
                    }
                },
            )

        plugin.query.assert_not_awaited()
        assert out["hc"]["t1"] == pytest.approx(10.0)

    def test_no_fill_before_slot_threshold_hour(self):
        """At 05:00 no slot has passed its threshold — history is not queried."""
        dp_id = uuid.uuid4()
        flow = _flow_dp_to_hc(dp_id)
        manager = _make_manager()
        plugin = _mock_plugin(6.5)

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 5, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, flow)

        plugin.query.assert_not_awaited()
        assert out["hc"]["t1"] is None

    def test_empty_history_leaves_slot_none(self):
        """When history returns no rows the slot stays None."""
        dp_id = uuid.uuid4()
        flow = _flow_dp_to_hc(dp_id)
        manager = _make_manager()
        plugin = _mock_plugin(None)  # empty result

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 10, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, flow)

        assert out["hc"]["t1"] is None

    def test_history_query_exception_caught_gracefully(self):
        """If the history query raises, the error is swallowed and execution continues."""
        dp_id = uuid.uuid4()
        flow = _flow_dp_to_hc(dp_id)
        manager = _make_manager()
        plugin = MagicMock()
        plugin.query = AsyncMock(side_effect=RuntimeError("history DB down"))

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 10, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, flow)  # must not raise

        assert out["hc"]["heating_mode"] == 0  # default, no crash

    def test_get_history_plugin_not_configured_handled(self):
        """RuntimeError from get_history_plugin (not configured) is caught cleanly."""
        dp_id = uuid.uuid4()
        flow = _flow_dp_to_hc(dp_id)
        manager = _make_manager()

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 10, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", side_effect=RuntimeError("no plugin")),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, flow)  # must not raise

        assert out["hc"]["t1"] is None

    def test_no_fill_when_heating_circuit_has_no_upstream_datapoint(self):
        """heating_circuit with no datapoint_read edge — history query skipped entirely."""
        flow = FlowData.model_validate(
            {
                "nodes": [
                    {
                        "id": "hc",
                        "type": "heating_circuit",
                        "position": {"x": 0, "y": 0},
                        "data": {"threshold_temp": 14.0},
                    }
                ],
                "edges": [],
            }
        )
        manager = _make_manager()
        plugin = _mock_plugin(6.5)

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 10, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            _run(manager, flow)

        plugin.query.assert_not_awaited()

    def test_all_slots_filled_at_hour_22_triggers_daily_avg(self):
        """At 22:30 all three missing slots are pre-filled and daily_avg is computed."""
        dp_id = uuid.uuid4()
        flow = _flow_dp_to_hc(dp_id)
        manager = _make_manager()
        plugin = _mock_plugin(8.0)

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 22, 30, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, flow)

        assert out["hc"]["t1"] == pytest.approx(8.0)
        assert out["hc"]["t2"] == pytest.approx(8.0)
        assert out["hc"]["t3"] == pytest.approx(8.0)
        # daily_avg = (8 + 8 + 2*8) / 4 = 8.0
        assert out["hc"]["daily_avg"] == pytest.approx(8.0)
        # 8.0 < threshold (14) → heating ON
        assert out["hc"]["heating_mode"] == 1

    def test_value_formula_applied_to_history_slot(self):
        """value_formula on the datapoint_read node is applied to the history value."""
        dp_id = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    {
                        "id": "dp",
                        "type": "datapoint_read",
                        "position": {"x": 0, "y": 0},
                        "data": {"datapoint_id": str(dp_id), "value_formula": "x * 2"},
                    },
                    {
                        "id": "hc",
                        "type": "heating_circuit",
                        "position": {"x": 200, "y": 0},
                        "data": {"threshold_temp": 14.0, "hysteresis": 2.0},
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "dp",
                        "target": "hc",
                        "sourceHandle": "value",
                        "targetHandle": "value",
                    }
                ],
            }
        )
        manager = _make_manager()
        plugin = _mock_plugin(5.0)  # raw history value

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 10, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, flow)

        # history value 5.0 transformed by formula x*2 → 10.0
        assert out["hc"]["t1"] == pytest.approx(10.0)

    def test_value_map_applied_to_non_numeric_history_value(self):
        """value_map works on non-numeric raw history values (e.g. 'warm' → 22.5).

        Regression: float() was called before value_map, crashing on non-numeric
        stored values and silently leaving the slot empty.
        """
        dp_id = uuid.uuid4()
        flow = FlowData.model_validate(
            {
                "nodes": [
                    {
                        "id": "dp",
                        "type": "datapoint_read",
                        "position": {"x": 0, "y": 0},
                        "data": {
                            "datapoint_id": str(dp_id),
                            "value_map": {"warm": 22.5, "cold": 5.0},
                        },
                    },
                    {
                        "id": "hc",
                        "type": "heating_circuit",
                        "position": {"x": 200, "y": 0},
                        "data": {"threshold_temp": 14.0, "hysteresis": 2.0},
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "dp",
                        "target": "hc",
                        "sourceHandle": "value",
                        "targetHandle": "value",
                    }
                ],
            }
        )
        manager = _make_manager()
        plugin = MagicMock()
        plugin.query = AsyncMock(return_value=[{"v": "warm"}])  # non-numeric raw value

        with (
            _fixed_now(real_dt.datetime(2025, 1, 1, 10, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, flow)

        # "warm" → 22.5 via value_map; slot must be filled (not skipped due to float() error)
        assert out["hc"]["t1"] == pytest.approx(22.5)

    def test_app_timezone_date_injected_for_executor(self):
        """Manager injects _date in app timezone so executor and manager agree on today.

        Without this, around midnight the executor might tag slots with the system-clock
        date while the manager checks against the app-timezone date, causing perpetual
        history re-queries.
        """
        dp_id = uuid.uuid4()
        flow = _flow_dp_to_hc(dp_id)
        manager = _make_manager()
        plugin = _mock_plugin(8.0)

        with (
            _fixed_now(real_dt.datetime(2025, 3, 15, 10, 0, 0, tzinfo=_ZURICH)),
            patch("obs.history.factory.get_history_plugin", return_value=plugin),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            _run(manager, flow)

        # Slot must be tagged with the app-timezone date "2025-03-15"
        assert manager._hysteresis["g1"]["hc"]["t1_date"] == "2025-03-15"
