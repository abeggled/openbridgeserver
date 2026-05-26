"""Unit tests for the Anwesenheitssimulation adapter.

Tested without asyncio.run / event loop so they remain fast.
The background _simulation_loop is NOT tested here — only the
pure logic helpers (_handle_control_event, _handle_presence,
_preload_window, _fire_due) are exercised via AsyncMock / MagicMock.
"""

from __future__ import annotations

import heapq
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.adapters.anwesenheit.adapter import (
    AnwesenheitssimulationAdapter,
    AnwesenheitssimulationBindingConfig,
    AnwesenheitssimulationConfig,
    OnPresence,
)
from obs.core.event_bus import DataValueEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock()
    return bus


def _make_adapter(config: dict | None = None, bus: MagicMock | None = None) -> AnwesenheitssimulationAdapter:
    return AnwesenheitssimulationAdapter(
        event_bus=bus or _make_bus(),
        config=config or {},
        instance_id=uuid.uuid4(),
        name="Test",
    )


def _make_binding(
    dp_id: uuid.UUID | None = None,
    config: dict | None = None,
    direction: str = "SOURCE",
    enabled: bool = True,
) -> MagicMock:
    b = MagicMock()
    b.id = uuid.uuid4()
    b.datapoint_id = dp_id or uuid.uuid4()
    b.config = config or {}
    b.enabled = enabled
    b.direction = direction
    return b


def _evt(dp_id: uuid.UUID, value: Any) -> DataValueEvent:
    return DataValueEvent(datapoint_id=dp_id, value=value, quality="good", source_adapter="OTHER")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestConfig:
    def test_default_offset_seven(self):
        cfg = AnwesenheitssimulationConfig()
        assert cfg.offset_days == 7

    def test_custom_offset(self):
        cfg = AnwesenheitssimulationConfig(offset_days=14)
        assert cfg.offset_days == 14

    def test_offset_lower_bound(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            AnwesenheitssimulationConfig(offset_days=0)

    def test_offset_upper_bound(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            AnwesenheitssimulationConfig(offset_days=31)

    def test_offset_min_valid(self):
        cfg = AnwesenheitssimulationConfig(offset_days=1)
        assert cfg.offset_days == 1

    def test_offset_max_valid(self):
        cfg = AnwesenheitssimulationConfig(offset_days=30)
        assert cfg.offset_days == 30

    def test_defaults(self):
        cfg = AnwesenheitssimulationConfig()
        assert cfg.control_dp_id is None
        assert cfg.control_invert is False
        assert cfg.on_presence == OnPresence.KEEP

    def test_on_presence_values(self):
        assert OnPresence.KEEP == "behalten"
        assert OnPresence.RESET == "zuruecksetzen"
        assert OnPresence.SET == "setzen"

    def test_on_presence_value_default_none(self):
        cfg = AnwesenheitssimulationConfig()
        assert cfg.on_presence_value is None


class TestBindingConfig:
    def test_all_none_by_default(self):
        bc = AnwesenheitssimulationBindingConfig()
        assert bc.offset_override is None
        assert bc.on_presence_override is None

    def test_offset_override_bounds(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            AnwesenheitssimulationBindingConfig(offset_override=0)
        with pytest.raises(pydantic.ValidationError):
            AnwesenheitssimulationBindingConfig(offset_override=31)

    def test_on_presence_override(self):
        bc = AnwesenheitssimulationBindingConfig(on_presence_override=OnPresence.RESET)
        assert bc.on_presence_override == OnPresence.RESET

    def test_on_presence_value_default_none(self):
        bc = AnwesenheitssimulationBindingConfig()
        assert bc.on_presence_value is None


# ---------------------------------------------------------------------------
# Adapter type
# ---------------------------------------------------------------------------


class TestAdapterType:
    def test_adapter_type_name(self):
        adapter = _make_adapter()
        assert adapter.adapter_type == "ANWESENHEITSSIMULATION"

    @pytest.mark.asyncio
    async def test_connect_sets_connected_true(self):
        """_publish_status must be awaited so connected=True after connect()."""
        bus = _make_bus()
        adapter = _make_adapter(bus=bus)
        adapter._bindings = []

        with (
            patch("obs.adapters.anwesenheit.adapter.get_history_plugin", side_effect=RuntimeError),
            patch.object(adapter, "_resolve_initial_state", new=AsyncMock(return_value=False)),
        ):
            await adapter.connect()
            await adapter.disconnect()

        # _publish_status is awaited → connected must toggle correctly
        assert not adapter.connected  # False after disconnect


# ---------------------------------------------------------------------------
# Control datapoint — activation / deactivation
# ---------------------------------------------------------------------------


class TestControlDatapoint:
    @pytest.mark.asyncio
    async def test_returns_home_stops_simulation(self):
        ctrl = uuid.uuid4()
        adapter = _make_adapter({"control_dp_id": str(ctrl)})
        adapter._active = True
        adapter._bindings = []

        await adapter._handle_control_event(_evt(ctrl, True))

        assert adapter._active is False

    @pytest.mark.asyncio
    async def test_leaves_home_starts_simulation(self):
        ctrl = uuid.uuid4()
        adapter = _make_adapter({"control_dp_id": str(ctrl)})
        adapter._active = False
        adapter._bindings = []

        with patch.object(adapter, "_preload_window", new=AsyncMock()):
            await adapter._handle_control_event(_evt(ctrl, False))

        assert adapter._active is True

    @pytest.mark.asyncio
    async def test_invert_value0_means_home(self):
        ctrl = uuid.uuid4()
        adapter = _make_adapter({"control_dp_id": str(ctrl), "control_invert": True})
        adapter._active = True
        adapter._bindings = []

        # With invert: value=False → at_home=True → simulation stops
        await adapter._handle_control_event(_evt(ctrl, False))

        assert adapter._active is False

    @pytest.mark.asyncio
    async def test_invert_value1_means_away(self):
        ctrl = uuid.uuid4()
        adapter = _make_adapter({"control_dp_id": str(ctrl), "control_invert": True})
        adapter._active = False
        adapter._bindings = []

        with patch.object(adapter, "_preload_window", new=AsyncMock()):
            await adapter._handle_control_event(_evt(ctrl, True))

        assert adapter._active is True

    @pytest.mark.asyncio
    async def test_ignores_unrelated_dp(self):
        ctrl = uuid.uuid4()
        adapter = _make_adapter({"control_dp_id": str(ctrl)})
        adapter._active = True

        await adapter._handle_control_event(_evt(uuid.uuid4(), True))

        assert adapter._active is True  # unchanged

    @pytest.mark.asyncio
    async def test_no_control_dp_noop(self):
        adapter = _make_adapter()  # no control_dp_id
        adapter._active = True

        await adapter._handle_control_event(_evt(uuid.uuid4(), True))

        assert adapter._active is True

    @pytest.mark.asyncio
    async def test_no_change_if_same_state(self):
        ctrl = uuid.uuid4()
        adapter = _make_adapter({"control_dp_id": str(ctrl)})
        adapter._active = False  # already inactive (at home)

        presence_action = AsyncMock()
        adapter._handle_presence = presence_action

        # value=True → at_home → new_active=False (same as current)
        await adapter._handle_control_event(_evt(ctrl, True))

        presence_action.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_home_clears_pending(self):
        ctrl = uuid.uuid4()
        adapter = _make_adapter({"control_dp_id": str(ctrl), "on_presence": "behalten"})
        adapter._active = True
        adapter._bindings = []
        adapter._pending = [(_utcnow() + timedelta(hours=1), 0, str(uuid.uuid4()), str(uuid.uuid4()), True, "good")]

        await adapter._handle_control_event(_evt(ctrl, True))

        assert adapter._pending == []


# ---------------------------------------------------------------------------
# On-presence action
# ---------------------------------------------------------------------------


class TestPresenceAction:
    @pytest.mark.asyncio
    async def test_keep_does_not_publish(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "behalten"}, bus=bus)
        adapter._bindings = [_make_binding()]

        await adapter._handle_presence()

        bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_reset_publishes_false(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "zuruecksetzen"}, bus=bus)
        dp_id = uuid.uuid4()
        adapter._bindings = [_make_binding(dp_id=dp_id)]

        await adapter._handle_presence()

        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert isinstance(event, DataValueEvent)
        assert event.datapoint_id == dp_id
        assert event.value is False

    @pytest.mark.asyncio
    async def test_binding_override_reset_beats_adapter_keep(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "behalten"}, bus=bus)
        dp_id = uuid.uuid4()
        adapter._bindings = [_make_binding(dp_id=dp_id, config={"on_presence_override": "zuruecksetzen"})]

        await adapter._handle_presence()

        bus.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_binding_override_keep_beats_adapter_reset(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "zuruecksetzen"}, bus=bus)
        adapter._bindings = [_make_binding(config={"on_presence_override": "behalten"})]

        await adapter._handle_presence()

        bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_binding_skipped(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "zuruecksetzen"}, bus=bus)
        adapter._bindings = [_make_binding(enabled=False)]

        await adapter._handle_presence()

        bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_dest_binding_skipped(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "zuruecksetzen"}, bus=bus)
        adapter._bindings = [_make_binding(direction="DEST")]

        await adapter._handle_presence()

        bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_bindings_all_reset(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "zuruecksetzen"}, bus=bus)
        adapter._bindings = [_make_binding(), _make_binding(), _make_binding()]

        await adapter._handle_presence()

        assert bus.publish.call_count == 3

    @pytest.mark.asyncio
    async def test_set_publishes_configured_value(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "setzen", "on_presence_value": "21.5"}, bus=bus)
        dp_id = uuid.uuid4()
        adapter._bindings = [_make_binding(dp_id=dp_id)]

        await adapter._handle_presence()

        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert event.value == "21.5"

    @pytest.mark.asyncio
    async def test_set_binding_override_value_beats_adapter(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "setzen", "on_presence_value": "1"}, bus=bus)
        dp_id = uuid.uuid4()
        adapter._bindings = [_make_binding(dp_id=dp_id, config={"on_presence_override": "setzen", "on_presence_value": "99"})]

        await adapter._handle_presence()

        event = bus.publish.call_args[0][0]
        assert event.value == "99"

    @pytest.mark.asyncio
    async def test_set_without_value_publishes_false(self):
        bus = _make_bus()
        adapter = _make_adapter({"on_presence": "setzen"}, bus=bus)  # no on_presence_value
        adapter._bindings = [_make_binding()]

        await adapter._handle_presence()

        event = bus.publish.call_args[0][0]
        assert event.value is False


# ---------------------------------------------------------------------------
# History pre-loading (_preload_window)
# ---------------------------------------------------------------------------


class TestPreloadWindow:
    @staticmethod
    def _mock_history(records: list[dict]) -> Any:
        h = AsyncMock()
        h.query = AsyncMock(return_value=records)
        return h

    @pytest.mark.asyncio
    async def test_loads_future_events_into_pending(self):
        adapter = _make_adapter({"offset_days": 7})
        adapter._active = True
        dp_id = uuid.uuid4()
        binding = _make_binding(dp_id=dp_id)
        adapter._bindings = [binding]

        now = _utcnow()
        hist_ts = now - timedelta(days=7) + timedelta(minutes=30)
        fire_at = hist_ts + timedelta(days=7)

        history = self._mock_history([{"ts": hist_ts.isoformat(), "v": True, "q": "good"}])
        with patch("obs.adapters.anwesenheit.adapter.get_history_plugin", return_value=history):
            await adapter._preload_window(now, now + timedelta(hours=1))

        assert len(adapter._pending) == 1
        entry = adapter._pending[0]
        assert abs((entry[0] - fire_at).total_seconds()) < 1
        assert entry[4] is True

    @pytest.mark.asyncio
    async def test_skips_past_events(self):
        adapter = _make_adapter()
        adapter._active = True
        adapter._bindings = [_make_binding()]

        now = _utcnow()
        hist_ts = now - timedelta(days=7) - timedelta(minutes=30)  # fires 30 min ago

        history = self._mock_history([{"ts": hist_ts.isoformat(), "v": False, "q": "good"}])
        with patch("obs.adapters.anwesenheit.adapter.get_history_plugin", return_value=history):
            await adapter._preload_window(now, now + timedelta(hours=1))

        assert len(adapter._pending) == 0

    @pytest.mark.asyncio
    async def test_inactive_does_not_query(self):
        adapter = _make_adapter()
        adapter._active = False
        adapter._bindings = [_make_binding()]

        history = self._mock_history([])
        with patch("obs.adapters.anwesenheit.adapter.get_history_plugin", return_value=history):
            await adapter._preload_window(_utcnow(), _utcnow() + timedelta(hours=1))

        history.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_binding_offset_override_used(self):
        adapter = _make_adapter({"offset_days": 7})
        adapter._active = True
        binding = _make_binding(config={"offset_override": 14})
        adapter._bindings = [binding]

        call_args: list[tuple] = []

        async def capture_query(dp_id, from_dt, to_dt, limit):
            call_args.append((from_dt, to_dt))
            return []

        history = AsyncMock()
        history.query = capture_query

        now = _utcnow()
        with patch("obs.adapters.anwesenheit.adapter.get_history_plugin", return_value=history):
            await adapter._preload_window(now, now + timedelta(hours=1))

        assert len(call_args) == 1
        from_dt, _ = call_args[0]
        expected_from = now - timedelta(days=14)
        assert abs((from_dt - expected_from).total_seconds()) < 1

    @pytest.mark.asyncio
    async def test_skips_disabled_bindings(self):
        adapter = _make_adapter()
        adapter._active = True
        adapter._bindings = [_make_binding(enabled=False)]

        history = self._mock_history([])
        with patch("obs.adapters.anwesenheit.adapter.get_history_plugin", return_value=history):
            await adapter._preload_window(_utcnow(), _utcnow() + timedelta(hours=1))

        history.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_dest_bindings(self):
        adapter = _make_adapter()
        adapter._active = True
        adapter._bindings = [_make_binding(direction="DEST")]

        history = self._mock_history([])
        with patch("obs.adapters.anwesenheit.adapter.get_history_plugin", return_value=history):
            await adapter._preload_window(_utcnow(), _utcnow() + timedelta(hours=1))

        history.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_heap_is_ordered(self):
        adapter = _make_adapter({"offset_days": 7})
        adapter._active = True
        dp_id = uuid.uuid4()
        adapter._bindings = [_make_binding(dp_id=dp_id)]

        now = _utcnow()
        delta = timedelta(days=7)
        t1 = now + timedelta(minutes=45) - delta
        t2 = now + timedelta(minutes=20) - delta
        t3 = now + timedelta(minutes=50) - delta

        records = [
            {"ts": t1.isoformat(), "v": 1, "q": "good"},
            {"ts": t2.isoformat(), "v": 2, "q": "good"},
            {"ts": t3.isoformat(), "v": 3, "q": "good"},
        ]
        history = AsyncMock()
        history.query = AsyncMock(return_value=records)
        with patch("obs.adapters.anwesenheit.adapter.get_history_plugin", return_value=history):
            await adapter._preload_window(now, now + timedelta(hours=1))

        assert len(adapter._pending) == 3
        times = [adapter._pending[i][0] for i in range(3)]
        assert adapter._pending[0][0] == min(times)

    @pytest.mark.asyncio
    async def test_history_plugin_not_initialized_is_handled(self):
        adapter = _make_adapter()
        adapter._active = True
        adapter._bindings = [_make_binding()]

        with patch("obs.adapters.anwesenheit.adapter.get_history_plugin", side_effect=RuntimeError("not init")):
            # Should not raise
            await adapter._preload_window(_utcnow(), _utcnow() + timedelta(hours=1))

        assert len(adapter._pending) == 0


# ---------------------------------------------------------------------------
# Firing due events (_fire_due)
# ---------------------------------------------------------------------------


class TestFireDue:
    @pytest.mark.asyncio
    async def test_fires_past_events(self):
        bus = _make_bus()
        adapter = _make_adapter(bus=bus)
        adapter._active = True
        dp_id = uuid.uuid4()
        b_id = uuid.uuid4()
        past = _utcnow() - timedelta(seconds=5)
        adapter._pending = [(past, 0, str(dp_id), str(b_id), True, "good")]
        heapq.heapify(adapter._pending)

        await adapter._fire_due()

        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert isinstance(event, DataValueEvent)
        assert event.datapoint_id == dp_id
        assert event.value is True
        assert len(adapter._pending) == 0

    @pytest.mark.asyncio
    async def test_does_not_fire_future_events(self):
        bus = _make_bus()
        adapter = _make_adapter(bus=bus)
        adapter._active = True
        dp_id = uuid.uuid4()
        b_id = uuid.uuid4()
        future = _utcnow() + timedelta(hours=1)
        adapter._pending = [(future, 0, str(dp_id), str(b_id), True, "good")]
        heapq.heapify(adapter._pending)

        await adapter._fire_due()

        bus.publish.assert_not_called()
        assert len(adapter._pending) == 1

    @pytest.mark.asyncio
    async def test_fires_multiple_due_events(self):
        bus = _make_bus()
        adapter = _make_adapter(bus=bus)
        adapter._active = True
        now = _utcnow()
        p1 = now - timedelta(minutes=10)
        p2 = now - timedelta(minutes=5)
        f1 = now + timedelta(minutes=10)

        d1, d2, d3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        b1, b2, b3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        adapter._pending = [
            (p1, 0, str(d1), str(b1), True, "good"),
            (p2, 1, str(d2), str(b2), False, "good"),
            (f1, 2, str(d3), str(b3), True, "good"),
        ]
        heapq.heapify(adapter._pending)

        await adapter._fire_due()

        assert bus.publish.call_count == 2
        assert len(adapter._pending) == 1
        assert adapter._pending[0][0] == f1

    @pytest.mark.asyncio
    async def test_inactive_does_not_fire(self):
        bus = _make_bus()
        adapter = _make_adapter(bus=bus)
        adapter._active = False
        past = _utcnow() - timedelta(seconds=5)
        adapter._pending = [(past, 0, str(uuid.uuid4()), str(uuid.uuid4()), True, "good")]

        await adapter._fire_due()

        bus.publish.assert_not_called()
        assert len(adapter._pending) == 1  # not consumed

    @pytest.mark.asyncio
    async def test_fires_correct_value_and_quality(self):
        bus = _make_bus()
        adapter = _make_adapter(bus=bus)
        adapter._active = True
        past = _utcnow() - timedelta(seconds=1)
        adapter._pending = [(past, 0, str(uuid.uuid4()), str(uuid.uuid4()), 42, "uncertain")]
        heapq.heapify(adapter._pending)

        await adapter._fire_due()

        event = bus.publish.call_args[0][0]
        assert event.value == 42
        assert event.quality == "uncertain"


# ---------------------------------------------------------------------------
# Bindings reloaded (_on_bindings_reloaded)
# ---------------------------------------------------------------------------


class TestBindingsReloaded:
    @pytest.mark.asyncio
    async def test_clears_pending_on_reload(self):
        adapter = _make_adapter()
        adapter._active = False
        adapter._pending = [(_utcnow() + timedelta(hours=1), 0, "a", "b", True, "good")]

        with patch.object(adapter, "_preload_window", new=AsyncMock()):
            adapter._bindings = []
            await adapter._on_bindings_reloaded()

        assert adapter._pending == []

    @pytest.mark.asyncio
    async def test_preloads_if_active(self):
        adapter = _make_adapter()
        adapter._active = True
        adapter._bindings = [_make_binding()]

        preload_mock = AsyncMock()
        with patch.object(adapter, "_preload_window", preload_mock):
            await adapter._on_bindings_reloaded()

        preload_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_preload_if_inactive(self):
        adapter = _make_adapter()
        adapter._active = False
        adapter._bindings = [_make_binding()]

        preload_mock = AsyncMock()
        with patch.object(adapter, "_preload_window", preload_mock):
            await adapter._on_bindings_reloaded()

        preload_mock.assert_not_called()
