"""Home Assistant browse/import routes: authorization, plan, mutation, redaction (#1392)."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from obs.adapters.base import AdapterDelegationCapability
from obs.adapters.homeassistant.adapter import HaNotConnectedError
from obs.adapters.homeassistant.importer import HaImportRequest, HaImportSelection
from obs.api.auth import Principal
from obs.api.v1 import adapters as adapters_api
from obs.api.v1.redaction import REDACTED
from obs.api.v1.route_classification_registry import ROUTE_CLASSIFICATIONS
from obs.db.database import Database

NOW = "2026-10-26T00:00:00+00:00"
ADMIN = Principal(subject="admin", type="user", is_admin=True)
ALICE = Principal(subject="alice", type="user", is_admin=False)

ENTITIES = [
    {
        "entity_id": "lock.shed",
        "name": "Shed",
        "domain": "lock",
        "state": "locked",
        "unit": None,
        "device_class": None,
        "available": True,
        "attributes": {},
        "platform": "nuki",
        "device": None,
        "area": None,
        "ha_uid": None,
    },
    {
        "entity_id": "sensor.temp",
        "name": "Temp",
        "domain": "sensor",
        "state": "21",
        "unit": "°C",
        "device_class": None,
        "available": True,
        "attributes": {},
        "platform": None,
        "device": None,
        "area": "Bad",
        "ha_uid": None,
    },
]


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _instance(db: Database, adapter_type: str = "HOME_ASSISTANT", config: dict | None = None) -> uuid.UUID:
    instance_id = uuid.uuid4()
    await db.execute_and_commit(
        "INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at) VALUES (?, ?, 'HA', ?, 0, ?, ?)",
        (str(instance_id), adapter_type, json.dumps(config or {}), NOW, NOW),
    )
    return instance_id


async def _grant(db: Database, instance_id: uuid.UUID, role: str = "operator") -> None:
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', 'alice', 'adapter_instance', ?, ?, 'allow')""",
        (str(instance_id), role),
    )


async def _binding(db: Database, instance_id: uuid.UUID, config: dict) -> str:
    dp_id, binding_id = str(uuid.uuid4()), str(uuid.uuid4())
    await db.execute_and_commit(
        """INSERT INTO datapoints (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
           VALUES (?, 'x', 'BOOLEAN', NULL, '[]', ?, NULL, 1, 1, ?, ?)""",
        (dp_id, f"dp/{dp_id}", NOW, NOW),
    )
    await db.execute_and_commit(
        """INSERT INTO adapter_bindings (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)
           VALUES (?, ?, 'HOME_ASSISTANT', ?, 'BOTH', ?, 1, ?, ?)""",
        (binding_id, dp_id, str(instance_id), json.dumps(config), NOW, NOW),
    )
    return binding_id


def _live(monkeypatch, entities=ENTITIES, *, side_effect=None):
    browse = AsyncMock(return_value={"entities": entities, "metadata_available": False}, side_effect=side_effect)
    services = AsyncMock(return_value=[{"domain": "lock", "services": []}], side_effect=side_effect)
    instance = SimpleNamespace(browse_entities=browse, browse_services=services)
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: instance)
    return instance


def _request(*selections) -> HaImportRequest:
    return HaImportRequest(selections=[HaImportSelection(**s) for s in selections])


async def _browse(instance_id, principal=ADMIN, db=None, **filters):
    params = {"q": "", "domain": "", "area": "", "device": "", "platform": "", "availability": "", "offset": 0, "limit": 100}
    params.update(filters)
    return await adapters_api.ha_browse_entities(instance_id, **params, _user=principal, db=db)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def test_profiles_catalog():
    catalog = await adapters_api.ha_profiles(_user=ALICE)
    assert any(p["id"] == "lock.boolean_locked" for p in catalog)


async def test_browse_requires_instance_write_and_link_capability(monkeypatch, db):
    instance_id = await _instance(db)
    _live(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await _browse(instance_id, ALICE, db)
    assert exc_info.value.status_code == 403
    await _grant(db, instance_id)
    # HOME_ASSISTANT declares no delegation: an operator grant alone is not enough
    with pytest.raises(HTTPException) as exc_info:
        await _browse(instance_id, ALICE, db)
    assert exc_info.value.status_code == 403
    monkeypatch.setattr(adapters_api.adapter_registry, "supports_delegation", lambda t, c: c is AdapterDelegationCapability.LINK_BINDING)
    page = await _browse(instance_id, ALICE, db)
    assert page.total == 2
    # ...but importing additionally needs CREATE_DATAPOINT
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.ha_import_preview(instance_id, _request(), _user=ALICE, db=db)
    assert exc_info.value.status_code == 403


async def test_browse_wrong_type_missing_and_offline(monkeypatch, db):
    other = await _instance(db, "IOBROKER")
    with pytest.raises(HTTPException) as exc_info:
        await _browse(other, db=db)
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        await _browse(uuid.uuid4(), db=db)
    assert exc_info.value.status_code == 404
    instance_id = await _instance(db)
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: None)
    with pytest.raises(HTTPException) as exc_info:
        await _browse(instance_id, db=db)
    assert exc_info.value.status_code == 503


@pytest.mark.parametrize("error", [HaNotConnectedError(), RuntimeError("token=secret")])
async def test_browse_live_failures_are_503_without_details(monkeypatch, db, error):
    instance_id = await _instance(db)
    _live(monkeypatch, side_effect=error)
    with pytest.raises(HTTPException) as exc_info:
        await _browse(instance_id, db=db)
    assert exc_info.value.status_code == 503
    assert "secret" not in str(exc_info.value.detail)


async def test_services_route(monkeypatch, db):
    instance_id = await _instance(db)
    _live(monkeypatch)
    assert await adapters_api.ha_browse_services(instance_id, _user=ADMIN, db=db) == [{"domain": "lock", "services": []}]
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.ha_browse_services(instance_id, _user=ALICE, db=db)
    assert exc_info.value.status_code == 403
    _live(monkeypatch, side_effect=RuntimeError("x"))
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.ha_browse_services(instance_id, _user=ADMIN, db=db)
    assert exc_info.value.status_code == 503


async def test_browse_filters_and_metadata_flag(monkeypatch, db):
    instance_id = await _instance(db)
    _live(monkeypatch)
    page = await _browse(instance_id, db=db, domain="lock")
    assert [i["entity_id"] for i in page.items] == ["lock.shed"]
    assert page.metadata_available is False
    assert page.facets["domain"] == ["lock", "sensor"]


# ---------------------------------------------------------------------------
# Preview / import
# ---------------------------------------------------------------------------


async def test_preview_does_not_mutate(monkeypatch, db):
    instance_id = await _instance(db)
    _live(monkeypatch)
    registry = SimpleNamespace(create=AsyncMock())
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    result = await adapters_api.ha_import_preview(instance_id, _request({"entity_id": "lock.shed"}), _user=ADMIN, db=db)
    assert [c.status for c in result.preview] == ["new"]
    assert result.preview[0].mapping["write"]["false"] == {"domain": "lock", "name": "unlock"}
    registry.create.assert_not_awaited()
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM adapter_bindings"))["n"] == 0


async def test_import_creates_only_new_and_reports_skips(monkeypatch, db):
    instance_id = await _instance(db)
    await _binding(db, instance_id, {"entity_id": "sensor.temp", "profile": "sensor.value"})
    _live(monkeypatch)
    dp_id = uuid.uuid4()
    registry = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id=dp_id)), delete=AsyncMock())
    create_binding = AsyncMock()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.bindings.create_binding", create_binding)
    result = await adapters_api.ha_import_entities(
        instance_id,
        _request({"entity_id": "lock.shed"}, {"entity_id": "sensor.temp"}, {"entity_id": "sensor.ghost"}),
        _user=ADMIN,
        db=db,
    )
    assert (result.created_datapoints, result.created_bindings, result.skipped_existing, result.skipped_unsupported) == (1, 1, 1, 1)
    created = registry.create.await_args.args[0]
    assert (created.name, created.data_type, created.tags) == ("Shed", "BOOLEAN", ["homeassistant", "lock", "nuki"])
    binding = create_binding.await_args.args[1]
    assert binding.direction == "BOTH"
    assert binding.config == {"entity_id": "lock.shed", "profile": "lock.boolean_locked"}
    assert create_binding.await_args.args[2] is ADMIN


async def test_import_skips_conflicts(monkeypatch, db):
    instance_id = await _instance(db)
    entities = [{**ENTITIES[0], "ha_uid": "a" * 32}]
    await _binding(db, instance_id, {"entity_id": "lock.old", "profile": "lock.boolean_locked", "ha_uid": "a" * 32})
    await _binding(db, instance_id, {})  # not an HA mapping: ignored by the plan
    _live(monkeypatch, entities)
    registry = SimpleNamespace(create=AsyncMock())
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    result = await adapters_api.ha_import_entities(instance_id, _request({"entity_id": "lock.shed"}), _user=ADMIN, db=db)
    assert result.skipped_conflicts == 1
    assert result.preview[0].reason == "entityRenamed"
    registry.create.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "message"),
    [(HTTPException(422, "Ungültige Konfiguration"), "lock.shed: Ungültige Konfiguration"), (RuntimeError("boom"), "lock.shed: boom")],
)
async def test_failed_binding_rolls_back_datapoint(monkeypatch, db, error, message):
    instance_id = await _instance(db)
    _live(monkeypatch)
    dp_id = uuid.uuid4()
    registry = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id=dp_id)), delete=AsyncMock())
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.bindings.create_binding", AsyncMock(side_effect=error))
    result = await adapters_api.ha_import_entities(instance_id, _request({"entity_id": "lock.shed"}), _user=ADMIN, db=db)
    assert result.errors == [message]
    assert result.created_datapoints == 0
    registry.delete.assert_awaited_once_with(dp_id)


async def test_failed_datapoint_creation_needs_no_rollback(monkeypatch, db):
    instance_id = await _instance(db)
    _live(monkeypatch)
    registry = SimpleNamespace(create=AsyncMock(side_effect=ValueError("dup")), delete=AsyncMock())
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    result = await adapters_api.ha_import_entities(instance_id, _request({"entity_id": "lock.shed"}), _user=ADMIN, db=db)
    assert result.errors == ["lock.shed: dup"]
    registry.delete.assert_not_awaited()


async def test_delegated_import_grants_the_new_datapoint(monkeypatch, db):
    instance_id = await _instance(db)
    await _grant(db, instance_id)
    _live(monkeypatch)
    monkeypatch.setattr(adapters_api.adapter_registry, "supports_delegation", lambda t, c: True)
    dp_id = uuid.uuid4()
    registry = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id=dp_id)), delete=AsyncMock())
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.bindings.create_binding", AsyncMock())
    result = await adapters_api.ha_import_entities(instance_id, _request({"entity_id": "lock.shed"}), _user=ALICE, db=db)
    assert result.created_bindings == 1
    grant = await db.fetchone(
        "SELECT role FROM authz_node_roles WHERE principal_id='alice' AND node_type='datapoint' AND node_id=?",
        (str(dp_id),),
    )
    assert grant["role"] == "operator"


def test_routes_are_classified_and_principal_scoped():
    routes = {(r.path, m): r for r in adapters_api.router.routes if isinstance(r, APIRoute) for m in r.methods}
    for path in ("/instances/{instance_id}/homeassistant/import-preview", "/instances/{instance_id}/homeassistant/import"):
        route = routes[(path, "POST")]
        assert any(d.call is adapters_api.get_current_principal for d in route.dependant.dependencies)
        assert ROUTE_CLASSIFICATIONS[("POST", f"/api/v1/adapters{path}")] == "config_mutation"
    assert ROUTE_CLASSIFICATIONS[("GET", "/api/v1/adapters/instances/{instance_id}/homeassistant/entities")] == "read_live"
    assert ROUTE_CLASSIFICATIONS[("GET", "/api/v1/adapters/instances/{instance_id}/homeassistant/services")] == "read_live"
    assert ROUTE_CLASSIFICATIONS[("GET", "/api/v1/adapters/homeassistant/profiles")] == "read_live"


# ---------------------------------------------------------------------------
# Token redaction
# ---------------------------------------------------------------------------


async def test_token_hidden_from_delegated_readers_only(monkeypatch, db):
    instance_id = await _instance(db, config={"host": "ha", "token": "LLAT-secret"})
    await _grant(db, instance_id, "guest")
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: None)
    reader_list = await adapters_api.list_instances(_user=ALICE, db=db)
    assert reader_list[0].config == {"host": "ha", "token": REDACTED}
    reader_one = await adapters_api.get_instance(instance_id, _user=ALICE, db=db)
    assert reader_one.config["token"] == REDACTED
    admin_one = await adapters_api.get_instance(instance_id, _user=ADMIN, db=db)
    assert admin_one.config["token"] == "LLAT-secret"


async def test_redacted_token_round_trip_keeps_the_stored_token(monkeypatch, db):
    instance_id = await _instance(db, config={"host": "ha", "token": "LLAT-secret"})
    restart = AsyncMock()
    monkeypatch.setattr(adapters_api.adapter_registry, "restart_instance", restart)
    monkeypatch.setattr("obs.core.event_bus.get_event_bus", lambda: None)
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: None)
    out = await adapters_api.update_instance(
        instance_id,
        adapters_api.AdapterInstanceUpdate(config={"host": "ha2", "token": REDACTED}, enabled=True),
        _user=ADMIN,
        db=db,
    )
    row = await db.fetchone("SELECT config FROM adapter_instances WHERE id=?", (str(instance_id),))
    assert json.loads(row["config"]) == {"host": "ha2", "token": "LLAT-secret"}
    assert out.config["token"] == "LLAT-secret"
    # a real new token replaces it
    await adapters_api.update_instance(instance_id, adapters_api.AdapterInstanceUpdate(config={"host": "ha2", "token": "new"}), _user=ADMIN, db=db)
    row = await db.fetchone("SELECT config FROM adapter_instances WHERE id=?", (str(instance_id),))
    assert json.loads(row["config"])["token"] == "new"


async def test_connection_test_uses_the_stored_token_for_a_redacted_one(monkeypatch, db):
    instance_id = await _instance(db, config={"host": "ha", "token": "LLAT-secret"})
    seen = {}

    class Probe:
        config_schema = adapters_api.adapter_registry.get_class("HOME_ASSISTANT").config_schema

        def __init__(self, event_bus, config):
            seen.update(config)
            self.connected = True

        async def connect(self):
            pass

        async def disconnect(self):
            pass

    monkeypatch.setattr(adapters_api.adapter_registry, "get_class", lambda _t: Probe)
    result = await adapters_api.test_instance(instance_id, adapters_api.TestRequest(config={"host": "ha", "token": REDACTED}), _user=ADMIN, db=db)
    assert result.success is True
    assert seen["token"] == "LLAT-secret"


async def test_legacy_type_config_hides_and_keeps_the_token(db):
    await adapters_api.update_adapter_config("HOME_ASSISTANT", adapters_api.ConfigPatch(config={"host": "h", "token": "s1"}), _user="admin", db=db)
    read = await adapters_api.get_adapter_config("HOME_ASSISTANT", _user="viewer", db=db)
    assert read.config["token"] == REDACTED
    written = await adapters_api.update_adapter_config(
        "HOME_ASSISTANT", adapters_api.ConfigPatch(config={"host": "h2", "token": REDACTED}), _user="admin", db=db
    )
    assert written.config == {"host": "h2", "token": "s1"}
    row = await db.fetchone("SELECT config FROM adapter_configs WHERE adapter_type='HOME_ASSISTANT'")
    assert json.loads(row["config"])["token"] == "s1"


def test_restore_redacted_token_only_touches_home_assistant():
    assert adapters_api._restore_redacted_token("MQTT", {"token": "x"}, {"token": REDACTED}) == {"token": REDACTED}
    assert adapters_api._restore_redacted_token("HOME_ASSISTANT", {}, {"token": REDACTED}) == {"token": ""}


async def test_import_reloads_the_adapter_once(monkeypatch, db):
    instance_id = await _instance(db)
    entities = [{**ENTITIES[0], "entity_id": f"lock.l{i}", "name": f"L{i}"} for i in range(5)]
    _live(monkeypatch, entities)
    registry = SimpleNamespace(create=AsyncMock(side_effect=lambda spec: SimpleNamespace(id=uuid.uuid4())), delete=AsyncMock())
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    reloads = AsyncMock()

    async def create_binding(dp_id, body, principal, db_):
        await adapters_api.adapter_registry.reload_instance_bindings(str(body.adapter_instance_id), db_)

    monkeypatch.setattr("obs.api.v1.bindings.create_binding", create_binding)
    monkeypatch.setattr(adapters_api.adapter_registry, "reload_instance_from_rows", reloads)
    monkeypatch.setitem(adapters_api.adapter_registry._instances, str(instance_id), object())
    result = await adapters_api.ha_import_entities(instance_id, _request(*({"entity_id": e["entity_id"]} for e in entities)), _user=ADMIN, db=db)
    assert result.created_bindings == 5
    reloads.assert_awaited_once()


async def test_rollback_failure_is_logged_not_raised(monkeypatch, db, caplog):
    instance_id = await _instance(db)
    _live(monkeypatch)
    registry = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())), delete=AsyncMock(side_effect=RuntimeError("db gone")))
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    monkeypatch.setattr("obs.api.v1.bindings.create_binding", AsyncMock(side_effect=RuntimeError("boom")))
    result = await adapters_api.ha_import_entities(instance_id, _request({"entity_id": "lock.shed"}), _user=ADMIN, db=db)
    assert result.errors == ["lock.shed: boom"]
    assert "verwaistes Objekt" in caplog.text


async def test_iobroker_import_shares_the_helper_and_omits_unknown_source_types(monkeypatch, db):
    instance_id = await _instance(db, "IOBROKER")
    items = [
        adapters_api.IoBrokerImportItem(state_id="a.0.x", name="X", data_type="DATE", direction="SOURCE", tags=[]),
        adapters_api.IoBrokerImportItem(state_id="a.0.y", name="Y", data_type="FLOAT", direction="SOURCE", tags=[]),
        adapters_api.IoBrokerImportItem(state_id="a.0.z", name="Z", data_type="FLOAT", direction="SOURCE", tags=[], exists=True),
    ]
    monkeypatch.setattr(adapters_api, "_iobroker_candidates", AsyncMock(return_value=items))
    registry = SimpleNamespace(create=AsyncMock(side_effect=lambda spec: SimpleNamespace(id=uuid.uuid4())), delete=AsyncMock())
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)
    create_binding = AsyncMock()
    monkeypatch.setattr("obs.api.v1.bindings.create_binding", create_binding)
    result = await adapters_api.iobroker_import_states(instance_id, adapters_api.IoBrokerImportRequest(), _user=ADMIN, db=db)
    assert (result.created_datapoints, result.created_bindings, result.skipped_existing) == (2, 2, 1)
    assert [c.args[1].config for c in create_binding.await_args_list] == [{"state_id": "a.0.x"}, {"state_id": "a.0.y", "source_data_type": "float"}]


async def test_failing_deferred_reload_does_not_fail_the_import(monkeypatch, db, caplog):
    instance_id = await _instance(db)
    _live(monkeypatch)
    registry = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())), delete=AsyncMock())
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: registry)

    async def create_binding(dp_id, body, principal, db_):
        await adapters_api.adapter_registry.reload_instance_bindings(str(body.adapter_instance_id), db_)

    monkeypatch.setattr("obs.api.v1.bindings.create_binding", create_binding)
    monkeypatch.setattr(adapters_api.adapter_registry, "reload_instance_from_rows", AsyncMock(side_effect=RuntimeError("reload boom")))
    monkeypatch.setitem(adapters_api.adapter_registry._instances, str(instance_id), object())
    result = await adapters_api.ha_import_entities(instance_id, _request({"entity_id": "lock.shed"}), _user=ADMIN, db=db)
    assert (result.created_bindings, result.errors) == (1, [])
    assert "Deferred binding reload failed" in caplog.text
