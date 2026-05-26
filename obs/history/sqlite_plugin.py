"""SQLite History Plugin — Phase 5

Speichert Werte in der Hauptdatenbank (history_values Tabelle, Migration V3).
Aggregation über SQLite strftime-Groupierung.

Unterstützte Intervalle: 1m | 5m | 15m | 30m | 1h | 6h | 12h | 1d
Unterstützte Funktionen: avg | min | max | last
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from obs.history.base import HistoryPlugin

logger = logging.getLogger(__name__)

# Interval → (truncation_fmt, minutes)
_INTERVALS: dict[str, tuple[str, int]] = {
    "1m": ("%Y-%m-%dT%H:%M:00", 1),
    "5m": ("%Y-%m-%dT%H:%M:00", 5),  # rounded in Python
    "15m": ("%Y-%m-%dT%H:%M:00", 15),
    "30m": ("%Y-%m-%dT%H:%M:00", 30),
    "1h": ("%Y-%m-%dT%H:00:00", 60),
    "6h": ("%Y-%m-%dT%H:00:00", 360),
    "12h": ("%Y-%m-%dT%H:00:00", 720),
    "1d": ("%Y-%m-%dT00:00:00", 1440),
}


class SQLiteHistoryPlugin(HistoryPlugin):
    """History stored in the main SQLite DB (history_values table)."""

    def __init__(self, db: Any) -> None:
        from obs.db.database import Database

        self._db: Database = db

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
        ts_str = (ts or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        await self._db.execute_and_commit(
            """INSERT INTO history_values (datapoint_id, value, unit, quality, ts, source_adapter)
               VALUES (?,?,?,?,?,?)""",
            (
                str(datapoint_id),
                json.dumps(value),
                unit,
                quality,
                ts_str,
                source_adapter,
            ),
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query(
        self,
        datapoint_id: uuid.UUID,
        from_ts: datetime,
        to_ts: datetime,
        limit: int = 10000,
    ) -> list[dict]:
        rows = await self._db.fetchall(
            """SELECT ts, value, unit, quality, source_adapter
               FROM history_values
               WHERE datapoint_id=? AND ts >= ? AND ts <= ?
               ORDER BY ts DESC
               LIMIT ?""",
            (
                str(datapoint_id),
                _to_sqlite_ts(from_ts),
                _to_sqlite_ts(to_ts),
                limit,
            ),
        )
        return [
            {
                "ts": r["ts"],
                "v": _safe_loads(r["value"]),
                "u": r["unit"],
                "q": r["quality"],
                "a": r["source_adapter"],
            }
            for r in rows
        ]

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
        if interval not in _INTERVALS:
            interval = "1h"

        fmt, minutes = _INTERVALS[interval]

        if fn == "last":
            return await self._aggregate_last(datapoint_id, fmt, minutes, from_ts, to_ts)

        sql_fn = {"avg": "AVG", "min": "MIN", "max": "MAX"}.get(fn, "AVG")

        if minutes >= 60:
            # SQLite can truncate to hour or day
            sql_fmt = fmt
            rows = await self._db.fetchall(
                f"""SELECT
                       strftime('{sql_fmt}', ts) AS bucket,
                       {sql_fn}(CAST(value AS REAL)) AS v
                    FROM history_values
                    WHERE datapoint_id=? AND ts >= ? AND ts <= ?
                    GROUP BY bucket
                    ORDER BY bucket""",
                (str(datapoint_id), _to_sqlite_ts(from_ts), _to_sqlite_ts(to_ts)),
            )
            return [{"bucket": r["bucket"], "v": r["v"]} for r in rows]
        # Sub-hourly: fetch raw, group in Python
        rows = await self._db.fetchall(
            """SELECT ts, value FROM history_values
                   WHERE datapoint_id=? AND ts >= ? AND ts <= ?
                   ORDER BY ts""",
            (str(datapoint_id), _to_sqlite_ts(from_ts), _to_sqlite_ts(to_ts)),
        )
        return _aggregate_python(rows, fn, minutes)

    async def _aggregate_last(
        self,
        datapoint_id: uuid.UUID,
        fmt: str,
        minutes: int,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict]:
        """Return the last value in each bucket."""
        if minutes >= 60:
            rows = await self._db.fetchall(
                f"""SELECT strftime('{fmt}', ts) AS bucket, value
                    FROM history_values
                    WHERE datapoint_id=? AND ts >= ? AND ts <= ?
                    GROUP BY bucket
                    HAVING ts = MAX(ts)
                    ORDER BY bucket""",
                (str(datapoint_id), _to_sqlite_ts(from_ts), _to_sqlite_ts(to_ts)),
            )
        else:
            raw = await self._db.fetchall(
                """SELECT ts, value FROM history_values
                   WHERE datapoint_id=? AND ts >= ? AND ts <= ?
                   ORDER BY ts""",
                (str(datapoint_id), _to_sqlite_ts(from_ts), _to_sqlite_ts(to_ts)),
            )
            return _aggregate_python(raw, "last", minutes)
        return [{"bucket": r["bucket"], "v": _safe_loads(r["value"])} for r in rows]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def delete_before(self, datapoint_id: uuid.UUID, before_ts: datetime) -> int:
        """Delete history older than *before_ts*. Returns deleted row count."""
        cur = await self._db.execute(
            "DELETE FROM history_values WHERE datapoint_id=? AND ts < ?",
            (str(datapoint_id), _to_sqlite_ts(before_ts)),
        )
        await self._db.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_sqlite_ts(dt: datetime) -> str:
    """Format a datetime to the same Z-suffix format used when storing rows.

    SQLite compares timestamps as strings, so both sides of WHERE clauses must
    use the identical format — otherwise 'Z' (ASCII 90) sorts higher than any
    digit or '+', causing boundary rows to be silently excluded.
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _safe_loads(s: str | None) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


def _bucket_key(ts_str: str, minutes: int) -> str:
    """Round ts_str down to the nearest *minutes* bucket."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        total_minutes = dt.hour * 60 + dt.minute
        rounded = (total_minutes // minutes) * minutes
        dt_rounded = dt.replace(hour=rounded // 60, minute=rounded % 60, second=0, microsecond=0)
        return dt_rounded.strftime("%Y-%m-%dT%H:%M:00")
    except Exception:
        return ts_str[:16]


def _aggregate_python(rows: list, fn: str, minutes: int) -> list[dict]:
    """Group raw rows into buckets of *minutes* and apply *fn*."""
    buckets: dict[str, list[float]] = {}
    bucket_last: dict[str, Any] = {}

    for row in rows:
        ts = row["ts"] if isinstance(row, dict) else row[0]
        val_raw = row["value"] if isinstance(row, dict) else row[1]
        try:
            val = float(_safe_loads(val_raw))
        except (TypeError, ValueError):
            continue
        bucket = _bucket_key(ts, minutes)
        buckets.setdefault(bucket, []).append(val)
        bucket_last[bucket] = val

    if fn == "last":
        return [{"bucket": b, "v": bucket_last[b]} for b in sorted(bucket_last)]
    if fn == "avg":
        return [{"bucket": b, "v": sum(vs) / len(vs)} for b, vs in sorted(buckets.items())]
    if fn == "min":
        return [{"bucket": b, "v": min(vs)} for b, vs in sorted(buckets.items())]
    if fn == "max":
        return [{"bucket": b, "v": max(vs)} for b, vs in sorted(buckets.items())]
    return []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_plugin: SQLiteHistoryPlugin | None = None


def get_history_plugin() -> SQLiteHistoryPlugin:
    if _plugin is None:
        raise RuntimeError("History plugin not initialized")
    return _plugin


def init_history_plugin(db: Any) -> SQLiteHistoryPlugin:
    global _plugin
    _plugin = SQLiteHistoryPlugin(db)
    return _plugin
