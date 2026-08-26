---
title: History DB
---

# History DB

The History DB tab lives in the Admin GUI under **Settings → History DB**.

## Database backend {#settings-history-db}

Chooses where historical values are stored: **SQLite** (internal, default, no external
database needed), **InfluxDB** (v1, v2, or v3 — depending on the version, URL, credentials,
and database/bucket/organization are requested), or **PostgreSQL/TimescaleDB** (via a
connection DSN). "Test connection" checks the configuration without applying it; "Save &
activate" applies it **immediately, with no restart**.

The "default window" only applies to History API calls that don't supply an explicit `from`
parameter.

## Data point filter {#settings-history-filter}

Sets, per data point, whether its values are stored in the history DB at all. Data points
with history disabled are excluded from recording — typical candidates are time, date, or
system values with no historical relevance. "Enable all" / "Disable all" act on the currently
filtered/searched list.
