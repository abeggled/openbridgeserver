from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

from obs.api.auth import Principal
from obs.api.v1 import logic as logic_api
from obs.db.database import Database
from obs.logic.models import FlowData, LogicGraphCreate, LogicGraphUpdate


class _CoordinatedDatabase(Database):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.pause_after_next_commit = False
        self.transaction_committed = asyncio.Event()
        self.resume_return_path = asyncio.Event()

    def pause_next_transaction_after_commit(self) -> None:
        self.pause_after_next_commit = True
        self.transaction_committed = asyncio.Event()
        self.resume_return_path = asyncio.Event()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with super().transaction():
            yield
        if self.pause_after_next_commit:
            self.pause_after_next_commit = False
            self.transaction_committed.set()
            await self.resume_return_path.wait()


@pytest.fixture(scope="module")
async def file_db(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[_CoordinatedDatabase]:
    db = _CoordinatedDatabase(str(tmp_path_factory.mktemp("logic-concurrency") / "obs.db"))
    await db.connect()
    try:
        yield db
    finally:
        await db.disconnect()


async def _insert_graph(db: Database, graph_id: str, name: str) -> None:
    await db.execute_and_commit(
        """INSERT INTO logic_graphs
               (id, name, description, enabled, flow_data, created_at, updated_at)
           VALUES (?, ?, '', 1, '{"nodes":[],"edges":[]}',
                   '2026-08-10T00:00:00+00:00', '2026-08-10T00:00:00+00:00')""",
        (graph_id, name),
    )


@pytest.mark.asyncio
async def test_create_returns_transaction_snapshot_while_shared_reader_is_stale(
    file_db: _CoordinatedDatabase,
) -> None:
    await _insert_graph(file_db, "reader-seed-1", "Reader seed 1")
    await _insert_graph(file_db, "reader-seed-2", "Reader seed 2")
    held_reader = await file_db.conn.execute("SELECT * FROM logic_graphs ORDER BY id")
    await held_reader.fetchone()

    try:
        row = await logic_api._persist_created_graph(
            file_db,
            Principal(subject="admin", type="user", is_admin=True),
            None,
            name="Concurrent create",
            description="",
            enabled=True,
            flow=FlowData(),
            control_class="room_local",
            delegated=False,
            audit_path="/api/v1/logic/graphs",
        )
    finally:
        await held_reader.close()

    assert row["name"] == "Concurrent create"


@pytest.mark.asyncio
@pytest.mark.parametrize("update_kind", ["put", "patch"])
async def test_update_returns_transaction_snapshot_during_uncommitted_concurrent_delete(
    file_db: _CoordinatedDatabase,
    monkeypatch: pytest.MonkeyPatch,
    update_kind: str,
) -> None:
    graph_id = f"concurrent-{update_kind}"
    await _insert_graph(file_db, graph_id, "Before")
    manager = MagicMock()
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: manager)

    file_db.pause_next_transaction_after_commit()
    if update_kind == "put":
        update = logic_api.update_graph_full
        body = LogicGraphCreate(name="After", flow_data=FlowData())
    else:
        update = logic_api.update_graph_partial
        body = LogicGraphUpdate(name="After")
    update_task = asyncio.create_task(
        update(
            graph_id,
            body,
            _user="admin",
            db=file_db,
        )
    )
    await asyncio.wait_for(file_db.transaction_committed.wait(), timeout=5)
    await file_db.execute("DELETE FROM logic_graphs WHERE id=?", (graph_id,))
    file_db.resume_return_path.set()
    try:
        result = await update_task
    finally:
        await file_db.rollback()

    assert result.id == graph_id
    assert result.name == "After"
    persisted = await file_db.fetchone("SELECT name FROM logic_graphs WHERE id=?", (graph_id,))
    assert persisted is not None
    assert persisted["name"] == "After"
