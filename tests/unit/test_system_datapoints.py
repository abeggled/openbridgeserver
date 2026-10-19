"""Read-only, backend-managed system DataPoints (issue #1370)."""

from __future__ import annotations

import sqlite3
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from obs.api.v1 import bindings as bindings_api
from obs.api.v1 import config as config_api
from obs.api.v1 import datapoints as dp_api
from obs.core.event_bus import DataValueEvent, EventBus
from obs.core.registry import DataPointRegistry, ValueState
from obs.core.system_datapoints import (
    CURRENT_VERSION,
    LAST_CHECK,
    LATEST_VERSION,
    SYSTEM_DATAPOINT_IDS,
    SYSTEM_DATAPOINTS,
    SYSTEM_SOURCE_ADAPTER,
    UPDATE_AVAILABLE,
    UPDATE_CHANNEL,
    SystemDataPointError,
    is_system_datapoint,
)
from obs.core.write_router import WriteRouter
from obs.db.database import Database
from obs.models.datapoint import DataPointCreate, DataPointUpdate


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


@pytest.fixture
async def registry(db):
    reg = DataPointRegistry(db=db, mqtt_client=AsyncMock(), event_bus=AsyncMock())
    await reg.load_from_db()
    return reg


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


def test_ids_are_stable_constants():
    # Visu and Logic configurations reference these IDs: they must never change.
    assert {d.name: str(d.id) for d in SYSTEM_DATAPOINTS} == {
        "OBS Update Channel": "986724a8-f7cc-53d9-980a-027c47ca7e43",
        "OBS Current Version": "3798f13a-7a2f-5d71-937f-de53c929098a",
        "OBS Latest Version": "1cd54729-d457-51c7-981a-2b31c9aac15b",
        "OBS Last Check": "9f70eb45-f1aa-5ae0-86c2-69bab3f817ba",
        "OBS Update Available": "e9a22ddf-c977-5676-a9b6-e5dc8278f63e",
    }
    assert [(d.data_type) for d in (UPDATE_CHANNEL, CURRENT_VERSION, LATEST_VERSION, LAST_CHECK, UPDATE_AVAILABLE)] == [
        "STRING",
        "STRING",
        "STRING",
        "DATETIME",
        "BOOLEAN",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (UPDATE_CHANNEL.id, True),
        (str(LAST_CHECK.id), True),
        (uuid.uuid4(), False),
        (str(uuid.uuid4()), False),
        ("not-a-uuid", False),
        (None, False),
    ],
)
def test_is_system_datapoint(value, expected):
    assert is_system_datapoint(value) is expected


def test_error_names_the_datapoint():
    err = SystemDataPointError(UPDATE_CHANNEL.id)
    assert err.dp_id == UPDATE_CHANNEL.id
    assert str(UPDATE_CHANNEL.id) in str(err)


# ---------------------------------------------------------------------------
# EventBus guard — the choke point for every write path
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_write_warnings(monkeypatch):
    from obs.core import event_bus

    monkeypatch.setattr(event_bus, "_warned_system_writes", set())


@pytest.mark.parametrize("source", ["api", "mqtt_set", "logic", "KNX", "WEBHOOK"])
async def test_bus_drops_foreign_writes_to_system_datapoints(source, caplog):
    bus = EventBus()
    handler = AsyncMock()
    bus.subscribe(DataValueEvent, handler)
    await bus.publish(DataValueEvent(datapoint_id=UPDATE_AVAILABLE.id, value=True, quality="good", source_adapter=source))
    handler.assert_not_awaited()
    assert "read-only system DataPoint" in caplog.text


async def test_bus_warns_once_per_source_and_not_for_confirmations(caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="obs.core.event_bus")
    bus = EventBus()
    confirmation = DataValueEvent(datapoint_id=UPDATE_AVAILABLE.id, value=True, quality="good", source_adapter="KNX", suppress_write_propagation=True)
    telegram = DataValueEvent(datapoint_id=UPDATE_AVAILABLE.id, value=True, quality="good", source_adapter="KNX")
    for event in (confirmation, telegram, telegram, telegram):
        await bus.publish(event)
    levels = [r.levelname for r in caplog.records if "read-only system DataPoint" in r.getMessage()]
    assert levels == ["DEBUG", "WARNING", "DEBUG", "DEBUG"]


async def test_bus_delivers_backend_writes_and_ordinary_datapoints():
    bus = EventBus()
    handler = AsyncMock()
    bus.subscribe(DataValueEvent, handler)
    await bus.publish(DataValueEvent(datapoint_id=UPDATE_AVAILABLE.id, value=True, quality="good", source_adapter=SYSTEM_SOURCE_ADAPTER))
    await bus.publish(DataValueEvent(datapoint_id=uuid.uuid4(), value=1, quality="good", source_adapter="api"))
    assert handler.await_count == 2


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


async def test_load_creates_each_system_datapoint_once(db, registry):
    rows = await db.fetchall("SELECT * FROM datapoints")
    assert {row["id"] for row in rows} == {str(i) for i in SYSTEM_DATAPOINT_IDS}
    channel = registry.get(UPDATE_CHANNEL.id)
    assert (channel.name, channel.data_type, channel.tags, channel.persist_value, channel.record_history) == (
        "OBS Update Channel",
        "STRING",
        ["system"],
        False,
        False,
    )
    # A second load (restart, database restore) does not duplicate anything.
    again = DataPointRegistry(db=db, mqtt_client=AsyncMock(), event_bus=AsyncMock())
    assert await again.load_from_db() == len(SYSTEM_DATAPOINTS)
    assert len(await db.fetchall("SELECT id FROM datapoints")) == len(SYSTEM_DATAPOINTS)


@pytest.mark.parametrize(
    "tampering",
    [
        "name='Renamed'",
        "data_type='FLOAT'",
        "persist_value=1",
        "external_write_enabled=1",
    ],
)
async def test_load_restores_a_tampered_definition(db, registry, tampering, caplog):
    await db.execute_and_commit(f"UPDATE datapoints SET {tampering} WHERE id=?", (str(LATEST_VERSION.id),))
    reloaded = DataPointRegistry(db=db, mqtt_client=AsyncMock(), event_bus=AsyncMock())
    await reloaded.load_from_db()
    dp = reloaded.get(LATEST_VERSION.id)
    assert (dp.name, dp.data_type, dp.persist_value, dp.external_write_enabled) == ("OBS Latest Version", "STRING", False, False)
    assert "restored definition" in caplog.text


async def test_registry_refuses_update_and_delete(registry):
    with pytest.raises(SystemDataPointError):
        await registry.update(UPDATE_CHANNEL.id, DataPointUpdate(name="x"))
    with pytest.raises(SystemDataPointError):
        await registry.delete(UPDATE_CHANNEL.id)
    assert registry.get(UPDATE_CHANNEL.id).name == "OBS Update Channel"


async def test_registry_still_updates_and_deletes_user_datapoints(registry):
    dp = await registry.create(DataPointCreate(name="User", data_type="FLOAT"))
    assert (await registry.update(dp.id, DataPointUpdate(name="Renamed"))).name == "Renamed"
    await registry.delete(dp.id)
    assert registry.get(dp.id) is None


# ---------------------------------------------------------------------------
# DataPoints API
# ---------------------------------------------------------------------------


@pytest.fixture
def api_registry(registry, monkeypatch):
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)
    return registry


def _assert_read_only(exc_info):
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "System DataPoint is read-only"


async def test_enrich_flags_system_datapoints(api_registry):
    assert dp_api._enrich(api_registry.get(UPDATE_CHANNEL.id)).system is True
    user_dp = await api_registry.create(DataPointCreate(name="User"))
    assert dp_api._enrich(user_dp).system is False


async def test_patch_is_refused(api_registry, db):
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.update_datapoint(UPDATE_CHANNEL.id, DataPointUpdate(name="Mine"), request=None, _user="admin", db=db)
    _assert_read_only(exc_info)


async def test_patch_value_is_refused(api_registry, db):
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.update_datapoint(UPDATE_AVAILABLE.id, DataPointUpdate(value=True), request=None, _user="admin", db=db)
    _assert_read_only(exc_info)


async def test_delete_is_refused(api_registry):
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.delete_datapoint(UPDATE_CHANNEL.id, _user="admin")
    _assert_read_only(exc_info)


async def test_duplicate_is_refused(api_registry, db):
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.duplicate_datapoint(UPDATE_CHANNEL.id, dp_api.DataPointDuplicateIn(name="Copy"), request=None, _user="admin", db=db)
    _assert_read_only(exc_info)


async def test_value_write_is_refused_even_for_admins(api_registry, db):
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.write_value(UPDATE_AVAILABLE.id, dp_api.WriteValueIn(value=True), request=MagicMock(), user="admin", db=db)
    _assert_read_only(exc_info)


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dp_id", "adapter_type", "direction", "allowed"),
    [
        (UPDATE_AVAILABLE.id, "KNX", "DEST", True),
        (str(UPDATE_AVAILABLE.id), "MESSAGE", "SOURCE", True),
        (UPDATE_AVAILABLE.id, "KNX", "SOURCE", False),
        (UPDATE_AVAILABLE.id, "MQTT", "BOTH", False),
        (uuid.uuid4(), "KNX", "SOURCE", True),
    ],
)
def test_system_datapoints_only_feed_bindings(dp_id, adapter_type, direction, allowed):
    if allowed:
        bindings_api._ensure_system_datapoint_binding_allowed(dp_id, adapter_type, direction)
        return
    with pytest.raises(HTTPException) as exc_info:
        bindings_api._ensure_system_datapoint_binding_allowed(dp_id, adapter_type, direction)
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# MQTT dp/{uuid}/set
# ---------------------------------------------------------------------------


async def test_mqtt_set_on_a_system_datapoint_is_ignored(registry, caplog):
    db = SimpleNamespace(fetchall=AsyncMock())
    bus = SimpleNamespace(publish=AsyncMock())
    router = WriteRouter(db, registry, event_bus=bus)
    await router.handle(UPDATE_AVAILABLE.id, "true")
    db.fetchall.assert_not_awaited()
    bus.publish.assert_not_awaited()
    assert "read-only system DataPoint" in caplog.text


# ---------------------------------------------------------------------------
# Config reset / restore
# ---------------------------------------------------------------------------


def test_forget_user_datapoints_keeps_the_system_ones():
    user_id = uuid.uuid4()
    reg = SimpleNamespace(
        _points={UPDATE_CHANNEL.id: object(), user_id: object()},
        _values={UPDATE_CHANNEL.id: ValueState(), user_id: ValueState()},
    )
    config_api._forget_user_datapoints(reg)
    assert set(reg._points) == {UPDATE_CHANNEL.id}
    assert set(reg._values) == {UPDATE_CHANNEL.id}


@pytest.mark.parametrize("variant", ["missing", "ok", "failing"])
async def test_db_restore_reloads_the_update_status(monkeypatch, tmp_path, variant, caplog):
    src_path = tmp_path / "upload.sqlite"
    conn = sqlite3.connect(str(src_path))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()

    settings = MagicMock()
    settings.database.path = str(tmp_path / "target.sqlite")
    monkeypatch.setattr("obs.config.get_settings", lambda: settings)

    reg = MagicMock()
    reg._points = {}
    reg._values = {}
    reg.load_from_db = AsyncMock()
    monkeypatch.setattr(config_api, "get_registry", lambda: reg)

    service = None
    if variant != "missing":
        service = SimpleNamespace(keep_after_restore=AsyncMock(side_effect=RuntimeError("db gone") if variant == "failing" else None))

    lifecycle = MagicMock()
    lifecycle.disconnect = AsyncMock()
    lifecycle.connect = AsyncMock()
    lifecycle_cm = MagicMock()
    lifecycle_cm.__aenter__ = AsyncMock(return_value=lifecycle)
    lifecycle_cm.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock()
    db.exclusive_lifecycle = MagicMock(return_value=lifecycle_cm)

    fake_file = MagicMock()
    fake_file.read = AsyncMock(return_value=src_path.read_bytes())

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.logic.manager.get_logic_manager", return_value=SimpleNamespace(stop=AsyncMock(), start=AsyncMock())),
        patch("obs.update.service.get_optional_update_service", return_value=service),
        patch.object(config_api, "AuditLogWriter", return_value=SimpleNamespace(write_contract=AsyncMock())),
    ):
        result = await config_api.import_db(file=fake_file, _admin="admin", db=db)

    assert result["ok"] is True
    if service is not None:
        service.keep_after_restore.assert_awaited_once()
    assert ("Update status reload after DB restore failed" in caplog.text) is (variant == "failing")


async def test_config_export_leaves_out_the_update_settings(db, registry, monkeypatch):
    monkeypatch.setattr(config_api, "get_registry", lambda: registry)
    for key, value in (("update.channel", "nightly"), ("update.check_state", "{}"), ("language", "en")):
        await db.execute_and_commit(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    export = await config_api.export_config(_user="admin", db=db)
    keys = {s.key for s in export.app_settings}
    assert "language" in keys
    assert not any(key.startswith("update.") for key in keys)


async def test_knx_import_leaves_a_system_datapoint_and_its_dest_binding_alone(db, registry, monkeypatch):
    from obs.api.v1 import knxproj as knxproj_api

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    now = "2026-10-17T00:00:00+00:00"
    await db.execute_and_commit(
        "INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        ("inst-knx", "KNX", "KNX-Test", "{}", 0, now, now),
    )
    user_dp = await registry.create(DataPointCreate(name="Mine", data_type="FLOAT"))
    for binding_id, dp_id, ga in (
        ("b-sys", str(LATEST_VERSION.id), "1/1/1"),
        ("b-sys-shared", str(CURRENT_VERSION.id), "1/1/3"),
        ("b-user-shared", str(user_dp.id), "1/1/3"),
        ("b-no-ga", str(user_dp.id), ""),
    ):
        await db.execute_and_commit(
            "INSERT INTO adapter_bindings (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (binding_id, dp_id, "KNX", "inst-knx", "DEST", f'{{"group_address": "{ga}"}}', 1, now, now),
        )
    records = [
        SimpleNamespace(address="1/1/1", name="Hijack", dpt="9.001"),
        SimpleNamespace(address="1/1/2", name="Fresh", dpt="9.001"),
        SimpleNamespace(address="1/1/3", name="Shared", dpt="9.001"),
    ]
    created, updated, skipped_system = await knxproj_api._bulk_import_datapoints(records, "KNX-Test", "SOURCE", db, now)
    # The GA OBS sends its own value to is skipped: no second DataPoint that
    # would read it back, and the skip is counted so the import can report it
    # instead of leaving the missing DataPoint unexplained. A user DataPoint on
    # a shared GA is updated as usual.
    assert (created, updated, skipped_system) == (1, 1, 1)
    ga_1 = await db.fetchall("SELECT id FROM adapter_bindings WHERE config LIKE '%1/1/1%'")
    assert [row["id"] for row in ga_1] == ["b-sys"]
    assert (await db.fetchone("SELECT name FROM datapoints WHERE id=?", (str(user_dp.id),)))["name"] == "Shared"
    row = await db.fetchone("SELECT name, data_type FROM datapoints WHERE id=?", (str(LATEST_VERSION.id),))
    assert (row["name"], row["data_type"]) == ("OBS Latest Version", "STRING")
    binding = await db.fetchone("SELECT direction FROM adapter_bindings WHERE id='b-sys'")
    assert binding["direction"] == "DEST"
    assert registry.get(LATEST_VERSION.id).name == "OBS Latest Version"


@pytest.mark.parametrize(
    ("source", "level"),
    [(SYSTEM_SOURCE_ADAPTER, "DEBUG"), ("KNX", "WARNING")],
)
async def test_write_router_logs_a_missing_system_result_quietly(registry, caplog, source, level):
    import logging

    caplog.set_level(logging.DEBUG, logger="obs.core.write_router")
    router = WriteRouter(SimpleNamespace(fetchall=AsyncMock()), registry, event_bus=SimpleNamespace(publish=AsyncMock()))
    await router.handle_value_event(DataValueEvent(datapoint_id=LATEST_VERSION.id, value=None, quality="uncertain", source_adapter=source))
    (record,) = [r for r in caplog.records if "skip DataValueEvent propagation" in r.getMessage()]
    assert record.levelname == level


async def test_app_maps_the_registry_guard_to_403():
    import json as _json

    from obs.main import _system_datapoint_error_handler, create_app

    assert create_app().exception_handlers[SystemDataPointError] is _system_datapoint_error_handler
    response = await _system_datapoint_error_handler(None, SystemDataPointError(UPDATE_CHANNEL.id))
    assert response.status_code == 403
    assert _json.loads(response.body) == {"detail": "System DataPoint is read-only"}


def _value_state(value, quality):
    state = ValueState()
    state.value, state.quality = value, quality
    return state


@pytest.mark.parametrize(
    ("dp_id", "state", "written"),
    [
        (CURRENT_VERSION.id, _value_state("2026.10.0", "good"), True),
        (CURRENT_VERSION.id, _value_state(None, "good"), False),
        (LATEST_VERSION.id, _value_state(None, "uncertain"), False),
        (LATEST_VERSION.id, None, False),
        (uuid.uuid4(), _value_state("x", "good"), False),
    ],
)
async def test_new_binding_on_a_system_datapoint_gets_the_current_value(dp_id, state, written):
    router = WriteRouter(AsyncMock(), SimpleNamespace(get_value=lambda _id: state))
    router.write_binding = AsyncMock()
    binding_id = uuid.uuid4()
    assert await router.resend_system_value(dp_id, binding_id) is written
    if written:
        # Only the new binding is written; no value event re-fires other consumers.
        router.write_binding.assert_awaited_once_with(dp_id, binding_id, "2026.10.0")
    else:
        router.write_binding.assert_not_awaited()


async def test_binding_routes_delegate_the_resend_to_the_write_router():
    router = SimpleNamespace(resend_system_value=AsyncMock(return_value=True))
    binding_id = uuid.uuid4()
    with patch("obs.core.write_router.get_write_router", return_value=router):
        await bindings_api._resend_system_value(CURRENT_VERSION.id, str(binding_id))
    router.resend_system_value.assert_awaited_once_with(CURRENT_VERSION.id, binding_id)


async def test_write_binding_writes_only_the_named_binding(db, registry, monkeypatch):
    from obs.adapters import registry as adapter_registry

    now = "2026-10-17T00:00:00+00:00"
    for binding_id in ("b-new", "b-old"):
        await db.execute_and_commit(
            "INSERT INTO adapter_bindings (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid5(uuid.NAMESPACE_OID, binding_id)), str(LATEST_VERSION.id), "MQTT", None, "DEST", '{"topic": "t"}', 1, now, now),
        )
    # Send-on-change and a throttle on both: a migrated or re-enabled binding
    # still holds the value it was sent before.
    await db.execute_and_commit("UPDATE adapter_bindings SET send_on_change=1, send_throttle_ms=3600000")
    written = []
    instance = SimpleNamespace(write=AsyncMock(side_effect=lambda binding, value: written.append((binding.id, value))))
    monkeypatch.setattr(adapter_registry, "get_instance", lambda _type: instance)
    target = uuid.uuid5(uuid.NAMESPACE_OID, "b-new")
    router = WriteRouter(db, registry)
    await router.write_binding(LATEST_VERSION.id, target, "2026.10.0")
    await router.write_binding(LATEST_VERSION.id, target, "2026.10.0")
    assert written == [(target, "2026.10.0")] * 2


# ---------------------------------------------------------------------------
# Re-sending the constant system values to a (re)connected adapter instance
# ---------------------------------------------------------------------------


async def _insert_binding(db, binding_id, dp_id, *, instance_id, direction="DEST", enabled=1, adapter_type="MQTT"):
    now = "2026-10-17T00:00:00+00:00"
    await db.execute_and_commit(
        "INSERT INTO adapter_bindings (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (str(binding_id), str(dp_id), adapter_type, str(instance_id), direction, '{"topic": "t"}', enabled, now, now),
    )


async def test_reconnected_instance_gets_every_system_value_it_writes(db, registry):
    """The channel and the installed version never change, so a dropped write is never retried."""
    instance_id, other_instance = uuid.uuid4(), uuid.uuid4()
    wanted = {
        uuid.uuid4(): UPDATE_CHANNEL.id,  # DEST on this instance
        uuid.uuid4(): CURRENT_VERSION.id,
    }
    for binding_id, dp_id in wanted.items():
        await _insert_binding(db, binding_id, dp_id, instance_id=instance_id)
    both_id = uuid.uuid4()
    await _insert_binding(db, both_id, LATEST_VERSION.id, instance_id=instance_id, direction="BOTH")
    # None of these may be written: wrong instance, disabled, observer-only, SOURCE, user DataPoint.
    user_dp = await registry.create(DataPointCreate(name="User", data_type="STRING"))
    for dp_id, kwargs in (
        (UPDATE_CHANNEL.id, {"instance_id": other_instance}),
        (UPDATE_CHANNEL.id, {"instance_id": instance_id, "enabled": 0}),
        (UPDATE_CHANNEL.id, {"instance_id": instance_id, "adapter_type": "MESSAGE", "direction": "SOURCE"}),
        (LAST_CHECK.id, {"instance_id": instance_id, "direction": "SOURCE"}),
        (user_dp.id, {"instance_id": instance_id}),
    ):
        await _insert_binding(db, uuid.uuid4(), dp_id, **kwargs)

    registry.get_value(UPDATE_CHANNEL.id).update("stable", "good")
    registry.get_value(CURRENT_VERSION.id).update("2026.10.0", "good")
    registry.get_value(user_dp.id).update("user", "good")
    # LATEST_VERSION has no result yet, so its BOTH binding is matched but not written.

    router = WriteRouter(db, registry)
    router.write_binding = AsyncMock()
    assert await router.resend_system_bindings(instance_id) == 2
    assert {call.args[:2] for call in router.write_binding.await_args_list} == {(dp, b) for b, dp in wanted.items()}
    assert both_id not in {call.args[1] for call in router.write_binding.await_args_list}


async def test_resend_to_an_instance_without_system_bindings_writes_nothing(db, registry):
    router = WriteRouter(db, registry)
    router.write_binding = AsyncMock()
    assert await router.resend_system_bindings(uuid.uuid4()) == 0
    router.write_binding.assert_not_awaited()


async def test_a_failing_resend_does_not_stop_the_remaining_bindings(db, registry, caplog):
    instance_id = uuid.uuid4()
    first, second = sorted([uuid.uuid4(), uuid.uuid4()])
    await _insert_binding(db, first, UPDATE_CHANNEL.id, instance_id=instance_id)
    await _insert_binding(db, second, CURRENT_VERSION.id, instance_id=instance_id)
    registry.get_value(UPDATE_CHANNEL.id).update("stable", "good")
    registry.get_value(CURRENT_VERSION.id).update("2026.10.0", "good")

    router = WriteRouter(db, registry)
    calls = []

    async def _write(dp_id, binding_id, value):
        calls.append(binding_id)
        if len(calls) == 1:
            raise RuntimeError("adapter exploded")

    router.write_binding = _write
    assert await router.resend_system_bindings(instance_id) == 1
    assert len(calls) == 2
    assert "could not re-send system value" in caplog.text


async def test_a_user_datapoint_binding_never_reaches_the_write_router():
    """The resend is for system DataPoints only — it must not require a WriteRouter."""
    with patch("obs.core.write_router.get_write_router", side_effect=AssertionError("must not be called")):
        await bindings_api._resend_system_value(uuid.uuid4(), str(uuid.uuid4()))
