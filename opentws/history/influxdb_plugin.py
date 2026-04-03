"""
InfluxDB History Plugin — Phase 5

Supports InfluxDB v1, v2, and v3.

Write uses the InfluxDB line protocol for all versions.
Query uses InfluxQL for all versions:
  v1 — basic auth (username/password) at /query?db=<database>
  v2 — Token auth, InfluxQL compat at /query?db=<bucket>
  v3 — Bearer token auth, InfluxQL compat at /query?db=<database>

Configuration keys (from app_settings):
  history.influx_url         e.g. "http://localhost:8086"
  history.influx_version     1 | 2 | 3  (default: 2)
  history.influx_token       Token/password for v2/v3
  history.influx_org         Organisation for v2 write
  history.influx_bucket      Bucket for v2 (used as db for queries too)
  history.influx_database    Database name for v1 / v3
  history.influx_username    Username for v1
  history.influx_password    Password for v1

Line protocol schema:
  opentws,dp_id=<uuid> raw="<json>",quality="<q>",unit="<u>"[,v=<float>] <timestamp_ns>
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from opentws.history.base import HistoryPlugin

logger = logging.getLogger(__name__)

# Map interval strings to InfluxQL duration literals
_INFLUX_INTERVALS: dict[str, str] = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "6h":  "6h",
    "12h": "12h",
    "1d":  "1d",
}

_INFLUX_AGGFN: dict[str, str] = {
    "avg":  "MEAN",
    "min":  "MIN",
    "max":  "MAX",
    "last": "LAST",
}


class InfluxDBHistoryPlugin(HistoryPlugin):
    """History stored in InfluxDB (v1, v2, or v3)."""

    def __init__(
        self,
        url: str,
        version: int,
        token: str,
        org: str,
        bucket: str,
        database: str,
        username: str,
        password: str,
    ) -> None:
        self._url = url.rstrip("/")
        self._version = int(version)
        self._token = token
        self._org = org
        self._bucket = bucket        # v2 write target / query db alias
        self._database = database    # v1/v3 db name
        self._username = username
        self._password = password

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_url_params(self) -> tuple[str, dict]:
        """Return (url, query_params) for the line-protocol write endpoint."""
        if self._version == 1:
            return (
                f"{self._url}/write",
                {"db": self._database, "precision": "ns"},
            )
        elif self._version == 2:
            return (
                f"{self._url}/api/v2/write",
                {"org": self._org, "bucket": self._bucket, "precision": "ns"},
            )
        else:  # v3
            return (
                f"{self._url}/api/v3/write_lp",
                {"database": self._database, "precision": "nanosecond"},
            )

    def _query_url_params(self) -> tuple[str, dict]:
        """Return (url, base_query_params) for InfluxQL queries."""
        if self._version == 1:
            return f"{self._url}/query", {"db": self._database}
        elif self._version == 2:
            # v2 InfluxQL compat: db = bucket name
            return f"{self._url}/query", {"db": self._bucket}
        else:  # v3
            return f"{self._url}/query", {"db": self._database}

    def _headers(self, content_type: str = "application/json") -> dict:
        h: dict[str, str] = {"Accept": "application/json"}
        if content_type:
            h["Content-Type"] = content_type
        if self._version == 1:
            if self._username:
                import base64
                cred = base64.b64encode(
                    f"{self._username}:{self._password}".encode()
                ).decode()
                h["Authorization"] = f"Basic {cred}"
        elif self._version == 2:
            if self._token:
                h["Authorization"] = f"Token {self._token}"
        else:  # v3
            if self._token:
                h["Authorization"] = f"Bearer {self._token}"
        return h

    def _escape_tag(self, s: str) -> str:
        """Escape InfluxDB line-protocol tag value (no comma, space, equals)."""
        return s.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")

    def _escape_field_str(self, s: str) -> str:
        """Escape InfluxDB string field value (escape backslash and quote)."""
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def _build_line(
        self,
        datapoint_id: uuid.UUID,
        value: Any,
        unit: str | None,
        quality: str,
        ts: datetime,
    ) -> str:
        ts_ns = int(ts.timestamp() * 1_000_000_000)

        dp_tag = self._escape_tag(str(datapoint_id))
        raw_str = self._escape_field_str(json.dumps(value))
        unit_str = self._escape_field_str(unit or "")
        quality_str = self._escape_field_str(quality)

        fields = [
            f'raw="{raw_str}"',
            f'quality="{quality_str}"',
            f'unit="{unit_str}"',
        ]
        try:
            v_float = float(value)
            if v_float == v_float:  # not NaN
                fields.append(f"v={v_float}")
        except (TypeError, ValueError):
            pass

        return f"opentws,dp_id={dp_tag} {','.join(fields)} {ts_ns}"

    def _parse_influxql_series(
        self, data: dict, raw_field: bool = True
    ) -> list[dict]:
        """Parse the standard InfluxQL JSON response into our list-of-dicts format."""
        results = data.get("results", [])
        if not results:
            return []
        series = results[0].get("series", [])
        if not series:
            return []

        columns = series[0]["columns"]  # ["time", "raw"/"mean", "quality", "unit"]
        rows = series[0].get("values", [])

        col_idx = {c: i for i, c in enumerate(columns)}
        out = []
        for row in rows:
            ts = row[col_idx["time"]]
            if raw_field:
                raw = row[col_idx.get("raw", 0)] if "raw" in col_idx else None
                try:
                    v = json.loads(raw) if raw is not None else None
                except Exception:
                    v = raw
                q = row[col_idx.get("quality", 0)] if "quality" in col_idx else ""
                u = row[col_idx.get("unit", 0)] if "unit" in col_idx else None
                out.append({"ts": ts, "v": v, "u": u or None, "q": q or ""})
            else:
                # Aggregate row: time + aggregated value
                val_col = [c for c in columns if c != "time"][0] if len(columns) > 1 else "mean"
                v = row[col_idx.get(val_col, 1)] if val_col in col_idx else None
                out.append({"bucket": ts, "v": v})
        return out

    async def _run_influxql(self, q: str) -> dict:
        """Execute an InfluxQL query and return parsed JSON."""
        url, params = self._query_url_params()
        params = {**params, "q": q}
        headers = self._headers(content_type="")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # HistoryPlugin interface
    # ------------------------------------------------------------------

    async def write(
        self,
        datapoint_id: uuid.UUID,
        value: Any,
        unit: str | None,
        quality: str,
        ts: datetime | None = None,
        source_adapter: str | None = None,
    ) -> None:
        ts_dt = ts or datetime.now(timezone.utc)
        line = self._build_line(datapoint_id, value, unit, quality, ts_dt)

        url, params = self._write_url_params()
        headers = self._headers(content_type="text/plain; charset=utf-8")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    url,
                    content=line.encode(),
                    params=params,
                    headers=headers,
                )
                r.raise_for_status()
        except Exception as exc:
            logger.error("InfluxDB write failed: %s", exc)
            raise

    async def query(
        self,
        datapoint_id: uuid.UUID,
        from_ts: datetime,
        to_ts: datetime,
        limit: int = 1000,
    ) -> list[dict]:
        from_rfc = _to_rfc3339(from_ts)
        to_rfc = _to_rfc3339(to_ts)
        dp = str(datapoint_id)

        q = (
            f"SELECT raw, quality, unit FROM \"opentws\" "
            f"WHERE dp_id='{dp}' "
            f"AND time >= '{from_rfc}' AND time <= '{to_rfc}' "
            f"ORDER BY time DESC "
            f"LIMIT {limit}"
        )
        try:
            data = await self._run_influxql(q)
            return self._parse_influxql_series(data, raw_field=True)
        except Exception as exc:
            logger.error("InfluxDB query failed: %s", exc)
            return []

    async def aggregate(
        self,
        datapoint_id: uuid.UUID,
        fn: str,
        interval: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict]:
        influx_fn = _INFLUX_AGGFN.get(fn, "MEAN")
        influx_interval = _INFLUX_INTERVALS.get(interval, "1h")
        from_rfc = _to_rfc3339(from_ts)
        to_rfc = _to_rfc3339(to_ts)
        dp = str(datapoint_id)

        q = (
            f"SELECT {influx_fn}(v) FROM \"opentws\" "
            f"WHERE dp_id='{dp}' "
            f"AND time >= '{from_rfc}' AND time <= '{to_rfc}' "
            f"GROUP BY time({influx_interval}) fill(none)"
        )
        try:
            data = await self._run_influxql(q)
            return self._parse_influxql_series(data, raw_field=False)
        except Exception as exc:
            logger.error("InfluxDB aggregate failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Return True if the InfluxDB instance is reachable and authenticated."""
        try:
            if self._version == 1:
                url = f"{self._url}/ping"
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(url)
                    return r.status_code < 300
            elif self._version == 2:
                url = f"{self._url}/api/v2/ping"
                headers = self._headers(content_type="")
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(url, headers=headers)
                    return r.status_code < 300
            else:  # v3
                # Try a trivial InfluxQL query against the database
                data = await self._run_influxql(
                    f"SHOW MEASUREMENTS ON \"{self._database}\" LIMIT 1"
                )
                return "results" in data
        except Exception as exc:
            logger.debug("InfluxDB ping failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_rfc3339(dt: datetime) -> str:
    """Convert datetime to RFC3339/ISO8601 with Z suffix."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
