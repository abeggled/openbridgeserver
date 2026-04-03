"""
History Plugin Factory — Phase 5

Reads configuration from the app_settings DB table and creates the
appropriate HistoryPlugin instance.

Settings keys:
  history.plugin          "sqlite" | "influxdb" | "timescaledb"  (default: sqlite)

  -- InfluxDB --
  history.influx_url       URL incl. port, e.g. "http://localhost:8086"
  history.influx_version   "1" | "2" | "3"                       (default: 2)
  history.influx_token     API token (v2/v3) or password (v1 alt.)
  history.influx_org       Organisation name (v2 required)
  history.influx_bucket    Bucket name (v2)
  history.influx_database  Database name (v1/v3)
  history.influx_username  Username (v1)
  history.influx_password  Password (v1)

  -- TimescaleDB / PostgreSQL --
  history.timescale_dsn    DSN, e.g. "postgresql://user:pass@host:5432/db"
"""
from __future__ import annotations

import logging
from typing import Any

from opentws.history.base import HistoryPlugin

logger = logging.getLogger(__name__)

_plugin: HistoryPlugin | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_history_plugin() -> HistoryPlugin:
    """Return the active history plugin. Raises RuntimeError if not initialized."""
    if _plugin is None:
        raise RuntimeError("History plugin not initialized")
    return _plugin


async def handle_value_event(event: Any) -> None:
    """EventBus handler — called for every DataValueEvent; writes to the active plugin."""
    if _plugin is None:
        return
    try:
        # Resolve unit from the DataPoint registry (not carried on the event)
        unit: str | None = None
        try:
            from opentws.core.registry import get_registry
            dp = get_registry().get(event.datapoint_id)
            if dp:
                unit = dp.unit
        except RuntimeError:
            pass

        await _plugin.write(
            datapoint_id=event.datapoint_id,
            value=event.value,
            unit=unit,
            quality=event.quality,
            ts=event.ts,
            source_adapter=event.source_adapter,
        )
    except Exception as exc:
        logger.error("History write failed for %s: %s", event.datapoint_id, exc)


async def init_history_plugin(db: Any) -> HistoryPlugin:
    """
    Read settings from DB and initialize the appropriate HistoryPlugin.
    Called once at startup from main.py lifespan.
    """
    global _plugin

    cfg = await _load_history_settings(db)
    plugin_type = cfg.get("plugin", "sqlite")

    logger.info("History plugin: %s", plugin_type)

    if plugin_type == "influxdb":
        _plugin = _create_influxdb_plugin(cfg)
    elif plugin_type == "timescaledb":
        _plugin = await _create_timescaledb_plugin(cfg)
    else:
        # Default: SQLite (uses existing main DB)
        from opentws.history.sqlite_plugin import SQLiteHistoryPlugin
        _plugin = SQLiteHistoryPlugin(db)

    return _plugin


async def reload_history_plugin(db: Any) -> HistoryPlugin:
    """
    Recreate the history plugin from current DB settings.
    Called after the user changes history settings through the GUI.
    """
    global _plugin

    # Disconnect old plugin if it supports it
    if _plugin is not None and hasattr(_plugin, "disconnect"):
        try:
            await _plugin.disconnect()
        except Exception as exc:
            logger.warning("Error disconnecting old history plugin: %s", exc)

    _plugin = None
    return await init_history_plugin(db)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _load_history_settings(db: Any) -> dict[str, str]:
    """Load all history.* keys from app_settings into a plain dict."""
    prefix = "history."
    rows = await db.fetchall(
        "SELECT key, value FROM app_settings WHERE key LIKE 'history.%'"
    )
    cfg: dict[str, str] = {}
    for r in rows:
        short_key = r["key"][len(prefix):]
        cfg[short_key] = r["value"] or ""
    return cfg


def _create_influxdb_plugin(cfg: dict[str, str]) -> HistoryPlugin:
    from opentws.history.influxdb_plugin import InfluxDBHistoryPlugin

    return InfluxDBHistoryPlugin(
        url=cfg.get("influx_url", "http://localhost:8086"),
        version=int(cfg.get("influx_version", "2")),
        token=cfg.get("influx_token", ""),
        org=cfg.get("influx_org", ""),
        bucket=cfg.get("influx_bucket", "opentws"),
        database=cfg.get("influx_database", "opentws"),
        username=cfg.get("influx_username", ""),
        password=cfg.get("influx_password", ""),
    )


async def _create_timescaledb_plugin(cfg: dict[str, str]) -> HistoryPlugin:
    from opentws.history.timescaledb_plugin import TimescaleDBHistoryPlugin

    dsn = cfg.get("timescale_dsn", "")
    if not dsn:
        raise ValueError(
            "history.timescale_dsn is required for the timescaledb plugin"
        )

    plugin = TimescaleDBHistoryPlugin(dsn=dsn)
    await plugin.connect()
    return plugin
