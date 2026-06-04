"""Unit tests for obs.history.influxdb_plugin.

All network calls are mocked with unittest.mock so no real
InfluxDB instance is required.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from obs.history.influxdb_plugin import InfluxDBHistoryPlugin, _to_rfc3339


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plugin(version: int = 2, **kwargs) -> InfluxDBHistoryPlugin:
    defaults = dict(
        url="http://localhost:8086",
        version=version,
        token="mytoken",
        org="myorg",
        bucket="mybucket",
        database="mydb",
        username="user",
        password="pass",
    )
    defaults.update(kwargs)
    return InfluxDBHistoryPlugin(**defaults)


def _ts(offset: int = 0) -> datetime:
    return datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC) + timedelta(hours=offset)


def _make_httpx_mock(status=204, json_body=None):
    """Return a (ctx, client) pair where ctx is usable as AsyncClient(...)."""
    resp = mock.MagicMock()
    resp.status_code = status
    resp.raise_for_status = mock.MagicMock()
    if json_body is not None:
        resp.json = mock.MagicMock(return_value=json_body)

    client = mock.AsyncMock()
    client.post = mock.AsyncMock(return_value=resp)
    client.get = mock.AsyncMock(return_value=resp)

    ctx = mock.MagicMock()
    ctx.__aenter__ = mock.AsyncMock(return_value=client)
    ctx.__aexit__ = mock.AsyncMock(return_value=False)
    return ctx, client


# ---------------------------------------------------------------------------
# _to_rfc3339
# ---------------------------------------------------------------------------


class TestToRfc3339:
    def test_aware_datetime(self):
        dt = datetime(2024, 1, 15, 10, 30, 0, 123000, tzinfo=UTC)
        result = _to_rfc3339(dt)
        assert result == "2024-01-15T10:30:00.123Z"

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = _to_rfc3339(dt)
        assert result.endswith("Z")


# ---------------------------------------------------------------------------
# URL / params helpers
# ---------------------------------------------------------------------------


class TestWriteUrlParams:
    def test_v1(self):
        url, params = _plugin(1)._write_url_params()
        assert url.endswith("/write")
        assert params == {"db": "mydb", "precision": "ns"}

    def test_v2(self):
        url, params = _plugin(2)._write_url_params()
        assert url.endswith("/api/v2/write")
        assert params == {"org": "myorg", "bucket": "mybucket", "precision": "ns"}

    def test_v3(self):
        url, params = _plugin(3)._write_url_params()
        assert url.endswith("/api/v3/write_lp")
        assert params == {"db": "mydb", "precision": "nanosecond"}


class TestQueryUrlParams:
    def test_v1_uses_database(self):
        _, params = _plugin(1)._query_url_params()
        assert params["db"] == "mydb"

    def test_v2_uses_bucket(self):
        _, params = _plugin(2)._query_url_params()
        assert params["db"] == "mybucket"

    def test_v3_uses_database(self):
        _, params = _plugin(3)._query_url_params()
        assert params["db"] == "mydb"


# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_v1_no_username_no_auth(self):
        p = _plugin(1, username="", password="")
        h = p._headers()
        assert "Authorization" not in h

    def test_v1_with_username_basic_auth(self):
        h = _plugin(1)._headers()
        assert h["Authorization"].startswith("Basic ")

    def test_v2_bearer_token(self):
        h = _plugin(2)._headers()
        assert h["Authorization"] == "Token mytoken"

    def test_v3_bearer_token(self):
        h = _plugin(3)._headers()
        assert h["Authorization"] == "Bearer mytoken"

    def test_no_content_type_when_empty(self):
        h = _plugin(2)._headers(content_type="")
        assert "Content-Type" not in h


# ---------------------------------------------------------------------------
# _escape helpers
# ---------------------------------------------------------------------------


class TestEscapeHelpers:
    def test_escape_tag_comma(self):
        assert _plugin()._escape_tag("a,b") == r"a\,b"

    def test_escape_tag_space(self):
        assert _plugin()._escape_tag("a b") == r"a\ b"

    def test_escape_tag_equals(self):
        assert _plugin()._escape_tag("a=b") == r"a\=b"

    def test_escape_field_str_quote(self):
        assert _plugin()._escape_field_str('say "hi"') == r"say \"hi\""

    def test_escape_field_str_backslash(self):
        assert _plugin()._escape_field_str("a\\b") == "a\\\\b"


# ---------------------------------------------------------------------------
# _build_line
# ---------------------------------------------------------------------------


class TestBuildLine:
    def test_float_value_adds_v_field(self):
        line = _plugin()._build_line(uuid.uuid4(), 42.5, "°C", "good", _ts())
        assert "v=42.5" in line

    def test_string_value_no_v_field(self):
        line = _plugin()._build_line(uuid.uuid4(), "ON", None, "ok", _ts())
        assert "v=" not in line

    def test_nan_no_v_field(self):
        line = _plugin()._build_line(uuid.uuid4(), float("nan"), None, "ok", _ts())
        assert "v=" not in line

    def test_measurement_name(self):
        line = _plugin()._build_line(uuid.uuid4(), 1.0, None, "ok", _ts())
        assert line.startswith("obs,dp_id=")

    def test_contains_raw_and_quality(self):
        line = _plugin()._build_line(uuid.uuid4(), True, None, "good", _ts())
        assert 'raw="' in line
        assert 'quality="good"' in line


# ---------------------------------------------------------------------------
# _parse_influxql_series
# ---------------------------------------------------------------------------


class TestParseInfluxqlSeries:
    def _make_response(self, columns, values):
        return {"results": [{"series": [{"columns": columns, "values": values}]}]}

    def test_raw_field_query(self):
        # raw stores JSON-encoded values; "42.5" (with quotes) decodes to str "42.5"
        # Store the number unquoted so json.loads returns float
        data = self._make_response(
            ["time", "raw", "quality", "unit"],
            [["2024-01-01T00:00:00Z", "42.5", "good", "°C"]],
        )
        result = _plugin()._parse_influxql_series(data, raw_field=True)
        assert len(result) == 1
        assert result[0]["v"] == pytest.approx(42.5)
        assert result[0]["q"] == "good"
        assert result[0]["u"] == "°C"

    def test_aggregate_field_query(self):
        data = self._make_response(
            ["time", "mean"],
            [["2024-01-01T00:00:00Z", 15.0]],
        )
        result = _plugin()._parse_influxql_series(data, raw_field=False)
        assert result[0]["bucket"] == "2024-01-01T00:00:00Z"
        assert result[0]["v"] == pytest.approx(15.0)

    def test_empty_results(self):
        assert _plugin()._parse_influxql_series({}, raw_field=True) == []

    def test_empty_series(self):
        data = {"results": [{"series": []}]}
        assert _plugin()._parse_influxql_series(data, raw_field=True) == []

    def test_invalid_raw_json_returns_raw_string(self):
        data = self._make_response(
            ["time", "raw", "quality", "unit"],
            [["2024-01-01T00:00:00Z", "not-json", "", None]],
        )
        result = _plugin()._parse_influxql_series(data, raw_field=True)
        assert result[0]["v"] == "not-json"


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


class TestInfluxDBWrite:
    async def test_write_posts_to_endpoint(self):
        ctx, client = _make_httpx_mock(status=204)
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            await _plugin(2).write(uuid.uuid4(), 42.0, "°C", "good", ts=_ts())
        client.post.assert_awaited_once()

    async def test_write_raises_on_http_error(self):
        resp = mock.MagicMock()
        resp.raise_for_status = mock.MagicMock(side_effect=Exception("HTTP 500"))
        ctx = mock.MagicMock()
        ctx.__aenter__ = mock.AsyncMock(return_value=mock.MagicMock(post=mock.AsyncMock(return_value=resp)))
        ctx.__aexit__ = mock.AsyncMock(return_value=False)
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(Exception, match="HTTP 500"):
                await _plugin().write(uuid.uuid4(), 1.0, None, "ok", ts=_ts())

    async def test_write_without_ts_uses_now(self):
        ctx, client = _make_httpx_mock(status=204)
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            await _plugin().write(uuid.uuid4(), 1.0, None, "ok")
        client.post.assert_awaited_once()


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


class TestInfluxDBQuery:
    def _query_response(self):
        return {
            "results": [
                {
                    "series": [
                        {
                            "columns": ["time", "raw", "quality", "unit"],
                            "values": [["2024-01-01T00:00:00Z", "7.5", "ok", "V"]],
                        }
                    ]
                }
            ]
        }

    async def test_query_returns_rows(self):
        ctx, _ = _make_httpx_mock(status=200, json_body=self._query_response())
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            result = await _plugin().query(uuid.uuid4(), _ts(0), _ts(1))
        assert len(result) == 1
        assert result[0]["v"] == pytest.approx(7.5)

    async def test_query_http_error_returns_empty(self):
        resp = mock.MagicMock()
        resp.raise_for_status = mock.MagicMock(side_effect=Exception("500"))
        ctx = mock.MagicMock()
        ctx.__aenter__ = mock.AsyncMock(return_value=mock.MagicMock(get=mock.AsyncMock(return_value=resp)))
        ctx.__aexit__ = mock.AsyncMock(return_value=False)
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            result = await _plugin().query(uuid.uuid4(), _ts(0), _ts(1))
        assert result == []


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


class TestInfluxDBAggregate:
    def _agg_response(self):
        return {
            "results": [
                {
                    "series": [
                        {
                            "columns": ["time", "mean", "count"],
                            "values": [["2024-01-01T00:00:00Z", 10.0, 4]],
                        }
                    ]
                }
            ]
        }

    async def test_aggregate_avg(self):
        ctx, _ = _make_httpx_mock(status=200, json_body=self._agg_response())
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            result = await _plugin().aggregate(uuid.uuid4(), "avg", "1h", _ts(0), _ts(1))
        assert len(result) == 1
        assert result[0]["v"] == pytest.approx(10.0)
        assert result[0]["n"] == 4

    async def test_aggregate_unknown_interval_falls_back_to_1h(self):
        ctx, client = _make_httpx_mock(status=200, json_body=self._agg_response())
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            await _plugin().aggregate(uuid.uuid4(), "avg", "bad_interval", _ts(0), _ts(1))
        q = client.get.call_args.kwargs.get("params", {}).get("q", "")
        assert "GROUP BY time(1h)" in q

    async def test_aggregate_http_error_returns_empty(self):
        resp = mock.MagicMock()
        resp.raise_for_status = mock.MagicMock(side_effect=Exception("500"))
        ctx = mock.MagicMock()
        ctx.__aenter__ = mock.AsyncMock(return_value=mock.MagicMock(get=mock.AsyncMock(return_value=resp)))
        ctx.__aexit__ = mock.AsyncMock(return_value=False)
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            result = await _plugin().aggregate(uuid.uuid4(), "avg", "1h", _ts(0), _ts(1))
        assert result == []

    async def test_aggregate_all_fns(self):
        for fn in ("avg", "min", "max", "last"):
            ctx, client = _make_httpx_mock(status=200, json_body=self._agg_response())
            with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
                result = await _plugin().aggregate(uuid.uuid4(), fn, "1h", _ts(0), _ts(1))
            assert isinstance(result, list)


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


class TestInfluxDBPing:
    async def test_ping_v1_success(self):
        ctx, _ = _make_httpx_mock(status=204)
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            assert await _plugin(1).ping() is True

    async def test_ping_v1_failure(self):
        ctx, _ = _make_httpx_mock(status=500)
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            assert await _plugin(1).ping() is False

    async def test_ping_v2_success(self):
        ctx, _ = _make_httpx_mock(status=200)
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            assert await _plugin(2).ping() is True

    async def test_ping_v3_success(self):
        ctx, _ = _make_httpx_mock(status=200, json_body={"results": []})
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            assert await _plugin(3).ping() is True

    async def test_ping_exception_returns_false(self):
        ctx = mock.MagicMock()
        ctx.__aenter__ = mock.AsyncMock(side_effect=Exception("connection refused"))
        ctx.__aexit__ = mock.AsyncMock(return_value=False)
        with mock.patch("obs.history.influxdb_plugin.httpx.AsyncClient", return_value=ctx):
            assert await _plugin(1).ping() is False
