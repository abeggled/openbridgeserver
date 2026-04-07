"""
Unit tests for the 1-Wire adapter — filesystem-level functions.
Uses tmp_path; no hardware, no asyncio required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from obs.adapters.onewire.adapter import _read_sensor_file, scan_sensors


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
            tmp_path, "28-000000000001",
            "50 05 4b 46 7f ff 0c 10 1c : crc=1c YES",
            "50 05 4b 46 7f ff 0c 10 1c t=21312",
        )
        result = _read_sensor_file(tmp_path / "28-000000000001")
        assert result == pytest.approx(21.312, abs=1e-6)

    def test_zero_degrees(self, tmp_path):
        _make_sensor(
            tmp_path, "28-zero",
            "xx xx xx xx : crc=xx YES",
            "xx xx xx xx t=0",
        )
        result = _read_sensor_file(tmp_path / "28-zero")
        assert result == 0.0

    def test_negative_temperature(self, tmp_path):
        _make_sensor(
            tmp_path, "28-negative",
            "xx : crc=xx YES",
            "xx t=-5000",
        )
        result = _read_sensor_file(tmp_path / "28-negative")
        assert result == pytest.approx(-5.0, abs=1e-6)

    def test_rounding_to_3_decimals(self, tmp_path):
        _make_sensor(
            tmp_path, "28-precise",
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
            tmp_path, "28-crcfail",
            "50 05 4b 46 7f ff 0c 10 1c : crc=1c NO",   # NO instead of YES
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
            tmp_path, "28-nofield",
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
