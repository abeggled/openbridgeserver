from __future__ import annotations

from obs.history.influxdb_plugin import InfluxDBHistoryPlugin


def _plugin(version: int) -> InfluxDBHistoryPlugin:
    return InfluxDBHistoryPlugin(
        url="http://localhost:8181",
        version=version,
        token="token",
        org="org",
        bucket="bucket",
        database="obs",
        username="user",
        password="pass",
    )


def test_write_url_params_v1_uses_db_param():
    url, params = _plugin(1)._write_url_params()
    assert url.endswith("/write")
    assert params == {"db": "obs", "precision": "ns"}


def test_write_url_params_v2_uses_org_and_bucket():
    url, params = _plugin(2)._write_url_params()
    assert url.endswith("/api/v2/write")
    assert params == {"org": "org", "bucket": "bucket", "precision": "ns"}


def test_write_url_params_v3_uses_db_param():
    url, params = _plugin(3)._write_url_params()
    assert url.endswith("/api/v3/write_lp")
    assert params == {"db": "obs", "precision": "nanosecond"}
