"""Unit tests for the WEBHOOK management routes (issue #1256).

Covers the defensive and authorization paths the integration suite cannot
reach through the HTTP API: a stored configuration that predates the current
validation (an imported backup or a hand-edited database), and a non-admin
principal whose grants do not cover every bound DataPoint.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi import HTTPException

from obs.adapters.webhook.adapter import DEFAULT_PATH_PREFIX, BindingStats, RejectionCounters, RejectionReason
from obs.api.auth import Principal
from obs.api.v1 import adapters as adapters_api
from obs.db.database import Database
from obs.models.datapoint import DataPoint

NOW = "2026-10-06T00:00:00+00:00"


class _RegistryStub:
    def __init__(self, datapoints: list[DataPoint]) -> None:
        self._datapoints = datapoints
        self.external_write_lock = asyncio.Lock()

    def all(self) -> list[DataPoint]:
        return list(self._datapoints)

    def get(self, dp_id: uuid.UUID) -> DataPoint | None:
        return next((dp for dp in self._datapoints if dp.id == dp_id), None)


class _InstanceStub:
    def __init__(self, stats: dict[str, BindingStats] | None = None, rejections: RejectionCounters | None = None) -> None:
        self._stats = stats or {}
        self.rejections = rejections or RejectionCounters()

    def stats_for(self, binding_id) -> BindingStats:
        return self._stats.get(str(binding_id), BindingStats())


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _principal(subject: str = "alice", *, is_admin: bool = False) -> Principal:
    return Principal(subject=subject, type="user", is_admin=is_admin)


def _dp(dp_id: uuid.UUID, name: str) -> DataPoint:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return DataPoint(id=dp_id, name=name, data_type="BOOLEAN", created_at=now, updated_at=now)


async def _insert_tree_and_nodes(db: Database) -> None:
    await db.execute_and_commit(
        "INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at) VALUES ('tree', 'tree', '', ?, ?)",
        (NOW, NOW),
    )
    await db.executemany(
        """
        INSERT INTO hierarchy_nodes
            (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
        VALUES (?, 'tree', NULL, ?, '', 0, NULL, ?, ?)
        """,
        [("allowed-room", "allowed-room", NOW, NOW), ("secret-room", "secret-room", NOW, NOW), ("read-room", "read-room", NOW, NOW)],
    )
    await db.commit()


async def _insert_datapoint_row(db: Database, dp_id: uuid.UUID, name: str = "Bell") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO datapoints
            (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, record_history, created_at, updated_at)
        VALUES (?, ?, 'BOOLEAN', NULL, '[]', ?, NULL, 1, 1, ?, ?)
        """,
        (str(dp_id), name, f"obs/test/{dp_id}", NOW, NOW),
    )


async def _insert_datapoint(db: Database, dp: DataPoint, node_id: str) -> None:
    await _insert_datapoint_row(db, dp.id, dp.name)
    await db.execute_and_commit(
        "INSERT INTO hierarchy_datapoint_links (id, node_id, datapoint_id, created_at) VALUES (?, ?, ?, ?)",
        (f"link-{dp.id}", node_id, str(dp.id), NOW),
    )


async def _insert_grant(db: Database, node_id: str, *, node_type: str = "hierarchy", role: str = "guest") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('user', 'alice', ?, ?, ?, 'allow')
        """,
        (node_type, node_id, role),
    )


async def _insert_instance(db: Database, instance_id: uuid.UUID, *, adapter_type: str = "WEBHOOK", config: str = "{}") -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at)
        VALUES (?, ?, 'Hook', ?, 0, ?, ?)
        """,
        (str(instance_id), adapter_type, config, NOW, NOW),
    )


async def _insert_binding(db: Database, *, binding_id: uuid.UUID, dp_id: uuid.UUID, instance_id: uuid.UUID, config: dict) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO adapter_bindings
            (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled, created_at, updated_at)
        VALUES (?, ?, 'WEBHOOK', ?, 'SOURCE', ?, 1, ?, ?)
        """,
        (str(binding_id), str(dp_id), str(instance_id), json.dumps(config), NOW, NOW),
    )


# ---------------------------------------------------------------------------
# Path prefix resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({}, DEFAULT_PATH_PREFIX),
        ({"path_prefix": ""}, DEFAULT_PATH_PREFIX),
        ({"path_prefix": "/iot/trigger"}, "/iot/trigger"),
        ({"path_prefix": "iot/trigger/"}, "/iot/trigger"),
        # An imported backup or a hand-edited row can hold a prefix the current
        # validation would refuse; the listing falls back instead of failing.
        ({"path_prefix": "/api"}, DEFAULT_PATH_PREFIX),
        ({"path_prefix": "/a/b/c/d"}, DEFAULT_PATH_PREFIX),
    ],
)
def test_webhook_path_prefix(stored, expected):
    row = {"config": json.dumps(stored)}
    assert adapters_api._webhook_path_prefix(row) == expected


def test_webhook_call_paths():
    query_form, path_form = adapters_api._webhook_call_paths("/hook", "bell", "tok")
    assert query_form == "/hook/bell?token=tok"
    assert path_form == "/hook/bell/tok"


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


async def test_listing_serves_a_token_only_for_datapoints_the_principal_may_write(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    allowed = _dp(uuid.uuid4(), "Allowed")
    blocked = _dp(uuid.uuid4(), "Blocked")
    read_only = _dp(uuid.uuid4(), "ReadOnly")
    await _insert_tree_and_nodes(db)
    await _insert_datapoint(db, allowed, "allowed-room")
    await _insert_datapoint(db, blocked, "secret-room")
    await _insert_datapoint(db, read_only, "read-room")
    await _insert_grant(db, "allowed-room", role="operator")
    await _insert_grant(db, "read-room", role="guest")
    await _insert_instance(db, instance_id)
    await _insert_grant(db, str(instance_id), node_type="adapter_instance", role="operator")
    for dp, slug in ((allowed, "allowed"), (blocked, "blocked"), (read_only, "readonly")):
        await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=dp.id, instance_id=instance_id, config={"slug": slug, "token": f"t-{slug}"})
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([allowed, blocked, read_only]))

    entries = (await adapters_api.webhook_list_bindings(instance_id, _user=_principal(), db=db)).bindings

    # The token is a bearer secret with write authority: read access to the
    # DataPoint ("readonly") must not be enough to be handed it.
    assert [entry.slug for entry in entries] == ["allowed"]


async def test_listing_skips_a_binding_whose_stored_config_is_invalid(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    good = _dp(uuid.uuid4(), "Good")
    broken = _dp(uuid.uuid4(), "Broken")
    await _insert_instance(db, instance_id)
    await _insert_datapoint_row(db, good.id, good.name)
    await _insert_datapoint_row(db, broken.id, broken.name)
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=good.id, instance_id=instance_id, config={"slug": "good", "token": "t1"})
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=broken.id, instance_id=instance_id, config={"slug": "NOT A SLUG"})
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([good, broken]))

    entries = (await adapters_api.webhook_list_bindings(instance_id, _user=_principal(is_admin=True), db=db)).bindings

    assert [entry.slug for entry in entries] == ["good"]


async def test_listing_reports_a_binding_whose_datapoint_vanished(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    dp_id = uuid.uuid4()
    await _insert_instance(db, instance_id)
    await _insert_datapoint_row(db, dp_id)
    await _insert_binding(db, binding_id=uuid.uuid4(), dp_id=dp_id, instance_id=instance_id, config={"slug": "orphan", "token": "t1"})
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([]))

    entries = (await adapters_api.webhook_list_bindings(instance_id, _user=_principal(is_admin=True), db=db)).bindings

    assert [entry.datapoint_name for entry in entries] == [None]


async def test_listing_reports_statistics_of_the_running_instance(monkeypatch, db: Database):
    import datetime

    instance_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    dp = _dp(uuid.uuid4(), "Bell")
    called_at = datetime.datetime(2026, 10, 6, 7, 30, tzinfo=datetime.UTC)
    await _insert_instance(db, instance_id)
    await _insert_datapoint_row(db, dp.id, dp.name)
    await _insert_binding(db, binding_id=binding_id, dp_id=dp.id, instance_id=instance_id, config={"slug": "bell", "token": "t1"})
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([dp]))
    monkeypatch.setattr(
        adapters_api.adapter_registry,
        "get_instance_by_id",
        lambda _id: _InstanceStub({str(binding_id): BindingStats(call_count=3, publish_count=2, last_called=called_at, last_status=204)}),
    )

    entry = (await adapters_api.webhook_list_bindings(instance_id, _user=_principal(is_admin=True), db=db)).bindings[0]

    assert (entry.call_count, entry.publish_count, entry.last_status) == (3, 2, 204)
    assert entry.last_called == called_at.isoformat()


async def test_overview_reports_the_instance_settings_and_rejections(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    dp = _dp(uuid.uuid4(), "Bell")
    counters = RejectionCounters()
    counters.record(RejectionReason.ADDRESS_BLOCKED, client_ip="127.0.0.1")
    counters.record(RejectionReason.ADDRESS_BLOCKED, client_ip="127.0.0.1")
    await _insert_instance(
        db,
        instance_id,
        config=json.dumps({"path_prefix": "/iot/hook", "rate_limit_per_minute": 120}),
    )
    await _insert_datapoint_row(db, dp.id, dp.name)
    await _insert_binding(
        db,
        binding_id=uuid.uuid4(),
        dp_id=dp.id,
        instance_id=instance_id,
        config={"slug": "bell", "token": "t1", "allowed_networks": ["192.168.1.5"]},
    )
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([dp]))
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: _InstanceStub(rejections=counters))

    overview = await adapters_api.webhook_list_bindings(instance_id, _user=_principal(is_admin=True), db=db)

    assert overview.instance_id == str(instance_id)
    assert overview.running is True
    assert overview.path_prefix == "/iot/hook"
    assert not hasattr(overview, "allowed_networks")
    assert overview.rate_limit_per_minute == 120
    assert overview.trust_forwarded_for is False
    assert overview.rejections.total == 2
    assert overview.rejections.counts == {"address_blocked": 2}
    assert overview.rejections.last_reason == "address_blocked"
    assert overview.rejections.last_client_ip == "127.0.0.1"
    assert overview.rejections.last_at is not None
    assert overview.bindings[0].allowed_networks == ["192.168.1.5/32"]


async def test_overview_of_a_stopped_instance_reports_empty_diagnostics(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id)
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([]))
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: None)

    overview = await adapters_api.webhook_list_bindings(instance_id, _user=_principal(is_admin=True), db=db)

    assert overview.running is False
    assert overview.rejections.total == 0
    assert overview.rejections.last_reason is None


async def test_overview_falls_back_to_defaults_for_an_unparseable_instance_config(monkeypatch, db: Database):
    """An imported backup can hold a config the current validation refuses."""
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id, config=json.dumps({"path_prefix": "/api", "rate_limit_per_minute": 0}))
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([]))
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: None)

    overview = await adapters_api.webhook_list_bindings(instance_id, _user=_principal(is_admin=True), db=db)

    assert overview.path_prefix == DEFAULT_PATH_PREFIX
    assert overview.rate_limit_per_minute == 60


async def test_listing_requires_a_write_grant_on_the_instance(db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id)
    await _insert_grant(db, str(instance_id), node_type="adapter_instance", role="guest")

    with pytest.raises(HTTPException) as excinfo:
        await adapters_api.webhook_list_bindings(instance_id, _user=_principal(), db=db)
    assert excinfo.value.status_code == 403


async def test_listing_rejects_a_missing_or_foreign_instance(db: Database):
    other_id = uuid.uuid4()
    await _insert_instance(db, other_id, adapter_type="SNMP")

    with pytest.raises(HTTPException) as missing:
        await adapters_api.webhook_list_bindings(uuid.uuid4(), _user=_principal(is_admin=True), db=db)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_type:
        await adapters_api.webhook_list_bindings(other_id, _user=_principal(is_admin=True), db=db)
    assert wrong_type.value.status_code == 400


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


async def test_rotation_rejects_a_stored_config_that_no_longer_validates(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    dp = _dp(uuid.uuid4(), "Bell")
    await _insert_instance(db, instance_id)
    await _insert_datapoint_row(db, dp.id, dp.name)
    await _insert_binding(db, binding_id=binding_id, dp_id=dp.id, instance_id=instance_id, config={"slug": "NOT A SLUG"})
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([dp]))

    with pytest.raises(HTTPException) as excinfo:
        await adapters_api.webhook_rotate_token(instance_id, binding_id, request=None, _user=_principal(is_admin=True), db=db)
    assert excinfo.value.status_code == 422


async def test_rotation_without_a_request_object_still_rotates(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    dp = _dp(uuid.uuid4(), "Bell")
    await _insert_instance(db, instance_id, config=json.dumps({"path_prefix": "/iot/hook"}))
    await _insert_datapoint_row(db, dp.id, dp.name)
    await _insert_binding(db, binding_id=binding_id, dp_id=dp.id, instance_id=instance_id, config={"slug": "bell", "token": "old-token"})
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([dp]))

    result = await adapters_api.webhook_rotate_token(instance_id, binding_id, request=None, _user=_principal(is_admin=True), db=db)

    assert result.token != "old-token"
    assert result.call_path == f"/iot/hook/bell?token={result.token}"
    row = await db.fetchone("SELECT config FROM adapter_bindings WHERE id=?", (str(binding_id),))
    assert json.loads(row["config"])["token"] == result.token


async def test_rotation_refuses_an_api_key_even_with_operator_grants(monkeypatch, db: Database):
    instance_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    dp = _dp(uuid.uuid4(), "Bell")
    await _insert_instance(db, instance_id)
    await _insert_datapoint_row(db, dp.id, dp.name)
    await _insert_binding(db, binding_id=binding_id, dp_id=dp.id, instance_id=instance_id, config={"slug": "bell", "token": "old-token"})
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('api_key', 'device-key', 'adapter_instance', ?, 'operator', 'allow')
        """,
        (str(instance_id),),
    )
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _RegistryStub([dp]))
    key = Principal(subject="api_key:device-key", type="api_key", is_admin=False)

    with pytest.raises(HTTPException) as excinfo:
        await adapters_api.webhook_rotate_token(instance_id, binding_id, request=None, _user=key, db=db)

    assert excinfo.value.status_code == 403
    row = await db.fetchone("SELECT config FROM adapter_bindings WHERE id=?", (str(binding_id),))
    assert json.loads(row["config"])["token"] == "old-token"


async def test_listing_refuses_an_api_key_even_with_operator_grants(db: Database):
    instance_id = uuid.uuid4()
    await _insert_instance(db, instance_id)
    await db.execute_and_commit(
        """
        INSERT INTO authz_node_roles (principal_type, principal_id, node_type, node_id, role, effect)
        VALUES ('api_key', 'device-key', 'adapter_instance', ?, 'operator', 'allow')
        """,
        (str(instance_id),),
    )
    key = Principal(subject="api_key:device-key", type="api_key", is_admin=False)

    with pytest.raises(HTTPException) as excinfo:
        await adapters_api.webhook_list_bindings(instance_id, _user=key, db=db)

    assert excinfo.value.status_code == 403


# ---------------------------------------------------------------------------
# Prefixes of instances that are configured but not running
# ---------------------------------------------------------------------------


async def test_a_configured_prefix_is_claimed_even_without_a_running_instance(monkeypatch, db: Database):
    from obs.api.webhook import _claimed_by_inactive_instance

    await _insert_instance(db, uuid.uuid4(), config=json.dumps({"path_prefix": "/off-hook"}))
    await _insert_instance(db, uuid.uuid4(), config="{}")  # falls back to /hook
    await _insert_instance(db, uuid.uuid4(), config=json.dumps({"path_prefix": "/api"}))  # invalid, skipped
    monkeypatch.setattr("obs.db.database.get_db", lambda: db)

    assert await _claimed_by_inactive_instance("/off-hook") is True
    assert await _claimed_by_inactive_instance("/off-hook/bell/tok") is True
    assert await _claimed_by_inactive_instance("/hook/bell") is True
    assert await _claimed_by_inactive_instance("/off-hookish") is False
    assert await _claimed_by_inactive_instance("/") is False


async def test_application_owned_paths_never_reach_the_database(monkeypatch):
    from obs.api.webhook import _claimed_by_inactive_instance

    def boom():
        raise AssertionError("the database must not be queried for application paths")

    monkeypatch.setattr("obs.db.database.get_db", boom)

    assert await _claimed_by_inactive_instance("/api/v1/datapoints") is False
    assert await _claimed_by_inactive_instance("/Assets/index.js") is False


async def test_an_uninitialised_database_claims_nothing(monkeypatch):
    from obs.api.webhook import _claimed_by_inactive_instance

    def not_ready():
        raise RuntimeError("Database not initialized")

    monkeypatch.setattr("obs.db.database.get_db", not_ready)

    assert await _claimed_by_inactive_instance("/hook/bell") is False
