"""Unit tests for obs.history.sqlite_plugin.

All tests use an in-memory SQLite database via the real Database class so
no mocking of SQL is required and every branch is exercised end-to-end.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from obs.history.sqlite_plugin import (
    SQLiteHistoryPlugin,
    _aggregate_python,
    _bucket_key,
    _safe_loads,
    _to_sqlite_ts,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    """Minimal in-memory Database with the history_values table."""
    from obs.db.database import Database

    db_path = str(tmp_path / "test.db")
    d = Database(db_path)
    await d.connect()
    # Create only what the plugin needs
    await d.execute(
        """CREATE TABLE IF NOT EXISTS history_values (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            datapoint_id   TEXT NOT NULL,
            value          TEXT,
            unit           TEXT,
            quality        TEXT NOT NULL DEFAULT '',
            ts             TEXT NOT NULL,
            source_adapter TEXT
        )"""
    )
    await d.commit()
    yield d
    await d.disconnect()


@pytest.fixture
async def plugin(db):
    return SQLiteHistoryPlugin(db)


def _ts(offset_minutes: int = 0) -> datetime:
    return datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC) + timedelta(minutes=offset_minutes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestToSqliteTs:
    def test_format(self):
        dt = datetime(2024, 6, 15, 10, 30, 45, 123456, tzinfo=UTC)
        result = _to_sqlite_ts(dt)
        assert result == "2024-06-15T10:30:45.123Z"

    def test_millisecond_truncation(self):
        dt = datetime(2024, 1, 1, 0, 0, 0, 999999, tzinfo=UTC)
        result = _to_sqlite_ts(dt)
        assert result.endswith("Z")
        assert "." in result


class TestSafeLoads:
    def test_none_returns_none(self):
        assert _safe_loads(None) is None

    def test_valid_json_int(self):
        assert _safe_loads("42") == 42

    def test_valid_json_float(self):
        assert _safe_loads("3.14") == pytest.approx(3.14)

    def test_valid_json_string(self):
        assert _safe_loads('"hello"') == "hello"

    def test_invalid_json_returns_raw(self):
        assert _safe_loads("not-json") == "not-json"


class TestBucketKey:
    def test_5m_bucket(self):
        ts = "2024-01-01T12:07:30Z"
        assert _bucket_key(ts, 5) == "2024-01-01T12:05:00"

    def test_15m_bucket(self):
        ts = "2024-01-01T12:22:00Z"
        assert _bucket_key(ts, 15) == "2024-01-01T12:15:00"

    def test_30m_bucket_upper_half(self):
        ts = "2024-01-01T12:45:00Z"
        assert _bucket_key(ts, 30) == "2024-01-01T12:30:00"

    def test_invalid_ts_returns_truncated(self):
        result = _bucket_key("bad-ts-string-xyz", 5)
        # Falls back to ts[:16]
        assert result == "bad-ts-string-xy"


class TestAggregatePython:
    def _rows(self, pairs):
        return [{"ts": ts, "value": json.dumps(v)} for ts, v in pairs]

    def test_avg(self):
        rows = self._rows(
            [
                ("2024-01-01T12:00:00Z", 10.0),
                ("2024-01-01T12:02:00Z", 20.0),
            ]
        )
        result = _aggregate_python(rows, "avg", 5)
        assert len(result) == 1
        assert result[0]["v"] == pytest.approx(15.0)

    def test_min(self):
        rows = self._rows(
            [
                ("2024-01-01T12:00:00Z", 5.0),
                ("2024-01-01T12:01:00Z", 3.0),
            ]
        )
        result = _aggregate_python(rows, "min", 5)
        assert result[0]["v"] == pytest.approx(3.0)

    def test_max(self):
        rows = self._rows(
            [
                ("2024-01-01T12:00:00Z", 5.0),
                ("2024-01-01T12:01:00Z", 3.0),
            ]
        )
        result = _aggregate_python(rows, "max", 5)
        assert result[0]["v"] == pytest.approx(5.0)

    def test_last(self):
        rows = self._rows(
            [
                ("2024-01-01T12:00:00Z", 1.0),
                ("2024-01-01T12:02:00Z", 9.0),
            ]
        )
        result = _aggregate_python(rows, "last", 5)
        assert result[0]["v"] == pytest.approx(9.0)

    def test_non_numeric_value_skipped(self):
        rows = self._rows([("2024-01-01T12:00:00Z", "text")])
        result = _aggregate_python(rows, "avg", 5)
        assert result == []

    def test_unknown_fn_returns_empty(self):
        rows = self._rows([("2024-01-01T12:00:00Z", 1.0)])
        result = _aggregate_python(rows, "unknown", 5)
        assert result == []

    def test_two_buckets(self):
        rows = self._rows(
            [
                ("2024-01-01T12:00:00Z", 1.0),
                ("2024-01-01T12:10:00Z", 2.0),
            ]
        )
        result = _aggregate_python(rows, "avg", 5)
        assert len(result) == 2

    def test_tuple_rows(self):
        """Rows may be tuples (index-based) rather than dicts."""
        rows = [("2024-01-01T12:00:00Z", "5.0")]
        result = _aggregate_python(rows, "avg", 5)
        assert result[0]["v"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# SQLiteHistoryPlugin — write / query
# ---------------------------------------------------------------------------


class TestSQLiteWrite:
    async def test_write_stores_row(self, plugin, db):
        dp_id = uuid.uuid4()
        await plugin.write(dp_id, 42.0, "°C", "good", ts=_ts())
        rows = await db.fetchall("SELECT * FROM history_values WHERE datapoint_id=?", (str(dp_id),))
        assert len(rows) == 1
        assert json.loads(rows[0]["value"]) == 42.0
        assert rows[0]["unit"] == "°C"
        assert rows[0]["quality"] == "good"

    async def test_write_without_ts_uses_now(self, plugin, db):
        dp_id = uuid.uuid4()
        await plugin.write(dp_id, 1.0, None, "ok")
        rows = await db.fetchall("SELECT ts FROM history_values WHERE datapoint_id=?", (str(dp_id),))
        assert len(rows) == 1
        assert rows[0]["ts"].endswith("Z")

    async def test_write_string_value(self, plugin, db):
        dp_id = uuid.uuid4()
        await plugin.write(dp_id, "ON", None, "ok", ts=_ts())
        rows = await db.fetchall("SELECT value FROM history_values WHERE datapoint_id=?", (str(dp_id),))
        assert json.loads(rows[0]["value"]) == "ON"

    async def test_write_bool_value(self, plugin, db):
        dp_id = uuid.uuid4()
        await plugin.write(dp_id, True, None, "ok", ts=_ts())
        rows = await db.fetchall("SELECT value FROM history_values WHERE datapoint_id=?", (str(dp_id),))
        assert json.loads(rows[0]["value"]) is True


class TestSQLiteQuery:
    async def _write_n(self, plugin, dp_id, n):
        for i in range(n):
            await plugin.write(dp_id, float(i), None, "ok", ts=_ts(i))

    async def test_query_returns_rows_in_range(self, plugin):
        dp_id = uuid.uuid4()
        await self._write_n(plugin, dp_id, 5)
        result = await plugin.query(dp_id, _ts(0), _ts(4))
        assert len(result) == 5

    async def test_query_respects_limit(self, plugin):
        dp_id = uuid.uuid4()
        await self._write_n(plugin, dp_id, 10)
        result = await plugin.query(dp_id, _ts(0), _ts(9), limit=3)
        assert len(result) == 3

    async def test_query_order_desc(self, plugin):
        dp_id = uuid.uuid4()
        await self._write_n(plugin, dp_id, 3)
        result = await plugin.query(dp_id, _ts(0), _ts(2))
        # newest first
        assert result[0]["v"] >= result[-1]["v"]

    async def test_query_empty_range(self, plugin):
        dp_id = uuid.uuid4()
        result = await plugin.query(dp_id, _ts(100), _ts(200))
        assert result == []

    async def test_query_result_shape(self, plugin):
        dp_id = uuid.uuid4()
        await plugin.write(dp_id, 7.5, "V", "good", ts=_ts())
        result = await plugin.query(dp_id, _ts(-1), _ts(1))
        assert len(result) == 1
        row = result[0]
        assert "ts" in row
        assert "v" in row
        assert "u" in row
        assert "q" in row
        assert row["v"] == pytest.approx(7.5)
        assert row["u"] == "V"


# ---------------------------------------------------------------------------
# SQLiteHistoryPlugin — aggregate
# ---------------------------------------------------------------------------


class TestSQLiteAggregate:
    async def _write_series(self, plugin, dp_id, values_with_offset):
        for offset, val in values_with_offset:
            await plugin.write(dp_id, float(val), None, "ok", ts=_ts(offset))

    async def test_aggregate_avg_1h(self, plugin):
        dp_id = uuid.uuid4()
        await self._write_series(plugin, dp_id, [(0, 10.0), (30, 20.0)])
        result = await plugin.aggregate(dp_id, "avg", "1h", _ts(-1), _ts(60))
        assert len(result) == 1
        assert result[0]["v"] == pytest.approx(15.0)

    async def test_aggregate_min_1h(self, plugin):
        dp_id = uuid.uuid4()
        await self._write_series(plugin, dp_id, [(0, 3.0), (30, 9.0)])
        result = await plugin.aggregate(dp_id, "min", "1h", _ts(-1), _ts(60))
        assert result[0]["v"] == pytest.approx(3.0)

    async def test_aggregate_max_1h(self, plugin):
        dp_id = uuid.uuid4()
        await self._write_series(plugin, dp_id, [(0, 3.0), (30, 9.0)])
        result = await plugin.aggregate(dp_id, "max", "1h", _ts(-1), _ts(60))
        assert result[0]["v"] == pytest.approx(9.0)

    async def test_aggregate_last_1h(self, plugin):
        dp_id = uuid.uuid4()
        await self._write_series(plugin, dp_id, [(0, 1.0), (30, 99.0)])
        result = await plugin.aggregate(dp_id, "last", "1h", _ts(-1), _ts(60))
        assert len(result) == 1
        assert result[0]["v"] == pytest.approx(99.0)

    async def test_aggregate_sub_hourly_avg(self, plugin):
        """5m interval triggers Python-side aggregation."""
        dp_id = uuid.uuid4()
        await self._write_series(plugin, dp_id, [(0, 10.0), (2, 20.0)])
        result = await plugin.aggregate(dp_id, "avg", "5m", _ts(-1), _ts(10))
        assert len(result) == 1
        assert result[0]["v"] == pytest.approx(15.0)

    async def test_aggregate_sub_hourly_last(self, plugin):
        dp_id = uuid.uuid4()
        await self._write_series(plugin, dp_id, [(0, 5.0), (2, 8.0)])
        result = await plugin.aggregate(dp_id, "last", "5m", _ts(-1), _ts(10))
        assert result[0]["v"] == pytest.approx(8.0)

    async def test_aggregate_unknown_interval_falls_back_to_1h(self, plugin):
        dp_id = uuid.uuid4()
        await self._write_series(plugin, dp_id, [(0, 42.0)])
        result = await plugin.aggregate(dp_id, "avg", "bad_interval", _ts(-1), _ts(60))
        assert len(result) == 1

    async def test_aggregate_empty_returns_empty(self, plugin):
        dp_id = uuid.uuid4()
        result = await plugin.aggregate(dp_id, "avg", "1h", _ts(100), _ts(200))
        assert result == []


# ---------------------------------------------------------------------------
# SQLiteHistoryPlugin — delete_before
# ---------------------------------------------------------------------------


class TestSQLiteDeleteBefore:
    async def test_delete_before_removes_old_rows(self, plugin, db):
        dp_id = uuid.uuid4()
        for i in range(5):
            await plugin.write(dp_id, float(i), None, "ok", ts=_ts(i))
        deleted = await plugin.delete_before(dp_id, _ts(3))
        assert deleted == 3
        rows = await db.fetchall("SELECT * FROM history_values WHERE datapoint_id=?", (str(dp_id),))
        assert len(rows) == 2

    async def test_delete_before_nothing_to_delete(self, plugin):
        dp_id = uuid.uuid4()
        await plugin.write(dp_id, 1.0, None, "ok", ts=_ts(10))
        deleted = await plugin.delete_before(dp_id, _ts(0))
        assert deleted == 0


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------


class TestSQLiteModuleSingleton:
    def test_get_raises_when_not_initialized(self):
        import obs.history.sqlite_plugin as mod

        mod._plugin = None
        from obs.history.sqlite_plugin import get_history_plugin

        with pytest.raises(RuntimeError, match="not initialized"):
            get_history_plugin()

    def test_init_sets_and_returns_plugin(self, db):
        from obs.history.sqlite_plugin import get_history_plugin, init_history_plugin

        p = init_history_plugin(db)
        assert isinstance(p, SQLiteHistoryPlugin)
        assert get_history_plugin() is p

    def teardown_method(self, _):
        import obs.history.sqlite_plugin as mod

        mod._plugin = None
