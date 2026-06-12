"""Unit tests for obs.history.timescaledb_plugin.

asyncpg and all DB connections are fully mocked — no PostgreSQL instance required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from obs.history.timescaledb_plugin import TimescaleDBHistoryPlugin, _pg_bucket_expr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plugin(dsn="postgresql://test/test") -> TimescaleDBHistoryPlugin:
    return TimescaleDBHistoryPlugin(dsn=dsn)


def _ts(offset_h: int = 0) -> datetime:
    return datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC) + timedelta(hours=offset_h)


def _make_pool_mock(rows=None, execute_side_effect=None):
    """Build a mock asyncpg pool with acquire() context manager."""
    conn = mock.AsyncMock()
    conn.fetchval = mock.AsyncMock(return_value=1)
    conn.fetch = mock.AsyncMock(return_value=rows or [])

    if execute_side_effect:
        # execute raises on first call (hypertable), succeeds otherwise
        conn.execute = mock.AsyncMock(side_effect=execute_side_effect)
    else:
        conn.execute = mock.AsyncMock(return_value=None)

    acq_ctx = mock.MagicMock()
    acq_ctx.__aenter__ = mock.AsyncMock(return_value=conn)
    acq_ctx.__aexit__ = mock.AsyncMock(return_value=False)

    pool = mock.AsyncMock()
    pool.acquire = mock.MagicMock(return_value=acq_ctx)
    pool.close = mock.AsyncMock()
    return pool, conn


def _make_asyncpg_mock(pool):
    """Return a mock asyncpg module whose create_pool returns the given pool."""
    asyncpg = mock.MagicMock()
    asyncpg.create_pool = mock.AsyncMock(return_value=pool)
    conn_mock = mock.AsyncMock()
    conn_mock.fetchval = mock.AsyncMock(return_value=1)
    conn_mock.close = mock.AsyncMock()
    asyncpg.connect = mock.AsyncMock(return_value=conn_mock)
    return asyncpg


# ---------------------------------------------------------------------------
# _pg_bucket_expr
# ---------------------------------------------------------------------------


class TestPgBucketExpr:
    def test_1_hour(self):
        assert _pg_bucket_expr("1 hour") == "date_trunc('hour', time)"

    def test_1_day(self):
        assert _pg_bucket_expr("1 day") == "date_trunc('day', time)"

    def test_1_minute(self):
        assert _pg_bucket_expr("1 minute") == "date_trunc('minute', time)"

    def test_5_minutes(self):
        expr = _pg_bucket_expr("5 minutes")
        assert "300" in expr  # 5 * 60

    def test_15_minutes(self):
        expr = _pg_bucket_expr("15 minutes")
        assert "900" in expr

    def test_30_minutes(self):
        expr = _pg_bucket_expr("30 minutes")
        assert "1800" in expr

    def test_6_hours(self):
        expr = _pg_bucket_expr("6 hours")
        assert "21600" in expr

    def test_12_hours(self):
        expr = _pg_bucket_expr("12 hours")
        assert "43200" in expr

    def test_unknown_falls_back_to_hour(self):
        assert _pg_bucket_expr("99 years") == "date_trunc('hour', time)"


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_connect_creates_pool_and_schema(self):
        pool, conn = _make_pool_mock()
        asyncpg = _make_asyncpg_mock(pool)
        p = _plugin()
        with mock.patch.dict(__import__("sys").modules, {"asyncpg": asyncpg}):
            await p.connect()
        asyncpg.create_pool.assert_awaited_once_with(
            "postgresql://test/test",
            min_size=1,
            max_size=5,
            server_settings={"timezone": "UTC"},
        )
        assert p._pool is pool

    async def test_connect_detects_timescaledb(self):
        pool, conn = _make_pool_mock()
        asyncpg = _make_asyncpg_mock(pool)
        p = _plugin()
        with mock.patch.dict(__import__("sys").modules, {"asyncpg": asyncpg}):
            await p.connect()
        assert p._has_timescaledb is True

    async def test_connect_without_timescaledb_extension(self):
        """When the hypertable call raises, has_timescaledb=False."""
        calls = [None, Exception("extension not found"), None]
        pool, conn = _make_pool_mock(execute_side_effect=calls)
        asyncpg = _make_asyncpg_mock(pool)
        p = _plugin()
        with mock.patch.dict(__import__("sys").modules, {"asyncpg": asyncpg}):
            await p.connect()
        assert p._has_timescaledb is False

    async def test_connect_import_error(self):
        import sys

        with mock.patch.dict(sys.modules, {"asyncpg": None}):
            with pytest.raises(RuntimeError, match="asyncpg"):
                await _plugin().connect()

    async def test_disconnect_closes_pool(self):
        pool, _ = _make_pool_mock()
        p = _plugin()
        p._pool = pool
        await p.disconnect()
        pool.close.assert_awaited_once()
        assert p._pool is None

    async def test_disconnect_when_not_connected(self):
        p = _plugin()
        await p.disconnect()  # should not raise

    def test_require_pool_raises_when_none(self):
        p = _plugin()
        with pytest.raises(RuntimeError, match="not connected"):
            p._require_pool()


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


class TestTimescaleWrite:
    async def test_write_inserts_row(self):
        pool, conn = _make_pool_mock()
        p = _plugin()
        p._pool = pool
        await p.write(uuid.uuid4(), 42.0, "°C", "good", ts=_ts())
        conn.execute.assert_awaited_once()
        call_sql = conn.execute.call_args.args[0]
        assert "INSERT INTO dp_history" in call_sql

    async def test_write_nan_converts_to_none(self):
        pool, conn = _make_pool_mock()
        p = _plugin()
        p._pool = pool
        await p.write(uuid.uuid4(), float("nan"), None, "ok", ts=_ts())
        args = conn.execute.call_args.args
        # v_float should be None (index 3 = $3)
        assert args[3] is None

    async def test_write_non_numeric_converts_to_none(self):
        pool, conn = _make_pool_mock()
        p = _plugin()
        p._pool = pool
        await p.write(uuid.uuid4(), "ON", None, "ok", ts=_ts())
        args = conn.execute.call_args.args
        assert args[3] is None

    async def test_write_without_ts_uses_now(self):
        pool, conn = _make_pool_mock()
        p = _plugin()
        p._pool = pool
        await p.write(uuid.uuid4(), 1.0, None, "ok")
        conn.execute.assert_awaited_once()

    async def test_write_requires_pool(self):
        p = _plugin()
        with pytest.raises(RuntimeError):
            await p.write(uuid.uuid4(), 1.0, None, "ok")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


def _make_row(time_dt, raw_json, unit, quality):
    row = mock.MagicMock()
    row.__getitem__ = lambda self, k: {
        "time": time_dt,
        "raw": raw_json,
        "unit": unit,
        "quality": quality,
    }[k]
    return row


class TestTimescaleQuery:
    async def test_query_returns_formatted_rows(self):
        # raw stores JSON; "42.5" (no quotes) → json.loads → float 42.5
        row = _make_row(_ts(), "42.5", "°C", "good")
        pool, conn = _make_pool_mock(rows=[row])
        p = _plugin()
        p._pool = pool
        result = await p.query(uuid.uuid4(), _ts(-1), _ts(1))
        assert len(result) == 1
        assert result[0]["v"] == pytest.approx(42.5)
        assert result[0]["u"] == "°C"
        assert result[0]["q"] == "good"

    async def test_query_invalid_json_raw_returns_string(self):
        row = _make_row(_ts(), "not-json", None, "")
        pool, conn = _make_pool_mock(rows=[row])
        p = _plugin()
        p._pool = pool
        result = await p.query(uuid.uuid4(), _ts(-1), _ts(1))
        assert result[0]["v"] == "not-json"

    async def test_query_none_raw_returns_none(self):
        row = _make_row(_ts(), None, None, "")
        pool, conn = _make_pool_mock(rows=[row])
        p = _plugin()
        p._pool = pool
        result = await p.query(uuid.uuid4(), _ts(-1), _ts(1))
        assert result[0]["v"] is None

    async def test_query_empty_returns_empty_list(self):
        pool, conn = _make_pool_mock(rows=[])
        p = _plugin()
        p._pool = pool
        result = await p.query(uuid.uuid4(), _ts(-1), _ts(1))
        assert result == []

    async def test_query_requires_pool(self):
        p = _plugin()
        with pytest.raises(RuntimeError):
            await p.query(uuid.uuid4(), _ts(-1), _ts(1))


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


def _make_agg_row(bucket_dt, v, n=1):
    row = mock.MagicMock()
    row.__getitem__ = lambda self, k: {"bucket": bucket_dt, "v": v, "n": n}[k]
    row.get = lambda k, d=None: {"bucket": bucket_dt, "v": v, "n": n}.get(k, d)
    return row


class TestTimescaleAggregate:
    async def _run(self, p, fn="avg", interval="1h", rows=None):
        pool, conn = _make_pool_mock(rows=rows or [])
        p._pool = pool
        p._has_timescaledb = True
        result = await p.aggregate(uuid.uuid4(), fn, interval, _ts(0), _ts(2))
        return result, conn

    async def test_aggregate_avg_timescaledb(self):
        row = _make_agg_row(_ts(), 10.0, n=3)
        p = _plugin()
        result, _ = await self._run(p, fn="avg", rows=[row])
        assert result[0]["v"] == pytest.approx(10.0)
        assert result[0]["n"] == 3

    async def test_aggregate_unknown_interval_falls_back(self):
        p = _plugin()
        result, conn = await self._run(p, fn="avg", interval="bad_interval")
        sql = conn.fetch.call_args.args[0]
        # Should have fallen back to "1h" → bucket_str = "1 hour"
        assert "1 hour" in sql

    async def test_aggregate_without_timescaledb_uses_pg_expr(self):
        row = _make_agg_row(_ts(), 5.0)
        pool, conn = _make_pool_mock(rows=[row])
        p = _plugin()
        p._pool = pool
        p._has_timescaledb = False
        await p.aggregate(uuid.uuid4(), "avg", "1h", _ts(0), _ts(2))
        sql = conn.fetch.call_args.args[0]
        assert "date_trunc" in sql

    async def test_aggregate_last_timescaledb(self):
        row = _make_agg_row(_ts(), 99.0)
        pool, conn = _make_pool_mock(rows=[row])
        p = _plugin()
        p._pool = pool
        p._has_timescaledb = True
        result = await p.aggregate(uuid.uuid4(), "last", "1h", _ts(0), _ts(2))
        assert result[0]["v"] == pytest.approx(99.0)
        sql = conn.fetch.call_args.args[0]
        assert "last(" in sql.lower()

    async def test_aggregate_last_without_timescaledb(self):
        row = _make_agg_row(_ts(), 7.0)
        pool, conn = _make_pool_mock(rows=[row])
        p = _plugin()
        p._pool = pool
        p._has_timescaledb = False
        result = await p.aggregate(uuid.uuid4(), "last", "1h", _ts(0), _ts(2))
        assert result[0]["v"] == pytest.approx(7.0)
        sql = conn.fetch.call_args.args[0]
        assert "DISTINCT ON" in sql

    async def test_aggregate_bucket_isoformat(self):
        """Bucket with isoformat() method is serialised as ISO string."""
        row = _make_agg_row(_ts(), 3.0)
        pool, conn = _make_pool_mock(rows=[row])
        p = _plugin()
        p._pool = pool
        p._has_timescaledb = True
        result = await p.aggregate(uuid.uuid4(), "avg", "1h", _ts(0), _ts(2))
        assert "T" in result[0]["bucket"]  # ISO format

    async def test_aggregate_bucket_no_isoformat(self):
        """Bucket without isoformat (plain str) falls back to str()."""
        row = mock.MagicMock()
        row.__getitem__ = lambda self, k: {"bucket": "2024-06-01", "v": 1.0, "n": 2}[k]
        pool, conn = _make_pool_mock(rows=[row])
        p = _plugin()
        p._pool = pool
        p._has_timescaledb = True
        result = await p.aggregate(uuid.uuid4(), "avg", "1h", _ts(0), _ts(2))
        assert result[0]["bucket"] == "2024-06-01"
        assert result[0]["n"] == 2

    async def test_aggregate_requires_pool(self):
        p = _plugin()
        with pytest.raises(RuntimeError):
            await p.aggregate(uuid.uuid4(), "avg", "1h", _ts(0), _ts(1))


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


class TestTimescalePing:
    async def test_ping_success(self):
        asyncpg = _make_asyncpg_mock(mock.AsyncMock())
        p = _plugin()
        with mock.patch.dict(__import__("sys").modules, {"asyncpg": asyncpg}):
            result = await p.ping()
        assert result is True

    async def test_ping_failure_returns_false(self):
        import sys

        asyncpg = mock.MagicMock()
        asyncpg.connect = mock.AsyncMock(side_effect=Exception("connection refused"))
        with mock.patch.dict(sys.modules, {"asyncpg": asyncpg}):
            result = await _plugin().ping()
        assert result is False

    async def test_ping_import_error_raises(self):
        import sys

        with mock.patch.dict(sys.modules, {"asyncpg": None}):
            with pytest.raises(RuntimeError, match="asyncpg"):
                await _plugin().ping()
