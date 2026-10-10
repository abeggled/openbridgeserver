"""Migration V55 (#1296): the project's group address style is stored.

Existing installations imported their addresses in the project's notation
(before #1296 nothing normalized them), so the stored addresses reveal the
style; a fresh or mixed installation falls back to three-level.
"""

from __future__ import annotations

import pytest

from obs.db import database
from obs.db.database import Database, _migration_v55_knx_group_address_style


async def _pre_1296_db(monkeypatch) -> Database:
    """Database at schema V54: raw notations were still allowed in knx_group_addresses."""
    monkeypatch.setattr(database, "MIGRATIONS", [m for m in database.MIGRATIONS if m[0] <= 54])
    db = Database(":memory:")
    await db.connect()
    monkeypatch.undo()
    await _migration_v55_knx_group_address_style(db.conn)
    return db


async def _style(db: Database) -> str | None:
    row = await db.fetchone("SELECT group_address_style FROM knx_project WHERE id = 1")
    return row["group_address_style"] if row else None


@pytest.mark.asyncio
async def test_fresh_database_defaults_to_three_level():
    db = Database(":memory:")
    await db.connect()
    try:
        assert await _style(db) == "ThreeLevel"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("addresses", "expected"),
    [
        (["1/0/234", "1/0/235"], "ThreeLevel"),
        (["1/234", "1/235"], "TwoLevel"),
        (["2282", "2283"], "Free"),
        (["1/0/234", "1/235"], "ThreeLevel"),  # tie: no single style to infer
        (["1/234", "1/235", "1/236", "32/0/0", "1/9/9", "01/1/1"], "TwoLevel"),  # invalid rows do not count, majority wins
        ([], "ThreeLevel"),
    ],
)
async def test_existing_addresses_reveal_the_style(addresses, expected, monkeypatch):
    db = await _pre_1296_db(monkeypatch)
    try:
        await db.execute("DELETE FROM knx_project")
        await db.executemany("INSERT INTO knx_group_addresses (address, name) VALUES (?, '')", [(a,) for a in addresses])
        await db.commit()

        await _migration_v55_knx_group_address_style(db.conn)

        assert await _style(db) == expected
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_migration_keeps_an_already_stored_style(monkeypatch):
    db = await _pre_1296_db(monkeypatch)
    try:
        await db.execute_and_commit("UPDATE knx_project SET group_address_style='Free'")
        await db.execute_and_commit("INSERT INTO knx_group_addresses (address, name) VALUES ('1/234', '')")

        await _migration_v55_knx_group_address_style(db.conn)

        assert await _style(db) == "Free"
    finally:
        await db.disconnect()
