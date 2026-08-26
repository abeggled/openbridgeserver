"""Regression test for the TOCTOU race between update_datapoint()'s
external_write_enabled enable-check and create_binding()'s opt-in cleanup
(Codex review, PR #1170 round 7).

Without obs.core.registry.DataPointRegistry.external_write_lock serializing
both sides, an admin's enable-PATCH could check "no write-semantic binding"
before a concurrent create_binding() commits its own binding, and neither
side's check would observe the other's not-yet-committed change — leaving
a DataPoint with both an enabled binding and external_write_enabled=True,
silently violating the invariant the PATCH route otherwise enforces.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from obs.api.auth import Principal
from obs.api.v1 import bindings as bindings_api
from obs.api.v1 import datapoints as dp_api
from obs.db.database import Database
from obs.models.binding import AdapterBindingCreate
from obs.models.datapoint import DataPointUpdate

NOW = "2026-06-10T00:00:00+00:00"
_ADAPTER_TYPE = "ANWESENHEITSSIMULATION"


class _RegistryStub:
    def __init__(self, dp) -> None:
        self._dp = dp
        self.external_write_lock = asyncio.Lock()
        # Set by the test to pause the *first* update() call right before it
        # mutates self._dp — simulates a PATCH that has already passed its
        # bindingless check and is about to persist, giving a concurrent
        # create_binding() a window to run before the flag is actually set.
        self.pause_next_update: tuple[asyncio.Event, asyncio.Event] | None = None

    def get(self, dp_id):
        return self._dp if dp_id == self._dp.id else None

    def get_value(self, dp_id):
        return None

    async def update(self, dp_id, body):
        if self.pause_next_update is not None:
            reached, proceed = self.pause_next_update
            self.pause_next_update = None
            reached.set()
            await proceed.wait()
        for field in body.model_fields_set:
            value = getattr(body, field)
            if value is not None and field != "value":
                setattr(self._dp, field, value)
        return self._dp


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _dp(dp_id: str) -> SimpleNamespace:
    parsed_id = uuid.UUID(dp_id)
    return SimpleNamespace(
        id=parsed_id,
        name="Race target",
        data_type="BOOLEAN",
        unit=None,
        tags=[],
        mqtt_topic=f"dp/{parsed_id}/value",
        mqtt_alias=None,
        persist_value=True,
        record_history=True,
        control_class="room_local",
        external_write_enabled=False,
        created_at=datetime(2026, 6, 10, tzinfo=UTC),
        updated_at=datetime(2026, 6, 10, tzinfo=UTC),
    )


async def _insert_datapoint(db: Database, dp) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history,
             control_class, external_write_enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, '[]', ?, NULL, 1, 1, ?, 0, ?, ?)
        """,
        (str(dp.id), dp.name, dp.data_type, dp.unit, dp.mqtt_topic, dp.control_class, NOW, NOW),
    )


async def _insert_adapter_instance(db: Database, instance_id: str) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at)
        VALUES (?, ?, 'Race Instance', '{}', 0, ?, ?)
        """,
        (instance_id, _ADAPTER_TYPE, NOW, NOW),
    )


@pytest.mark.asyncio
async def test_enable_patch_and_concurrent_create_binding_never_leave_both_true(monkeypatch, db: Database):
    datapoint = _dp("00000000-0000-0000-0000-0000000000f1")
    await _insert_datapoint(db, datapoint)
    instance_id = str(uuid.uuid4())
    await _insert_adapter_instance(db, instance_id)

    registry = _RegistryStub(datapoint)
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)
    monkeypatch.setattr(bindings_api, "get_registry", lambda: registry)

    admin = Principal(subject="admin", type="user", is_admin=True)

    reached_update = asyncio.Event()
    proceed = asyncio.Event()
    registry.pause_next_update = (reached_update, proceed)

    patch_task = asyncio.create_task(
        dp_api.update_datapoint(
            dp_id=datapoint.id,
            body=DataPointUpdate(external_write_enabled=True),
            request=None,
            _user=admin,
            db=db,
        )
    )
    # The bindingless check has already passed (real, unpatched — the
    # datapoint has no binding yet) and the PATCH is paused right before
    # persisting, still holding external_write_lock the whole time.
    await reached_update.wait()

    binding_task = asyncio.create_task(
        bindings_api.create_binding(
            dp_id=datapoint.id,
            body=AdapterBindingCreate(
                adapter_instance_id=uuid.UUID(instance_id),
                direction="SOURCE",
                config={},
                enabled=True,
            ),
            _user=admin,
            db=db,
        )
    )
    # Without the shared lock, create_binding() would run to completion here
    # — inserting the binding and finding external_write_enabled still False
    # (the paused PATCH hasn't set it yet), so its own cleanup no-ops. With
    # the lock, create_binding() blocks trying to acquire the same lock the
    # paused PATCH is holding, and genuinely cannot proceed until the PATCH's
    # `async with` exits — asserted directly here, deterministically, rather
    # than relying on a fixed sleep to "probably" have let it run far enough.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(binding_task), timeout=0.2)

    proceed.set()  # let the paused PATCH persist external_write_enabled=True
    await patch_task
    await binding_task

    # The binding must exist (create_binding always succeeds — it doesn't
    # depend on the flag), and the flag must not still read true once a
    # write-semantic binding exists: whichever side committed first, the
    # other's check/cleanup must have observed it.
    row = await db.fetchone(
        "SELECT 1 FROM adapter_bindings WHERE datapoint_id=? AND enabled=1 AND adapter_type != 'MESSAGE'",
        (str(datapoint.id),),
    )
    assert row is not None
    assert datapoint.external_write_enabled is False
