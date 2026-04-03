"""
TimescaleDB History Plugin — Phase 5

Uses asyncpg for async PostgreSQL / TimescaleDB access.

Optional dependency: asyncpg>=0.29.0

Schema (auto-created on first connect):
  CREATE TABLE IF NOT EXISTS dp_history (
      time     TIMESTAMPTZ NOT NULL,
      dp_id    TEXT        NOT NULL,
      v        DOUBLE PRECISION,
      raw      TEXT,
      unit     TEXT,
      quality  TEXT NOT NULL DEFAULT ''
  );
  -- TimescaleDB hypertable (skipped gracefully if extension not installed):
  SELECT create_hypertable('dp_history', 'time', if_not_exists => TRUE);
  CREATE INDEX IF NOT EXISTS dp_history_dp_id_time ON dp_history (dp_id, time DESC);

Configuration keys (from app_settings):
  history.timescale_dsn   e.g. "postgresql://user:pass@localhost:5432/opentws"
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from opentws.history.base import HistoryPlugin

logger = logging.getLogger(__name__)

# Map interval strings → (time_bucket_interval, date_trunc_unit)
_INTERVALS: dict[str, tuple[str, str | None]] = {
    "1m":  ("1 minute",  None),
    "5m":  ("5 minutes", None),
    "15m": ("15 minutes", None),
    "30m": ("30 minutes", None),
    "1h":  ("1 hour",    "hour"),
    "6h":  ("6 hours",   None),
    "12h": ("12 hours",  None),
    "1d":  ("1 day",     "day"),
}

_AGGFN: dict[str, str] = {
    "avg":  "AVG",
    "min":  "MIN",
    "max":  "MAX",
    "last": "LAST",
}


class TimescaleDBHistoryPlugin(HistoryPlugin):
    """History stored in PostgreSQL / TimescaleDB."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None
        self._has_timescaledb: bool | None = None  # detected on first use

    async def connect(self) -> None:
        """Create the connection pool and ensure schema exists."""
        try:
            import asyncpg
        except ImportError:
            raise RuntimeError(
                "asyncpg is required for the TimescaleDB history plugin. "
                "Install it with: pip install asyncpg"
            )

        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        await self._ensure_schema()

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------

    async def _ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS dp_history (
                    time    TIMESTAMPTZ     NOT NULL,
                    dp_id   TEXT            NOT NULL,
                    v       DOUBLE PRECISION,
                    raw     TEXT,
                    unit    TEXT,
                    quality TEXT            NOT NULL DEFAULT ''
                )
            """)

            # Try to create TimescaleDB hypertable (silently skip if not available)
            try:
                await conn.execute(
                    "SELECT create_hypertable('dp_history', 'time', if_not_exists => TRUE)"
                )
                self._has_timescaledb = True
                logger.info("TimescaleDB hypertable active for dp_history")
            except Exception:
                self._has_timescaledb = False
                logger.info(
                    "TimescaleDB extension not available — using plain PostgreSQL for history"
                )

            await conn.execute(
                "CREATE INDEX IF NOT EXISTS dp_history_dp_id_time "
                "ON dp_history (dp_id, time DESC)"
            )

    def _require_pool(self):
        if self._pool is None:
            raise RuntimeError(
                "TimescaleDB plugin not connected. Call connect() first."
            )

    # ------------------------------------------------------------------
    # Write
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
        self._require_pool()
        ts_dt = ts or datetime.now(timezone.utc)

        try:
            v_float = float(value)
            if v_float != v_float:  # NaN
                v_float = None
        except (TypeError, ValueError):
            v_float = None

        raw_str = json.dumps(value)

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO dp_history (time, dp_id, v, raw, unit, quality)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                ts_dt, str(datapoint_id), v_float, raw_str, unit, quality,
            )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query(
        self,
        datapoint_id: uuid.UUID,
        from_ts: datetime,
        to_ts: datetime,
        limit: int = 1000,
    ) -> list[dict]:
        self._require_pool()

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT time, raw, unit, quality
                FROM dp_history
                WHERE dp_id = $1 AND time >= $2 AND time <= $3
                ORDER BY time DESC
                LIMIT $4
                """,
                str(datapoint_id), from_ts, to_ts, limit,
            )

        result = []
        for r in rows:
            try:
                v = json.loads(r["raw"]) if r["raw"] is not None else None
            except Exception:
                v = r["raw"]
            result.append({
                "ts": r["time"].isoformat(),
                "v": v,
                "u": r["unit"],
                "q": r["quality"] or "",
            })
        return result

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    async def aggregate(
        self,
        datapoint_id: uuid.UUID,
        fn: str,
        interval: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict]:
        self._require_pool()

        if interval not in _INTERVALS:
            interval = "1h"

        bucket_str, _ = _INTERVALS[interval]

        if fn == "last":
            return await self._aggregate_last(datapoint_id, bucket_str, from_ts, to_ts)

        pg_fn = _AGGFN.get(fn, "AVG")

        if self._has_timescaledb:
            bucket_expr = f"time_bucket('{bucket_str}', time)"
        else:
            # Fallback: use date_trunc or arithmetic rounding
            bucket_expr = _pg_bucket_expr(bucket_str)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {bucket_expr} AS bucket, {pg_fn}(v) AS v
                FROM dp_history
                WHERE dp_id = $1 AND time >= $2 AND time <= $3 AND v IS NOT NULL
                GROUP BY bucket
                ORDER BY bucket
                """,
                str(datapoint_id), from_ts, to_ts,
            )

        return [
            {"bucket": r["bucket"].isoformat() if hasattr(r["bucket"], "isoformat") else str(r["bucket"]), "v": r["v"]}
            for r in rows
        ]

    async def _aggregate_last(
        self,
        datapoint_id: uuid.UUID,
        bucket_str: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict]:
        if self._has_timescaledb:
            bucket_expr = f"time_bucket('{bucket_str}', time)"
            # TimescaleDB last() function
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT {bucket_expr} AS bucket, last(v, time) AS v
                    FROM dp_history
                    WHERE dp_id = $1 AND time >= $2 AND time <= $3 AND v IS NOT NULL
                    GROUP BY bucket
                    ORDER BY bucket
                    """,
                    str(datapoint_id), from_ts, to_ts,
                )
        else:
            bucket_expr = _pg_bucket_expr(bucket_str)
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT DISTINCT ON ({bucket_expr})
                        {bucket_expr} AS bucket, v
                    FROM dp_history
                    WHERE dp_id = $1 AND time >= $2 AND time <= $3 AND v IS NOT NULL
                    ORDER BY {bucket_expr}, time DESC
                    """,
                    str(datapoint_id), from_ts, to_ts,
                )

        return [
            {"bucket": r["bucket"].isoformat() if hasattr(r["bucket"], "isoformat") else str(r["bucket"]), "v": r["v"]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Return True if the database is reachable."""
        try:
            import asyncpg
            conn = await asyncpg.connect(self._dsn, timeout=5)
            await conn.fetchval("SELECT 1")
            await conn.close()
            return True
        except ImportError:
            raise RuntimeError(
                "asyncpg is required for the TimescaleDB history plugin. "
                "Install it with: pip install asyncpg"
            )
        except Exception as exc:
            logger.debug("TimescaleDB ping failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pg_bucket_expr(bucket_str: str) -> str:
    """Build a PostgreSQL expression to truncate time to the given bucket.

    Works without TimescaleDB's time_bucket() function.
    Uses date_trunc for clean intervals, arithmetic for others.
    """
    mapping = {
        "1 hour":    "date_trunc('hour', time)",
        "1 day":     "date_trunc('day', time)",
        "1 minute":  "date_trunc('minute', time)",
        # Sub-hour buckets: round down via epoch arithmetic
        "5 minutes":  "to_timestamp(floor(extract(epoch from time) / 300)  * 300)  AT TIME ZONE 'UTC'",
        "15 minutes": "to_timestamp(floor(extract(epoch from time) / 900)  * 900)  AT TIME ZONE 'UTC'",
        "30 minutes": "to_timestamp(floor(extract(epoch from time) / 1800) * 1800) AT TIME ZONE 'UTC'",
        "6 hours":    "to_timestamp(floor(extract(epoch from time) / 21600) * 21600) AT TIME ZONE 'UTC'",
        "12 hours":   "to_timestamp(floor(extract(epoch from time) / 43200) * 43200) AT TIME ZONE 'UTC'",
    }
    return mapping.get(bucket_str, "date_trunc('hour', time)")
