#!/usr/bin/env python3
"""Index-Audit für Issue #919 / #935 — Evidenzskript.

Führt die REALEN Query-Muster aus der API/Query-Engine gegen mit Testdaten
befüllte SQLite-Datenbanken aus und erfasst `EXPLAIN QUERY PLAN` jeweils
VORHER (nur Bestandsindexe) und NACHHER (Kandidatenindex zusätzlich).

Zweck: belegen, welche Kandidatenindexe einen konkreten Query-Pfad von einem
Full-Table-Scan / temporären Sort auf eine Index-Suche verbessern — und welche
keinen messbaren Nutzen bringen (Duplikat / nicht genutzter Pfad).

Nutzung (aus dem Repo-Root):
    PYTHONPATH=. tools/with-venv python tools/index-audit-919.py

Das Skript schreibt reine Textausgabe nach stdout und legt keine Dateien an.
Es ist reines Dev-Tooling (Ordner tools/), kein Runtime-Skript.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

# --- Testdatenmengen (klein genug für Sekunden, groß genug, dass der Planner
#     Indexe wählt statt Full-Scan-Heuristik) --------------------------------
N_RB_ROWS = 20000
N_HIST_ROWS = 20000
N_DP = 200
N_INSTANCES = 12
N_BINDINGS = 4000


def _plan(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> str:
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return "\n".join(f"    {r[3]}" for r in rows)


# ---------------------------------------------------------------------------
# RingBufferDB
# ---------------------------------------------------------------------------

_RB_SCHEMA = """
CREATE TABLE ringbuffer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    datapoint_id   TEXT    NOT NULL,
    topic          TEXT    NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    source_adapter TEXT    NOT NULL,
    quality        TEXT    NOT NULL,
    metadata_version INTEGER NOT NULL DEFAULT 1,
    metadata       TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_rb_ts  ON ringbuffer(ts);
CREATE INDEX idx_rb_dp  ON ringbuffer(datapoint_id);
CREATE INDEX idx_rb_adp ON ringbuffer(source_adapter);
"""


def _populate_ringbuffer(conn: sqlite3.Connection) -> None:
    conn.executescript(_RB_SCHEMA)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    adapters = ["KNX", "MODBUS_TCP", "MQTT", "HOME_ASSISTANT"]
    qualities = ["GOOD", "GOOD", "GOOD", "BAD", "UNCERTAIN"]
    rows = []
    for i in range(N_RB_ROWS):
        ts = (base + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        rows.append(
            (
                ts,
                f"dp-{i % N_DP:04d}",
                f"dp/{i % N_DP}/value",
                None,
                str(i),
                adapters[i % len(adapters)],
                qualities[i % len(qualities)],
            )
        )
    conn.executemany(
        """INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality)
           VALUES (?,?,?,?,?,?,?)""",
        rows,
    )
    conn.execute("ANALYZE")
    conn.commit()


def audit_ringbuffer() -> None:
    print("=" * 78)
    print("RingBufferDB — query_v2 Pfade (obs/ringbuffer/ringbuffer.py)")
    print("=" * 78)

    # Reale query_v2-SQL-Fragmente (aus dem Code rekonstruiert).
    cases = [
        (
            "A1) EIN datapoint_id + Sort ts DESC (Einzel-DP-Filter, häufigster Fall)",
            "SELECT * FROM ringbuffer WHERE 1=1 AND datapoint_id IN (?) ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            ("dp-0001", 100, 0),
            "CREATE INDEX idx_rb_dp_ts_id ON ringbuffer(datapoint_id, ts DESC, id DESC)",
        ),
        (
            "A2) MEHRERE datapoint_ids (IN-Liste) + Sort ts DESC",
            "SELECT * FROM ringbuffer WHERE 1=1 AND datapoint_id IN (?,?) ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            ("dp-0001", "dp-0002", 100, 0),
            "CREATE INDEX idx_rb_dp_ts_id ON ringbuffer(datapoint_id, ts DESC, id DESC)",
        ),
        (
            "B) EIN source_adapter + Sort ts DESC (adapter_any_of, Einzelwert)",
            "SELECT * FROM ringbuffer WHERE 1=1 AND source_adapter IN (?) ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            ("KNX", 100, 0),
            "CREATE INDEX idx_rb_adp_ts_id ON ringbuffer(source_adapter, ts DESC, id DESC)",
        ),
        (
            "C) datapoint_ids-Filter + Default-Sort id DESC (GUI-Default id/desc)",
            "SELECT * FROM ringbuffer WHERE 1=1 AND datapoint_id IN (?) ORDER BY id DESC LIMIT ? OFFSET ?",
            ("dp-0001", 100, 0),
            "CREATE INDEX idx_rb_dp_ts_id ON ringbuffer(datapoint_id, ts DESC, id DESC)",
        ),
        (
            "D) quality-Filter — KEIN realer query_v2-Pfad (quality wird nicht als SQL-Filter gebaut)",
            "SELECT * FROM ringbuffer WHERE 1=1 AND quality IN (?) ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            ("BAD", 100, 0),
            "CREATE INDEX idx_rb_quality_ts_id ON ringbuffer(quality, ts DESC, id DESC)",
        ),
        (
            "E) reiner Zeitfilter + Sort ts DESC (from_ts/to_ts)",
            "SELECT * FROM ringbuffer WHERE 1=1 AND ts > ? AND ts < ? ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            ("2026-01-01T00:00:00.000Z", "2026-01-01T02:00:00.000Z", 100, 0),
            "CREATE INDEX idx_rb_ts_id_desc ON ringbuffer(ts DESC, id DESC)",
        ),
    ]

    for title, sql, params, index_ddl in cases:
        conn = sqlite3.connect(":memory:")
        _populate_ringbuffer(conn)
        before = _plan(conn, sql, params)
        conn.execute(index_ddl)
        conn.execute("ANALYZE")
        after = _plan(conn, sql, params)
        conn.close()
        print(f"\n{title}")
        print(f"  Index-Kandidat: {index_ddl}")
        print("  VORHER:")
        print(before)
        print("  NACHHER:")
        print(after)
        print(f"  -> Änderung: {'JA (Plan verbessert)' if before != after else 'NEIN (identisch)'}")


# ---------------------------------------------------------------------------
# OBS.db
# ---------------------------------------------------------------------------


async def audit_obs_db() -> None:
    import aiosqlite

    from obs.db.database import Database

    print("\n" + "=" * 78)
    print("OBS.db — reale Query-Pfade (über echten Migrationspfad database.py)")
    print("=" * 78)

    db = Database(":memory:")
    await db.connect()
    conn: aiosqlite.Connection = db.conn

    # --- Testdaten befüllen -------------------------------------------------
    now = datetime.now(UTC).isoformat()
    # datapoints
    for i in range(N_DP):
        await conn.execute(
            "INSERT INTO datapoints (id, name, mqtt_topic, created_at, updated_at) VALUES (?,?,?,?,?)",
            (f"dp-{i:04d}", f"DP {i}", f"dp/{i}", now, now),
        )
    # adapter_instances
    inst_ids = [f"inst-{i:03d}" for i in range(N_INSTANCES)]
    types = ["KNX", "MODBUS_TCP", "MQTT", "HOME_ASSISTANT"]
    for i, iid in enumerate(inst_ids):
        await conn.execute(
            "INSERT INTO adapter_instances (id, adapter_type, name, created_at, updated_at) VALUES (?,?,?,?,?)",
            (iid, types[i % len(types)], f"inst {i}", now, now),
        )
    # adapter_bindings
    for i in range(N_BINDINGS):
        await conn.execute(
            """INSERT INTO adapter_bindings
               (id, datapoint_id, adapter_type, direction, enabled, adapter_instance_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                f"bind-{i:05d}",
                f"dp-{i % N_DP:04d}",
                types[i % len(types)],
                "SOURCE",
                i % 5 != 0,  # ~80% enabled
                inst_ids[i % N_INSTANCES],
                now,
                now,
            ),
        )
    # history_values
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(N_HIST_ROWS):
        ts = (base + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        await conn.execute(
            "INSERT INTO history_values (datapoint_id, value, quality, ts) VALUES (?,?,?,?)",
            (f"dp-{i % N_DP:04d}", str(i), "GOOD", ts),
        )
    # filterset + user_state
    await conn.execute(
        "INSERT INTO ringbuffer_filtersets (id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("fs-1", "FS", now, now),
    )
    for i in range(50):
        await conn.execute(
            """INSERT INTO ringbuffer_filterset_user_state
               (username, filterset_id, is_active, topbar_active, topbar_order) VALUES (?,?,?,?,?)""",
            (f"user{i}", "fs-1", 1, 1, i),
        )
    await conn.execute("ANALYZE")
    await conn.commit()

    async def plan(sql: str, params: tuple = ()) -> str:
        async with conn.execute(f"EXPLAIN QUERY PLAN {sql}", params) as cur:
            rows = await cur.fetchall()
        return "\n".join(f"    {r[3]}" for r in rows)

    async def case(title: str, sql: str, params: tuple, index_ddl: str | None) -> None:
        before = await plan(sql, params)
        after = before
        if index_ddl:
            await conn.execute(index_ddl)
            await conn.execute("ANALYZE")
            await conn.commit()
            after = await plan(sql, params)
        print(f"\n{title}")
        if index_ddl:
            print(f"  Index-Kandidat: {index_ddl}")
        print("  VORHER:")
        print(before)
        if index_ddl:
            print("  NACHHER:")
            print(after)
            print(f"  -> Änderung: {'JA (Plan verbessert)' if before != after else 'NEIN (identisch)'}")

    # 1) history_values — reale query() (obs/history/sqlite_plugin.py:82)
    await case(
        "1) history query(): WHERE datapoint_id=? AND ts>=? AND ts<=? ORDER BY ts DESC — bestehender idx_hist_dp_ts",
        "SELECT ts, value, unit, quality, source_adapter FROM history_values WHERE datapoint_id=? AND ts >= ? AND ts <= ? ORDER BY ts DESC LIMIT ?",
        ("dp-0001", "2026-01-01T00:00:00.000Z", "2026-01-01T02:00:00.000Z", 10000),
        "CREATE INDEX idx_hist_dp_ts_desc ON history_values(datapoint_id, ts DESC)",
    )

    # 2) history aggregate() — ORDER BY ts ASC (nutzt gleichen Index vorwärts)
    await case(
        "2) history aggregate(): WHERE datapoint_id=? AND ts>=? AND ts<=? ORDER BY ts (ASC) — bestehender idx_hist_dp_ts",
        "SELECT ts, value FROM history_values WHERE datapoint_id=? AND ts >= ? AND ts <= ? ORDER BY ts",
        ("dp-0001", "2026-01-01T00:00:00.000Z", "2026-01-01T02:00:00.000Z"),
        None,
    )

    # 3) adapter_bindings — registry hot path (obs/adapters/registry.py:102)
    await case(
        "3) registry: WHERE adapter_instance_id=? AND enabled=1 — KEIN passender Bestandsindex",
        "SELECT * FROM adapter_bindings WHERE adapter_instance_id=? AND enabled=1",
        ("inst-003",),
        "CREATE INDEX idx_bind_instance_enabled ON adapter_bindings(adapter_instance_id, enabled)",
    )

    # 4) adapter_bindings — write_router (obs/core/write_router.py:161)
    await case(
        "4) write_router: WHERE datapoint_id=? — bestehender idx_bind_datapoint",
        "SELECT direction, enabled, adapter_type FROM adapter_bindings WHERE datapoint_id=?",
        ("dp-0001",),
        "CREATE INDEX idx_bind_dp_enabled_created ON adapter_bindings(datapoint_id, enabled, created_at)",
    )

    # 5) bindings.py:70 — WHERE datapoint_id=? ORDER BY created_at
    await case(
        "5) bindings list: WHERE datapoint_id=? ORDER BY created_at — bestehender idx_bind_datapoint",
        "SELECT * FROM adapter_bindings WHERE datapoint_id=? ORDER BY created_at",
        ("dp-0001",),
        None,
    )

    # 6) adapter_bindings adapter_type-Pfad (search.py:136)
    await case(
        "6) search: WHERE adapter_type IN (?) — bestehender idx_bind_adapter",
        "SELECT DISTINCT datapoint_id FROM adapter_bindings WHERE adapter_type IN (?)",
        ("KNX",),
        "CREATE INDEX idx_bind_type_instance_enabled ON adapter_bindings(adapter_type, adapter_instance_id, enabled)",
    )

    # 7) adapter_instances — registry (registry.py:71) WHERE enabled=1 (winzige Tabelle)
    await case(
        "7) registry: WHERE enabled=1 auf adapter_instances (winzige Tabelle) — kein Bestandsindex auf enabled",
        "SELECT * FROM adapter_instances WHERE enabled=1",
        (),
        "CREATE INDEX idx_ai_type_enabled ON adapter_instances(adapter_type, enabled)",
    )

    # 8) user_state — ringbuffer.py:934 (PK-Zugriff)
    await case(
        "8) user_state: WHERE username=? AND filterset_id=? — PRIMARY KEY",
        "SELECT is_active, topbar_active, topbar_order FROM ringbuffer_filterset_user_state WHERE username=? AND filterset_id=?",
        ("user1", "fs-1"),
        None,
    )

    # 9) user_state — ringbuffer.py:934 WHERE username=? (Bestandsindexe username-lead)
    await case(
        "9) user_state: WHERE username=? — bestehende idx_rb_fs_user_state_* (username-lead)",
        "SELECT filterset_id, is_active, topbar_active, topbar_order FROM ringbuffer_filterset_user_state WHERE username=?",
        ("user1",),
        None,
    )

    await db.disconnect()


def main() -> None:
    import asyncio

    audit_ringbuffer()
    asyncio.run(audit_obs_db())
    print("\n" + "=" * 78)
    print("Legende: 'SCAN <table>' ohne 'USING INDEX' = Full-Table-Scan.")
    print("'USE TEMP B-TREE FOR ORDER BY' = teurer Sort ohne passenden Index.")
    print("'SEARCH ... USING INDEX ...' = Index-Suche.")
    print("=" * 78)


if __name__ == "__main__":
    main()
