"""Unit tests for the 1-Wire adapter — filesystem functions and adapter lifecycle.
Uses tmp_path and mocked EventBus; no hardware required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import asyncio
import unittest.mock as mock

from obs.adapters.onewire.adapter import OneWireAdapter, _read_sensor_file, scan_sensors
from tests.adapters.conftest import make_binding

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sensor(base: Path, sensor_id: str, line0: str, line1: str) -> Path:
    """Create a fake sysfs sensor directory."""
    sensor_dir = base / sensor_id
    sensor_dir.mkdir(parents=True)
    w1_slave = sensor_dir / "w1_slave"
    w1_slave.write_text(f"{line0}\n{line1}\n", encoding="ascii")
    return sensor_dir


# ---------------------------------------------------------------------------
# _read_sensor_file — happy path
# ---------------------------------------------------------------------------


class TestReadSensorFileHappyPath:
    def test_valid_ds18b20_21_degrees(self, tmp_path):
        _make_sensor(
            tmp_path,
            "28-000000000001",
            "50 05 4b 46 7f ff 0c 10 1c : crc=1c YES",
            "50 05 4b 46 7f ff 0c 10 1c t=21312",
        )
        result = _read_sensor_file(tmp_path / "28-000000000001")
        assert result == pytest.approx(21.312, abs=1e-6)

    def test_zero_degrees(self, tmp_path):
        _make_sensor(
            tmp_path,
            "28-zero",
            "xx xx xx xx : crc=xx YES",
            "xx xx xx xx t=0",
        )
        result = _read_sensor_file(tmp_path / "28-zero")
        assert result == 0.0

    def test_negative_temperature(self, tmp_path):
        _make_sensor(
            tmp_path,
            "28-negative",
            "xx : crc=xx YES",
            "xx t=-5000",
        )
        result = _read_sensor_file(tmp_path / "28-negative")
        assert result == pytest.approx(-5.0, abs=1e-6)

    def test_rounding_to_3_decimals(self, tmp_path):
        _make_sensor(
            tmp_path,
            "28-precise",
            "xx : crc=xx YES",
            "xx t=21875",  # 21.875 exactly
        )
        result = _read_sensor_file(tmp_path / "28-precise")
        assert result == pytest.approx(21.875, abs=1e-6)


# ---------------------------------------------------------------------------
# _read_sensor_file — error paths
# ---------------------------------------------------------------------------


class TestReadSensorFileErrors:
    def test_missing_w1_slave_returns_none(self, tmp_path):
        sensor_dir = tmp_path / "28-missing"
        sensor_dir.mkdir()
        # w1_slave NOT created
        result = _read_sensor_file(sensor_dir)
        assert result is None

    def test_nonexistent_sensor_dir_returns_none(self, tmp_path):
        result = _read_sensor_file(tmp_path / "28-ghost" / "ghost")
        assert result is None

    def test_crc_error_returns_none(self, tmp_path):
        _make_sensor(
            tmp_path,
            "28-crcfail",
            "50 05 4b 46 7f ff 0c 10 1c : crc=1c NO",  # NO instead of YES
            "50 05 4b 46 7f ff 0c 10 1c t=21312",
        )
        result = _read_sensor_file(tmp_path / "28-crcfail")
        assert result is None

    def test_only_one_line_returns_none(self, tmp_path):
        sensor_dir = tmp_path / "28-short"
        sensor_dir.mkdir()
        (sensor_dir / "w1_slave").write_text("only one line\n", encoding="ascii")
        result = _read_sensor_file(sensor_dir)
        assert result is None

    def test_missing_t_field_returns_none(self, tmp_path):
        _make_sensor(
            tmp_path,
            "28-nofield",
            "xx : crc=xx YES",
            "xx no_temperature_here",
        )
        result = _read_sensor_file(tmp_path / "28-nofield")
        assert result is None


# ---------------------------------------------------------------------------
# scan_sensors
# ---------------------------------------------------------------------------


class TestScanSensors:
    def test_empty_path_returns_empty(self, tmp_path):
        result = scan_sensors(str(tmp_path))
        assert result == []

    def test_nonexistent_path_returns_empty(self, tmp_path):
        result = scan_sensors(str(tmp_path / "does_not_exist"))
        assert result == []

    def test_finds_sensor_dirs(self, tmp_path):
        (tmp_path / "28-000000000001").mkdir()
        (tmp_path / "28-000000000002").mkdir()
        result = sorted(scan_sensors(str(tmp_path)))
        assert result == ["28-000000000001", "28-000000000002"]

    def test_excludes_w1_bus_master1(self, tmp_path):
        (tmp_path / "28-000000000001").mkdir()
        (tmp_path / "w1_bus_master1").mkdir()
        result = scan_sensors(str(tmp_path))
        assert "w1_bus_master1" not in result
        assert "28-000000000001" in result

    def test_ignores_files(self, tmp_path):
        (tmp_path / "28-sensor").mkdir()
        (tmp_path / "somefile.txt").write_text("data")
        result = scan_sensors(str(tmp_path))
        assert "somefile.txt" not in result
        assert "28-sensor" in result


def _mock_create_task(coro, *, name=None, context=None):
    if asyncio.iscoroutine(coro):
        coro.close()
    return mock.MagicMock()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_initial_state(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        assert adapter._poll_tasks == []
        assert adapter._available is False

    def test_config_defaults_applied(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        assert adapter._cfg.poll_interval == 30.0
        assert adapter._cfg.w1_path == str(tmp_path)


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_path_not_found_disables_adapter(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(
            event_bus=mock_bus,
            config={"w1_path": str(tmp_path / "nonexistent")},
        )
        await adapter.connect()
        assert adapter._available is False
        assert mock_bus.publish.called

    @pytest.mark.asyncio
    async def test_path_found_enables_adapter(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        await adapter.connect()
        assert adapter._available is True
        assert adapter.connected is True


# ---------------------------------------------------------------------------
# disconnect()
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_cancels_poll_tasks_and_publishes_status(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})

        async def sleeper():
            await asyncio.sleep(100)

        adapter._poll_tasks = [
            asyncio.create_task(sleeper()),
            asyncio.create_task(sleeper()),
        ]

        await adapter.disconnect()

        assert adapter._poll_tasks == []
        assert mock_bus.publish.called

    @pytest.mark.asyncio
    async def test_disconnect_with_no_tasks(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        await adapter.disconnect()
        assert adapter._poll_tasks == []


# ---------------------------------------------------------------------------
# _on_bindings_reloaded()
# ---------------------------------------------------------------------------


class TestOnBindingsReloaded:
    @pytest.mark.asyncio
    async def test_cancels_old_tasks_before_reload(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})

        async def sleeper():
            await asyncio.sleep(100)

        old_task = asyncio.create_task(sleeper())
        adapter._poll_tasks = [old_task]
        # _available is False so no new tasks are created

        await adapter._on_bindings_reloaded()
        await asyncio.sleep(0)  # let event loop process the cancellation

        assert old_task.cancelled()
        assert adapter._poll_tasks == []

    @pytest.mark.asyncio
    async def test_returns_early_when_not_available(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        adapter._bindings = [make_binding({"sensor_id": "28-001"})]

        await adapter._on_bindings_reloaded()

        assert adapter._poll_tasks == []

    @pytest.mark.asyncio
    async def test_creates_tasks_for_source_and_both_only(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        adapter._available = True
        adapter._bindings = [
            make_binding({"sensor_id": "28-001"}, direction="SOURCE"),
            make_binding({"sensor_id": "28-002"}, direction="BOTH"),
            make_binding({"sensor_id": "28-003"}, direction="DEST"),
        ]

        with mock.patch.object(asyncio, "create_task", side_effect=_mock_create_task):
            await adapter._on_bindings_reloaded()

        assert len(adapter._poll_tasks) == 2  # SOURCE + BOTH, not DEST


# ---------------------------------------------------------------------------
# _poll_loop()
# ---------------------------------------------------------------------------


class TestPollLoop:
    @pytest.mark.asyncio
    async def test_invalid_binding_config_returns_immediately(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        bad_binding = make_binding({})  # missing sensor_id -> ValidationError

        await adapter._poll_loop(bad_binding)

        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_good_quality_event_on_valid_read(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(
            event_bus=mock_bus,
            config={"poll_interval": 0.0, "w1_path": str(tmp_path)},
        )
        binding = make_binding({"sensor_id": "28-001"})

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch("obs.adapters.onewire.adapter._read_sensor_file", return_value=21.5):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        event = mock_bus.publish.call_args[0][0]
        assert event.value == pytest.approx(21.5)
        assert event.quality == "good"

    @pytest.mark.asyncio
    async def test_publishes_bad_quality_when_sensor_returns_none(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(
            event_bus=mock_bus,
            config={"poll_interval": 0.0, "w1_path": str(tmp_path)},
        )
        binding = make_binding({"sensor_id": "28-001"})

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch("obs.adapters.onewire.adapter._read_sensor_file", return_value=None):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        event = mock_bus.publish.call_args[0][0]
        assert event.quality == "bad"
        assert event.value is None

    @pytest.mark.asyncio
    async def test_formula_applied_when_set(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(
            event_bus=mock_bus,
            config={"poll_interval": 0.0, "w1_path": str(tmp_path)},
        )
        binding = make_binding({"sensor_id": "28-001"}, value_formula="x * 2")

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch("obs.adapters.onewire.adapter._read_sensor_file", return_value=10.0):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        event = mock_bus.publish.call_args[0][0]
        assert event.value == pytest.approx(20.0)

    @pytest.mark.asyncio
    async def test_value_map_applied_when_set(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(
            event_bus=mock_bus,
            config={"poll_interval": 0.0, "w1_path": str(tmp_path)},
        )
        binding = make_binding({"sensor_id": "28-001"}, value_map={"21.5": "warm"})

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch("obs.adapters.onewire.adapter._read_sensor_file", return_value=21.5):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        event = mock_bus.publish.call_args[0][0]
        assert event.value == "warm"

    @pytest.mark.asyncio
    async def test_read_error_publishes_bad_quality_and_continues(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(
            event_bus=mock_bus,
            config={"poll_interval": 0.0, "w1_path": str(tmp_path)},
        )
        binding = make_binding({"sensor_id": "28-001"})

        published = asyncio.Event()

        async def track(ev):
            published.set()

        mock_bus.publish.side_effect = track

        with mock.patch(
            "obs.adapters.onewire.adapter._read_sensor_file",
            side_effect=OSError("sensor read failed"),
        ):
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.wait_for(published.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        event = mock_bus.publish.call_args[0][0]
        assert event.quality == "bad"
        assert event.value is None

    @pytest.mark.asyncio
    async def test_cancelled_error_inside_try_exits_loop_cleanly(self, mock_bus, tmp_path):
        """CancelledError raised inside the try block is caught and the loop returns."""
        adapter = OneWireAdapter(
            event_bus=mock_bus,
            config={"poll_interval": 0.0, "w1_path": str(tmp_path)},
        )
        binding = make_binding({"sensor_id": "28-001"})

        async def raise_cancelled(ev):
            raise asyncio.CancelledError()

        mock_bus.publish.side_effect = raise_cancelled

        with mock.patch("obs.adapters.onewire.adapter._read_sensor_file", return_value=21.5):
            result = await adapter._poll_loop(binding)

        assert result is None  # coroutine returned normally (no exception propagated)


# ---------------------------------------------------------------------------
# read()
# ---------------------------------------------------------------------------


class TestRead:
    @pytest.mark.asyncio
    async def test_returns_none_when_not_available(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        binding = make_binding({"sensor_id": "28-001"})

        result = await adapter.read(binding)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_sensor_value_when_available(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        adapter._available = True
        binding = make_binding({"sensor_id": "28-001"})

        with mock.patch("obs.adapters.onewire.adapter._read_sensor_file", return_value=22.5):
            result = await adapter.read(binding)

        assert result == pytest.approx(22.5)

    @pytest.mark.asyncio
    async def test_exception_in_read_returns_none(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        adapter._available = True
        bad_binding = make_binding({})  # missing sensor_id -> ValidationError

        result = await adapter.read(bad_binding)

        assert result is None


# ---------------------------------------------------------------------------
# write()
# ---------------------------------------------------------------------------


class TestWrite:
    @pytest.mark.asyncio
    async def test_write_is_a_no_op(self, mock_bus, tmp_path):
        adapter = OneWireAdapter(event_bus=mock_bus, config={"w1_path": str(tmp_path)})
        binding = make_binding({"sensor_id": "28-001"})

        await adapter.write(binding, 42.0)

        mock_bus.publish.assert_not_called()


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _read_sensor_file — exception path (lines 212-213)
# ---------------------------------------------------------------------------


class TestReadSensorFileExceptionPath:
    def test_non_numeric_t_value_returns_none(self, tmp_path):
        # int("not_a_number") triggers except Exception: return None
        _make_sensor(
            tmp_path,
            "28-parsefail",
            "xx : crc=xx YES",
            "xx t=not_a_number",
        )
        result = _read_sensor_file(tmp_path / "28-parsefail")
        assert result is None
