"""Index-Audit #919/#935 — Migration V38.

Belegt, dass der einzige umgesetzte OBS.db-Index
``idx_bind_instance_enabled`` idempotent angelegt wird und den realen
Registry-Hot-Path (``WHERE adapter_instance_id=? AND enabled=1``) vom
Full-Table-Scan auf eine Index-Suche bringt.
"""

from __future__ import annotations

import pytest

from obs.db.database import Database


@pytest.mark.asyncio
async def test_migration_v38_creates_binding_instance_enabled_index():
    db = Database(":memory:")
    await db.connect()
    try:
        indexes = await db.fetchall("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='adapter_bindings'")
        index_names = {row["name"] for row in indexes}
        assert "idx_bind_instance_enabled" in index_names

        cols = await db.fetchall("PRAGMA index_info(idx_bind_instance_enabled)")
        assert [row["name"] for row in cols] == ["adapter_instance_id", "enabled"]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_migration_v38_is_idempotent():
    """Re-running connect()/migrations must not fail (CREATE INDEX IF NOT EXISTS)."""
    db = Database(":memory:")
    await db.connect()
    try:
        # Explicitly re-run the migration body — must be a no-op, not raise.
        await db.conn.executescript("CREATE INDEX IF NOT EXISTS idx_bind_instance_enabled ON adapter_bindings(adapter_instance_id, enabled);")
        await db.commit()
        row = await db.fetchone("SELECT COUNT(*) AS n FROM sqlite_master WHERE type='index' AND name='idx_bind_instance_enabled'")
        assert row["n"] == 1
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_registry_hot_path_uses_index_not_scan():
    """EXPLAIN QUERY PLAN for the registry binding-load query must use the index."""
    db = Database(":memory:")
    await db.connect()
    try:
        query = "SELECT * FROM adapter_bindings WHERE adapter_instance_id=? AND enabled=1"
        rows = await db.fetchall(f"EXPLAIN QUERY PLAN {query}", ("inst-1",))
        detail = " ".join(row["detail"] for row in rows)
        assert "idx_bind_instance_enabled" in detail
        # A plain sequential scan would read "SCAN adapter_bindings" without USING INDEX.
        assert "USING INDEX idx_bind_instance_enabled" in detail
    finally:
        await db.disconnect()
