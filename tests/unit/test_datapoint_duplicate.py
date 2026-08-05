from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import aiosqlite
import pytest

import obs.api.v1.datapoints as datapoints_api
from obs.db.database import Database
from obs.models.datapoint import DataPoint


class _RegistryStub:
    def __init__(self, source: DataPoint, duplicate: DataPoint) -> None:
        self.source = source
        self.duplicate = duplicate
        self.inserted: list[uuid.UUID] = []
        self.prepared_payloads = []
        self.published: list[uuid.UUID] = []

    def get(self, dp_id: uuid.UUID) -> DataPoint | None:
        return self.source if dp_id == self.source.id else None

    def prepare_create(self, payload) -> DataPoint:
        self.prepared_payloads.append(payload)
        return self.duplicate

    async def insert(self, dp: DataPoint, *, connection=None) -> None:
        self.inserted.append(dp.id)

    def publish(self, dp: DataPoint) -> None:
        self.published.append(dp.id)


class _FailingDb:
    def __init__(self, binding: dict, source_row: dict | None = None) -> None:
        self.binding = binding
        self.source_row = source_row
        self.rolled_back = False
        self.in_transaction = False

    async def fetchall(self, _sql: str, _params: tuple[str]) -> list[dict]:
        assert self.in_transaction
        return [self.binding]

    async def fetchone(self, _sql: str, params: tuple[str]) -> dict:
        assert self.in_transaction
        return self.source_row or _datapoint_row(params[0])

    @asynccontextmanager
    async def isolated_transaction(self):
        self.in_transaction = True
        try:
            yield self
        finally:
            self.in_transaction = False

    async def execute(self, sql: str, _params=()) -> None:
        assert sql == "BEGIN IMMEDIATE"

    async def executemany(self, _sql: str, _params: list[tuple]) -> None:
        raise RuntimeError("copy failed")

    async def rollback(self) -> None:
        self.rolled_back = True


class _SuccessfulDb:
    def __init__(self, binding: dict, source_row: dict | None = None) -> None:
        self.binding = binding
        self.source_row = source_row
        self.committed = False
        self.in_transaction = False

    async def fetchall(self, _sql: str, _params: tuple[str]) -> list[dict]:
        assert self.in_transaction
        return [self.binding]

    async def fetchone(self, _sql: str, params: tuple[str]) -> dict:
        assert self.in_transaction
        return self.source_row or _datapoint_row(params[0])

    @asynccontextmanager
    async def isolated_transaction(self):
        self.in_transaction = True
        try:
            yield self
        finally:
            self.in_transaction = False

    async def execute(self, sql: str, _params=()) -> None:
        assert sql == "BEGIN IMMEDIATE"

    async def executemany(self, _sql: str, _params: list[tuple]) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


class _CancelledDb(_FailingDb):
    def __init__(self, binding: dict) -> None:
        super().__init__(binding)
        self.copy_started = asyncio.Event()

    async def executemany(self, _sql: str, _params: list[tuple]) -> None:
        self.copy_started.set()
        await asyncio.Future()


class _MissingSourceDb(_FailingDb):
    async def fetchone(self, _sql: str, _params: tuple[str]) -> None:
        assert self.in_transaction


class _SlowCommitDb(_SuccessfulDb):
    def __init__(self, binding: dict) -> None:
        super().__init__(binding)
        self.commit_started = asyncio.Event()
        self.finish_commit = asyncio.Event()

    async def commit(self) -> None:
        self.commit_started.set()
        await self.finish_commit.wait()
        self.committed = True


class _FailingCommitDb(_SuccessfulDb):
    def __init__(self, binding: dict) -> None:
        super().__init__(binding)
        self.rolled_back = False

    async def commit(self) -> None:
        raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rolled_back = True


class _SlowFailingCommitDb(_FailingCommitDb):
    def __init__(self, binding: dict) -> None:
        super().__init__(binding)
        self.commit_started = asyncio.Event()
        self.finish_commit = asyncio.Event()

    async def commit(self) -> None:
        self.commit_started.set()
        await self.finish_commit.wait()
        raise RuntimeError("commit failed")


class _SlowExitDb(_SuccessfulDb):
    def __init__(self, binding: dict) -> None:
        super().__init__(binding)
        self.exit_started = asyncio.Event()
        self.finish_exit = asyncio.Event()

    @asynccontextmanager
    async def isolated_transaction(self):
        self.in_transaction = True
        try:
            yield self
        finally:
            self.exit_started.set()
            exit_task = asyncio.create_task(self.finish_exit.wait())
            try:
                await asyncio.shield(exit_task)
            except asyncio.CancelledError:
                await exit_task
                raise
            finally:
                self.in_transaction = False


def _binding_row() -> dict:
    return {
        "adapter_type": "MQTT",
        "adapter_instance_id": str(uuid.uuid4()),
        "direction": "SOURCE",
        "config": '{"topic":"test"}',
        "enabled": 1,
        "send_throttle_ms": None,
        "send_on_change": 0,
        "send_min_delta": None,
        "send_min_delta_pct": None,
        "value_formula": None,
        "value_map": None,
    }


def _datapoint_row(dp_id: str, **overrides) -> dict:
    row = {
        "id": dp_id,
        "name": "Source",
        "data_type": "FLOAT",
        "unit": None,
        "tags": "[]",
        "mqtt_topic": f"dp/{dp_id}/value",
        "mqtt_alias": None,
        "persist_value": 1,
        "record_history": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_duplicate_datapoint_removes_created_datapoint_when_binding_copy_fails(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _FailingDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)

    with pytest.raises(RuntimeError, match="copy failed"):
        await datapoints_api.duplicate_datapoint(
            source.id,
            datapoints_api.DataPointDuplicateIn(name="Copy"),
            _user="admin",
            db=db,
        )

    assert db.rolled_back is True
    assert registry.inserted == [duplicate.id]
    assert registry.published == []


@pytest.mark.asyncio
async def test_duplicate_datapoint_rechecks_source_inside_transaction(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _MissingSourceDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)

    with pytest.raises(datapoints_api.HTTPException) as exc_info:
        await datapoints_api.duplicate_datapoint(
            source.id,
            datapoints_api.DataPointDuplicateIn(name="Copy"),
            _user="admin",
            db=db,
        )

    assert exc_info.value.status_code == 404
    assert db.rolled_back is True
    assert registry.inserted == []
    assert registry.published == []


@pytest.mark.asyncio
async def test_duplicate_datapoint_snapshots_metadata_inside_transaction(monkeypatch):
    source = DataPoint(name="Stale source", data_type="STRING")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _SuccessfulDb(
        _binding_row(),
        _datapoint_row(
            str(source.id),
            name="Current source",
            data_type="FLOAT",
            unit="°C",
            tags=json.dumps(["current"]),
            mqtt_alias="current/source",
            persist_value=0,
            record_history=0,
        ),
    )
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "_enrich", lambda dp: dp)

    await datapoints_api.duplicate_datapoint(
        source.id,
        datapoints_api.DataPointDuplicateIn(name="Copy"),
        _user="admin",
        db=db,
    )

    payload = registry.prepared_payloads[0]
    assert payload.data_type == "FLOAT"
    assert payload.unit == "°C"
    assert payload.tags == ["current"]
    assert payload.mqtt_alias == "current/source"
    assert payload.persist_value is False
    assert payload.record_history is False


@pytest.mark.asyncio
async def test_duplicate_datapoint_cleans_up_when_binding_copy_is_cancelled(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _CancelledDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)

    request_task = asyncio.create_task(
        datapoints_api.duplicate_datapoint(
            source.id,
            datapoints_api.DataPointDuplicateIn(name="Copy"),
            _user="admin",
            db=db,
        ),
    )
    await db.copy_started.wait()
    request_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await request_task

    assert db.rolled_back is True
    assert registry.inserted == [duplicate.id]
    assert registry.published == []


@pytest.mark.asyncio
async def test_duplicate_datapoint_ignores_post_commit_adapter_reload_failures(monkeypatch, caplog):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _SuccessfulDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "_enrich", lambda dp: dp)

    async def _reload_failure(_instance_id: str, _db) -> None:
        raise RuntimeError("reload failed")

    import obs.api.v1.bindings as bindings_api

    monkeypatch.setattr(bindings_api, "_reload_adapter_instance", _reload_failure)

    result = await datapoints_api.duplicate_datapoint(
        source.id,
        datapoints_api.DataPointDuplicateIn(name="Copy"),
        _user="admin",
        db=db,
    )

    assert result is duplicate
    assert db.committed is True
    assert registry.published == [duplicate.id]
    assert "duplicated successfully" in caplog.text
    assert "reload failed" in caplog.text


@pytest.mark.asyncio
async def test_duplicate_datapoint_waits_for_adapter_reload(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _SuccessfulDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "_enrich", lambda dp: dp)
    reload_started = asyncio.Event()
    finish_reload = asyncio.Event()

    async def _slow_reload(_dp_id, _instance_ids, _db):
        assert not db.in_transaction
        reload_started.set()
        await finish_reload.wait()

    monkeypatch.setattr(datapoints_api, "_reload_duplicate_bindings", _slow_reload)
    request_task = asyncio.create_task(
        datapoints_api.duplicate_datapoint(
            source.id,
            datapoints_api.DataPointDuplicateIn(name="Copy"),
            _user="admin",
            db=db,
        )
    )
    await reload_started.wait()
    assert not request_task.done()

    finish_reload.set()
    assert await request_task is duplicate


@pytest.mark.asyncio
async def test_duplicate_datapoint_does_not_reload_disabled_bindings(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    disabled_binding = {**_binding_row(), "enabled": 0}
    registry = _RegistryStub(source, duplicate)
    db = _SuccessfulDb(disabled_binding)
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    monkeypatch.setattr(datapoints_api, "_enrich", lambda dp: dp)

    result = await datapoints_api.duplicate_datapoint(
        source.id,
        datapoints_api.DataPointDuplicateIn(name="Copy"),
        _user="admin",
        db=db,
    )

    assert result is duplicate
    assert registry.published == [duplicate.id]


@pytest.mark.asyncio
async def test_duplicate_datapoint_publishes_if_cancelled_commit_completes(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _SlowCommitDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    reloaded = asyncio.Event()

    async def _record_reload(_dp_id, _instance_ids, _db):
        assert not db.in_transaction
        reloaded.set()

    monkeypatch.setattr(datapoints_api, "_reload_duplicate_bindings", _record_reload)

    request_task = asyncio.create_task(
        datapoints_api.duplicate_datapoint(
            source.id,
            datapoints_api.DataPointDuplicateIn(name="Copy"),
            _user="admin",
            db=db,
        )
    )
    await db.commit_started.wait()
    request_task.cancel()
    db.finish_commit.set()

    with pytest.raises(asyncio.CancelledError):
        await request_task

    assert db.committed is True
    assert registry.published == [duplicate.id]
    assert reloaded.is_set()


@pytest.mark.asyncio
async def test_duplicate_datapoint_rolls_back_when_commit_fails(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _FailingCommitDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)

    with pytest.raises(RuntimeError, match="commit failed"):
        await datapoints_api.duplicate_datapoint(
            source.id,
            datapoints_api.DataPointDuplicateIn(name="Copy"),
            _user="admin",
            db=db,
        )

    assert db.rolled_back is True
    assert registry.published == []


@pytest.mark.asyncio
async def test_duplicate_datapoint_rolls_back_when_cancelled_commit_fails(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _SlowFailingCommitDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)

    request_task = asyncio.create_task(
        datapoints_api.duplicate_datapoint(
            source.id,
            datapoints_api.DataPointDuplicateIn(name="Copy"),
            _user="admin",
            db=db,
        )
    )
    await db.commit_started.wait()
    request_task.cancel()
    db.finish_commit.set()

    with pytest.raises(RuntimeError, match="commit failed"):
        await request_task
    assert db.rolled_back is True
    assert registry.published == []


@pytest.mark.asyncio
async def test_duplicate_datapoint_reloads_if_cancelled_during_transaction_cleanup(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _SlowExitDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    reloaded = asyncio.Event()

    async def _record_reload(_dp_id, _instance_ids, _db):
        assert not db.in_transaction
        reloaded.set()

    monkeypatch.setattr(datapoints_api, "_reload_duplicate_bindings", _record_reload)

    request_task = asyncio.create_task(
        datapoints_api.duplicate_datapoint(
            source.id,
            datapoints_api.DataPointDuplicateIn(name="Copy"),
            _user="admin",
            db=db,
        )
    )
    await db.exit_started.wait()

    assert db.committed is True
    assert registry.published == [duplicate.id]

    request_task.cancel()
    await asyncio.sleep(0)
    assert not request_task.done()
    db.finish_exit.set()
    with pytest.raises(asyncio.CancelledError):
        await request_task
    assert reloaded.is_set()


@pytest.mark.asyncio
async def test_duplicate_datapoint_finishes_reload_when_cancelled(monkeypatch):
    source = DataPoint(name="Source", data_type="FLOAT")
    duplicate = DataPoint(name="Copy", data_type="FLOAT")
    registry = _RegistryStub(source, duplicate)
    db = _SuccessfulDb(_binding_row())
    monkeypatch.setattr(datapoints_api, "get_registry", lambda: registry)
    reload_started = asyncio.Event()
    finish_reload = asyncio.Event()
    reload_finished = asyncio.Event()

    async def _slow_reload(_dp_id, _instance_ids, _db):
        reload_started.set()
        await finish_reload.wait()
        reload_finished.set()

    monkeypatch.setattr(datapoints_api, "_reload_duplicate_bindings", _slow_reload)
    request_task = asyncio.create_task(
        datapoints_api.duplicate_datapoint(
            source.id,
            datapoints_api.DataPointDuplicateIn(name="Copy"),
            _user="admin",
            db=db,
        )
    )
    await reload_started.wait()
    request_task.cancel()
    await asyncio.sleep(0)
    assert not request_task.done()

    finish_reload.set()
    with pytest.raises(asyncio.CancelledError):
        await request_task
    assert reload_finished.is_set()


@pytest.mark.asyncio
async def test_database_transaction_uses_an_isolated_connection(tmp_path):
    db = Database(str(tmp_path / "isolated.db"))
    await db.connect()
    try:
        async with db.isolated_transaction() as transaction:
            assert transaction._conn is not db.conn
            await transaction.execute("CREATE TABLE isolated_write (id INTEGER PRIMARY KEY)")
            await transaction.commit()

        row = await db.fetchone("SELECT name FROM sqlite_master WHERE name='isolated_write'")
        assert row["name"] == "isolated_write"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_database_transaction_finishes_cleanup_when_cancelled(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "cancelled-cleanup.db"))
    await db.connect()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def _use_transaction() -> None:
        async with db.isolated_transaction() as transaction:
            original_close = transaction._conn.close

            async def _slow_close() -> None:
                cleanup_started.set()
                await finish_cleanup.wait()
                await original_close()
                cleanup_finished.set()

            monkeypatch.setattr(transaction._conn, "close", _slow_close)
            await transaction.execute("CREATE TABLE cleanup_write (id INTEGER PRIMARY KEY)")
            await transaction.commit()

    try:
        transaction_task = asyncio.create_task(_use_transaction())
        await cleanup_started.wait()
        transaction_task.cancel()
        await asyncio.sleep(0)
        assert not transaction_task.done()

        finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await transaction_task
        assert cleanup_finished.is_set()

        async with db.isolated_transaction() as transaction:
            row = await transaction.fetchone("SELECT name FROM sqlite_master WHERE name='cleanup_write'")
            assert row["name"] == "cleanup_write"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database_path",
    [":memory:", "file:duplicate-normalized?mode=memory", "file::memory:"],
)
async def test_memory_database_transaction_uses_private_connection_and_rolls_back(database_path):
    db = Database(database_path)
    await db.connect()
    try:
        await db.execute_and_commit("CREATE TABLE rolled_back_write (id INTEGER PRIMARY KEY)")
        async with db.isolated_transaction() as transaction:
            assert transaction._conn is not db.conn
            await transaction.execute("INSERT INTO rolled_back_write (id) VALUES (1)")

        row = await db.fetchone("SELECT COUNT(*) AS count FROM rolled_back_write")
        assert row["count"] == 0
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_isolated_transaction_closes_connection_when_setup_fails(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "setup-failure.db"))
    await db.connect()
    real_connect = aiosqlite.connect
    connection_closed = asyncio.Event()

    async def _connect_with_failing_setup(*args, **kwargs):
        connection = await real_connect(*args, **kwargs)
        real_close = connection.close

        async def _fail_execute(_sql, _params=None):
            raise RuntimeError("pragma failed")

        async def _record_close():
            await real_close()
            connection_closed.set()

        monkeypatch.setattr(connection, "execute", _fail_execute)
        monkeypatch.setattr(connection, "close", _record_close)
        return connection

    monkeypatch.setattr(aiosqlite, "connect", _connect_with_failing_setup)
    try:
        with pytest.raises(RuntimeError, match="pragma failed"):
            async with db.isolated_transaction():
                pytest.fail("transaction setup unexpectedly succeeded")
        assert connection_closed.is_set()
    finally:
        monkeypatch.setattr(aiosqlite, "connect", real_connect)
        await db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database_path",
    [":memory:", "file:duplicate-lock?mode=memory&cache=shared"],
)
async def test_memory_database_ordinary_write_waits_for_isolated_transaction(database_path):
    db = Database(database_path)
    await db.connect()
    try:
        await db.execute_and_commit("CREATE TABLE serialized_write (id INTEGER PRIMARY KEY)")
        async with db.isolated_transaction() as transaction:
            await transaction.execute("INSERT INTO serialized_write (id) VALUES (1)")
            ordinary_write = asyncio.create_task(db.execute_and_commit("INSERT INTO serialized_write (id) VALUES (2)"))
            await asyncio.sleep(0)
            assert not ordinary_write.done()
            await transaction.commit()

        await ordinary_write
        row = await db.fetchone("SELECT COUNT(*) AS count FROM serialized_write")
        assert row["count"] == 2
    finally:
        await db.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database_path",
    [":memory:", "file:duplicate-wait?mode=memory&cache=shared"],
)
async def test_memory_database_isolated_transaction_waits_for_ordinary_transaction(database_path):
    db = Database(database_path)
    await db.connect()
    try:
        await db.execute_and_commit("CREATE TABLE ordinary_write (id INTEGER PRIMARY KEY)")
        await db.execute("INSERT INTO ordinary_write (id) VALUES (1)")
        transaction_entered = asyncio.Event()

        async def _enter_transaction() -> None:
            async with db.isolated_transaction():
                transaction_entered.set()

        transaction_task = asyncio.create_task(_enter_transaction())
        await asyncio.sleep(0)
        assert not transaction_entered.is_set()

        row = await asyncio.wait_for(
            db.fetchone("SELECT COUNT(*) AS count FROM ordinary_write"),
            timeout=1,
        )
        assert row["count"] == 1
        await db.commit()
        await transaction_task
        assert transaction_entered.is_set()
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_memory_database_isolated_transaction_rechecks_after_acquiring_lock():
    db = Database(":memory:")
    await db.connect()
    original_lock = db._memory_operation_lock
    acquisition_started = asyncio.Event()

    class _ObservedLock:
        async def acquire(self):
            acquisition_started.set()
            return await original_lock.acquire()

        def release(self):
            original_lock.release()

        async def __aenter__(self):
            await self.acquire()
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            self.release()

    try:
        await original_lock.acquire()
        db._memory_operation_lock = _ObservedLock()
        transaction_entered = asyncio.Event()

        async def _enter_transaction() -> None:
            async with db.isolated_transaction():
                transaction_entered.set()

        transaction_task = asyncio.create_task(_enter_transaction())
        await acquisition_started.wait()
        await db.conn.execute("BEGIN")
        original_lock.release()
        await asyncio.sleep(0)
        assert not transaction_entered.is_set()

        await asyncio.wait_for(db.fetchone("SELECT 1"), timeout=1)
        await db.commit()
        await transaction_task
        assert transaction_entered.is_set()
    finally:
        if original_lock.locked():
            original_lock.release()
        db._memory_operation_lock = original_lock
        await db.disconnect()


@pytest.mark.asyncio
async def test_disconnect_waits_for_isolated_transaction(tmp_path):
    db = Database(str(tmp_path / "disconnect.db"))
    await db.connect()
    transaction_entered = asyncio.Event()
    release_transaction = asyncio.Event()

    async def _hold_transaction() -> None:
        async with db.isolated_transaction():
            transaction_entered.set()
            await release_transaction.wait()

    transaction_task = asyncio.create_task(_hold_transaction())
    await transaction_entered.wait()
    disconnect_task = asyncio.create_task(db.disconnect())
    await asyncio.sleep(0)
    assert not disconnect_task.done()

    release_transaction.set()
    await transaction_task
    await disconnect_task
    assert db._conn is None


@pytest.mark.asyncio
async def test_exclusive_lifecycle_blocks_transactions_until_reconnect(tmp_path):
    db = Database(str(tmp_path / "replacement.db"))
    await db.connect()
    transaction_entered = asyncio.Event()

    async def _enter_transaction() -> None:
        async with db.isolated_transaction():
            transaction_entered.set()

    async with db.exclusive_lifecycle() as lifecycle:
        await lifecycle.disconnect()
        transaction_task = asyncio.create_task(_enter_transaction())
        await asyncio.sleep(0)
        assert not transaction_entered.is_set()
        await lifecycle.connect()

    await transaction_task
    assert transaction_entered.is_set()
    await db.disconnect()


@pytest.mark.asyncio
async def test_isolated_transaction_rejects_disconnected_database(tmp_path):
    db = Database(str(tmp_path / "disconnected.db"))
    await db.connect()
    await db.disconnect()

    with pytest.raises(RuntimeError, match=r"Database\.connect"):
        async with db.isolated_transaction():
            pytest.fail("disconnected transaction unexpectedly opened")
