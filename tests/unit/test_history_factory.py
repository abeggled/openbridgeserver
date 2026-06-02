"""Unit tests for obs.history.factory.

All external dependencies (asyncpg, xknxproject, DB) are mocked so no
Docker / real database is required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import pytest

import obs.history.factory as factory_mod
from obs.history.factory import (
    _create_influxdb_plugin,
    _create_timescaledb_plugin,
    _load_history_settings,
    get_history_plugin,
    handle_value_event,
    init_history_plugin,
    reload_history_plugin,
    reset_history_plugin,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_plugin():
    """Always start each test with a clean plugin singleton."""
    reset_history_plugin()
    yield
    reset_history_plugin()


def _fake_db(rows=None):
    """Return a mock DB whose fetchall() returns the given rows."""
    db = mock.AsyncMock()
    db.fetchall = mock.AsyncMock(return_value=rows or [])
    return db


def _row(key, value):
    return {"key": key, "value": value}


# ---------------------------------------------------------------------------
# get_history_plugin
# ---------------------------------------------------------------------------


class TestGetHistoryPlugin:
    def test_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="not initialized"):
            get_history_plugin()


# ---------------------------------------------------------------------------
# reset_history_plugin
# ---------------------------------------------------------------------------


class TestResetHistoryPlugin:
    def test_reset_clears_singleton(self):
        factory_mod._plugin = mock.MagicMock()
        reset_history_plugin()
        assert factory_mod._plugin is None


# ---------------------------------------------------------------------------
# _load_history_settings
# ---------------------------------------------------------------------------


class TestLoadHistorySettings:
    async def test_parses_rows(self):
        db = _fake_db([_row("history.plugin", "influxdb"), _row("history.influx_url", "http://host:8086")])
        cfg = await _load_history_settings(db)
        assert cfg["plugin"] == "influxdb"
        assert cfg["influx_url"] == "http://host:8086"

    async def test_empty_db_returns_empty_dict(self):
        cfg = await _load_history_settings(_fake_db())
        assert cfg == {}

    async def test_none_value_becomes_empty_string(self):
        db = _fake_db([_row("history.plugin", None)])
        cfg = await _load_history_settings(db)
        assert cfg["plugin"] == ""


# ---------------------------------------------------------------------------
# _create_influxdb_plugin
# ---------------------------------------------------------------------------


class TestCreateInfluxdbPlugin:
    def test_creates_plugin_with_defaults(self):
        p = _create_influxdb_plugin({})
        from obs.history.influxdb_plugin import InfluxDBHistoryPlugin

        assert isinstance(p, InfluxDBHistoryPlugin)

    def test_creates_plugin_with_custom_values(self):
        cfg = {
            "influx_url": "http://custom:9999",
            "influx_version": "1",
            "influx_token": "tok",
            "influx_org": "org",
            "influx_bucket": "bkt",
            "influx_database": "mydb",
            "influx_username": "user",
            "influx_password": "pass",
        }
        p = _create_influxdb_plugin(cfg)
        assert p._url == "http://custom:9999"
        assert p._version == 1


# ---------------------------------------------------------------------------
# _create_timescaledb_plugin
# ---------------------------------------------------------------------------


class TestCreateTimescaledbPlugin:
    async def test_raises_when_dsn_missing(self):
        with pytest.raises(ValueError, match="timescale_dsn"):
            await _create_timescaledb_plugin({})

    async def test_creates_and_connects(self):
        fake_plugin = mock.AsyncMock()
        fake_module = mock.MagicMock()
        fake_module.TimescaleDBHistoryPlugin = mock.MagicMock(return_value=fake_plugin)
        import sys

        with mock.patch.dict(sys.modules, {"obs.history.timescaledb_plugin": fake_module}):
            result = await _create_timescaledb_plugin({"timescale_dsn": "postgresql://x"})
        fake_plugin.connect.assert_awaited_once()
        assert result is fake_plugin


# ---------------------------------------------------------------------------
# init_history_plugin
# ---------------------------------------------------------------------------


class TestInitHistoryPlugin:
    async def test_defaults_to_sqlite(self):
        db = _fake_db()
        plugin = await init_history_plugin(db)
        from obs.history.sqlite_plugin import SQLiteHistoryPlugin

        assert isinstance(plugin, SQLiteHistoryPlugin)

    async def test_sqlite_plugin_registered(self):
        db = _fake_db()
        await init_history_plugin(db)
        assert get_history_plugin() is not None

    async def test_influxdb_selected(self):
        db = _fake_db([_row("history.plugin", "influxdb")])
        plugin = await init_history_plugin(db)
        from obs.history.influxdb_plugin import InfluxDBHistoryPlugin

        assert isinstance(plugin, InfluxDBHistoryPlugin)

    async def test_timescaledb_selected(self):
        import sys

        db = _fake_db([_row("history.plugin", "timescaledb"), _row("history.timescale_dsn", "postgresql://x")])
        fake_plugin = mock.AsyncMock()
        fake_module = mock.MagicMock()
        fake_module.TimescaleDBHistoryPlugin = mock.MagicMock(return_value=fake_plugin)
        with mock.patch.dict(sys.modules, {"obs.history.timescaledb_plugin": fake_module}):
            plugin = await init_history_plugin(db)
        assert plugin is fake_plugin


# ---------------------------------------------------------------------------
# reload_history_plugin
# ---------------------------------------------------------------------------


class TestReloadHistoryPlugin:
    async def test_disconnects_old_plugin(self):
        old = mock.AsyncMock()
        old.disconnect = mock.AsyncMock()
        factory_mod._plugin = old
        db = _fake_db()
        await reload_history_plugin(db)
        old.disconnect.assert_awaited_once()

    async def test_old_plugin_without_disconnect(self):
        old = mock.MagicMock(spec=[])  # no disconnect attribute
        factory_mod._plugin = old
        db = _fake_db()
        # Should not raise
        await reload_history_plugin(db)

    async def test_disconnect_error_is_swallowed(self):
        old = mock.AsyncMock()
        old.disconnect = mock.AsyncMock(side_effect=Exception("boom"))
        factory_mod._plugin = old
        db = _fake_db()
        # Should not raise
        await reload_history_plugin(db)

    async def test_returns_new_plugin(self):
        factory_mod._plugin = mock.AsyncMock()
        db = _fake_db()
        new_plugin = await reload_history_plugin(db)
        from obs.history.sqlite_plugin import SQLiteHistoryPlugin

        assert isinstance(new_plugin, SQLiteHistoryPlugin)


# ---------------------------------------------------------------------------
# handle_value_event
# ---------------------------------------------------------------------------


class TestHandleValueEvent:
    def _event(self, dp_id=None, value=1.0, quality="good", ts=None, source_adapter=None):
        e = SimpleNamespace(
            datapoint_id=dp_id or uuid.uuid4(),
            value=value,
            quality=quality,
            ts=ts or datetime.now(UTC),
            source_adapter=source_adapter,
        )
        return e

    async def test_noop_when_no_plugin(self):
        # _plugin is None — should silently return
        await handle_value_event(self._event())

    async def test_writes_to_plugin(self):
        fake = mock.AsyncMock()
        factory_mod._plugin = fake
        ev = self._event(value=42.0, quality="ok")
        # Mock registry to return a DP with record_history=True
        dp = SimpleNamespace(record_history=True, unit="°C")
        fake_registry = mock.MagicMock()
        fake_registry.get.return_value = dp
        with mock.patch("obs.core.registry.get_registry", return_value=fake_registry):
            await handle_value_event(ev)
        fake.write.assert_awaited_once()

    async def test_skips_when_record_history_false(self):
        fake = mock.AsyncMock()
        factory_mod._plugin = fake
        ev = self._event()
        dp = SimpleNamespace(record_history=False, unit=None)
        fake_registry = mock.MagicMock()
        fake_registry.get.return_value = dp
        with mock.patch("obs.core.registry.get_registry", return_value=fake_registry):
            await handle_value_event(ev)
        fake.write.assert_not_awaited()

    async def test_write_error_does_not_propagate(self):
        fake = mock.AsyncMock()
        fake.write = mock.AsyncMock(side_effect=Exception("db down"))
        factory_mod._plugin = fake
        ev = self._event()
        with mock.patch("obs.core.registry.get_registry", side_effect=RuntimeError("not init")):
            # registry not available → unit=None, write still called and error swallowed
            await handle_value_event(ev)

    async def test_registry_runtime_error_still_writes(self):
        """If registry is not initialized, write is still attempted (unit=None)."""
        fake = mock.AsyncMock()
        factory_mod._plugin = fake
        ev = self._event()
        with mock.patch("obs.core.registry.get_registry", side_effect=RuntimeError("not init")):
            await handle_value_event(ev)
        fake.write.assert_awaited_once()
