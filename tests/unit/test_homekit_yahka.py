from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from obs.db.database import Database
from obs.homekit.yahka import (
    HomeKitApplyRequest,
    HomeKitPreviewOptions,
    apply_mapping,
    build_preview,
    slugify,
)


pytestmark = pytest.mark.asyncio


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _insert_dp(
    db: Database,
    dp_id: str,
    name: str,
    data_type: str = "BOOLEAN",
    unit: str | None = None,
) -> None:
    await db.execute(
        """INSERT INTO datapoints
           (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias,
            created_at, updated_at, persist_value, record_history)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            dp_id,
            name,
            data_type,
            unit,
            json.dumps(["knx"]),
            f"dp/{dp_id}/value",
            None,
            _now(),
            _now(),
            1,
            1,
        ),
    )


async def _insert_knx_binding(
    db: Database,
    dp_id: str,
    group_address: str,
    state_group_address: str | None = None,
    direction: str = "BOTH",
) -> None:
    await db.execute(
        """INSERT INTO adapter_bindings
           (id, datapoint_id, adapter_type, adapter_instance_id, direction, config, enabled,
            created_at, updated_at)
           VALUES (?, ?, 'KNX', ?, ?, ?, 1, ?, ?)""",
        (
            str(uuid.uuid4()),
            dp_id,
            str(uuid.uuid4()),
            direction,
            json.dumps(
                {
                    "group_address": group_address,
                    "state_group_address": state_group_address,
                    "dpt_id": "DPT1.001",
                }
            ),
            _now(),
            _now(),
        ),
    )


async def _insert_iobroker_instance(db: Database, instance_id: str) -> None:
    await db.execute(
        """INSERT INTO adapter_instances
           (id, adapter_type, name, config, enabled, created_at, updated_at)
           VALUES (?, 'IOBROKER', 'ioBroker', '{}', 1, ?, ?)""",
        (instance_id, _now(), _now()),
    )


async def _insert_node(
    db: Database,
    node_id: str,
    name: str,
    parent_id: str | None,
    node_type: str,
    widgets: list[dict] | None = None,
    order: int = 0,
) -> None:
    page_config = json.dumps(
        {
            "grid_cols": 12,
            "grid_row_height": 80,
            "grid_cell_width": 80,
            "background": None,
            "widgets": widgets or [],
        }
    )
    await db.execute(
        """INSERT INTO visu_nodes
           (id, parent_id, name, type, node_order, icon, access, access_pin,
            page_config, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)""",
        (node_id, parent_id, name, node_type, order, page_config, _now(), _now()),
    )


@pytest_asyncio.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def test_slugify_normalizes_german_names() -> None:
    assert slugify("EG Küche Licht Decke") == "eg_kueche_licht_decke"
    assert slugify("Außen Büro") == "aussen_buero"


async def test_build_preview_maps_visu_widgets_with_explicit_binding_directions(
    db: Database,
) -> None:
    switch_dp = str(uuid.uuid4())
    status_dp = str(uuid.uuid4())
    temp_dp = str(uuid.uuid4())
    actual_temp_dp = str(uuid.uuid4())
    contact_dp = str(uuid.uuid4())

    await _insert_dp(db, switch_dp, "Küche Licht schreiben")
    await _insert_dp(db, status_dp, "Küche Licht Status")
    await _insert_dp(db, temp_dp, "Küche Solltemperatur", "FLOAT", "°C")
    await _insert_dp(db, actual_temp_dp, "Küche Isttemperatur", "FLOAT", "°C")
    await _insert_dp(db, contact_dp, "Küche Fenster", "BOOLEAN")

    await _insert_knx_binding(db, switch_dp, "1/1/1", "1/1/2", "BOTH")
    await _insert_knx_binding(db, status_dp, "1/1/2", None, "SOURCE")

    root_id = "root"
    floor_id = "eg"
    page_id = "kueche"
    await _insert_node(db, root_id, "Home", None, "LOCATION")
    await _insert_node(db, floor_id, "EG", root_id, "LOCATION")
    await _insert_node(
        db,
        page_id,
        "Küche",
        floor_id,
        "PAGE",
        widgets=[
            {
                "id": "w-light",
                "type": "Licht",
                "config": {
                    "label": "Licht",
                    "dp_switch": switch_dp,
                    "dp_switch_status": status_dp,
                },
            },
            {
                "id": "w-rtr",
                "type": "RTR",
                "datapoint_id": temp_dp,
                "config": {
                    "label": "Heizung",
                    "actual_temp_dp_id": actual_temp_dp,
                    "step": 0.5,
                },
            },
            {
                "id": "w-window",
                "type": "Fenster",
                "config": {
                    "label": "Fenster",
                    "dp_contact": contact_dp,
                    "invert_contact": True,
                },
            },
        ],
    )
    await db.commit()

    preview = await build_preview(db, HomeKitPreviewOptions(root_node_id=root_id))

    assert preview.summary.rooms == 1
    assert preview.summary.accessories == 3
    room = preview.rooms[0]
    assert room.apple_home_room == "EG Küche"

    light = next(a for a in room.accessories if a.type == "Lightbulb")
    assert light.states["on"].binding_direction == "BOTH"
    assert light.states["on"].obs_binding_direction == "BOTH"
    assert light.obs.status_dp_id == status_dp
    assert light.obs.status_confirms_write is True
    assert light.obs.optimistic_update is False
    assert light.obs.knx_write_ga == "1/1/1"
    assert light.obs.knx_status_ga == "1/1/2"

    thermostat = next(a for a in room.accessories if a.type == "Thermostat")
    assert thermostat.states["current_temperature"].binding_direction == "FROM_OBS"
    assert thermostat.states["current_temperature"].obs_binding_direction == "DEST"
    assert thermostat.states["target_temperature"].binding_direction == "BOTH"
    assert thermostat.obs.extra["target_temperature_range"] == [10, 38]
    assert "Sollwert-Roundtrip" in " ".join(thermostat.warnings)

    contact = next(a for a in room.accessories if a.type == "ContactSensor")
    assert contact.states["contact"].binding_direction == "FROM_OBS"
    assert contact.obs.extra["invert"] is True


async def test_build_preview_warns_when_homekit_bridge_limit_is_exceeded(
    db: Database,
) -> None:
    root_id = "root"
    floor_id = "eg"
    page_id = "kueche"
    widgets = [
        {"id": f"w-{i}", "type": "Licht", "config": {"label": f"Licht {i}"}}
        for i in range(4)
    ]
    await _insert_node(db, root_id, "Home", None, "LOCATION")
    await _insert_node(db, floor_id, "EG", root_id, "LOCATION")
    await _insert_node(db, page_id, "Küche", floor_id, "PAGE", widgets=widgets)
    await db.commit()

    preview = await build_preview(
        db,
        HomeKitPreviewOptions(root_node_id=root_id, accessory_limit_per_bridge=3),
    )

    assert preview.summary.accessories == 4
    assert preview.summary.exceeds_single_bridge_limit is True
    assert preview.summary.recommended_bridges == 2
    assert preview.warnings


async def test_apply_mapping_dry_run_does_not_create_datapoints(db: Database) -> None:
    iobroker_id = str(uuid.uuid4())
    await _insert_iobroker_instance(db, iobroker_id)
    root_id = "root"
    floor_id = "eg"
    page_id = "kueche"
    await _insert_node(db, root_id, "Home", None, "LOCATION")
    await _insert_node(db, floor_id, "EG", root_id, "LOCATION")
    await _insert_node(
        db,
        page_id,
        "Küche",
        floor_id,
        "PAGE",
        widgets=[
            {"id": "w-light", "type": "Licht", "config": {"label": "Licht"}},
        ],
    )
    await db.commit()

    class FailingRegistry:
        async def create(self, payload):  # pragma: no cover - must not be called
            raise AssertionError("dry_run must not create datapoints")

    result = await apply_mapping(
        db,
        FailingRegistry(),
        HomeKitApplyRequest(
            root_node_id=root_id,
            iobroker_instance_id=uuid.UUID(iobroker_id),
            dry_run=True,
        ),
    )

    assert result.dry_run is True
    assert result.apply_summary.accessories == 1
    assert result.preview_summary.accessories == 1
    assert result.created_datapoints == 0
    assert [item.action for item in result.items] == ["create"]
    rows = await db.fetchall("SELECT * FROM datapoints")
    assert rows == []


async def test_apply_mapping_creates_datapoints_bindings_and_is_idempotent(
    db: Database,
) -> None:
    from unittest.mock import AsyncMock, MagicMock
    from obs.core.registry import DataPointRegistry

    iobroker_id = str(uuid.uuid4())
    await _insert_iobroker_instance(db, iobroker_id)
    root_id = "root"
    floor_id = "eg"
    page_id = "kueche"
    await _insert_node(db, root_id, "Home", None, "LOCATION")
    await _insert_node(db, floor_id, "EG", root_id, "LOCATION")
    await _insert_node(
        db,
        page_id,
        "Küche",
        floor_id,
        "PAGE",
        widgets=[
            {"id": "w-light", "type": "Licht", "config": {"label": "Licht"}},
        ],
    )
    await db.commit()

    mqtt = MagicMock()
    mqtt.publish_value = AsyncMock()
    bus = MagicMock()
    registry = DataPointRegistry(db, mqtt, bus)
    await registry.load_from_db()

    request = HomeKitApplyRequest(
        root_node_id=root_id,
        iobroker_instance_id=uuid.UUID(iobroker_id),
        dry_run=False,
    )
    first = await apply_mapping(db, registry, request)

    assert first.apply_summary.accessories == 1
    assert first.created_datapoints == 1
    assert first.created_bindings == 1
    assert first.items[0].state_id == "0_userdata.0.obs.home.eg.kueche.licht.on"
    assert first.items[0].obs_binding_direction == "BOTH"

    rows = await db.fetchall("SELECT * FROM adapter_bindings")
    assert len(rows) == 1
    assert rows[0]["direction"] == "BOTH"
    cfg = json.loads(rows[0]["config"])
    assert cfg["state_id"] == first.items[0].state_id
    assert cfg["ack"] is True
    assert cfg["source_data_type"] == "bool"

    second = await apply_mapping(db, registry, request)
    assert second.created_datapoints == 0
    assert second.created_bindings == 0
    assert second.skipped_existing == 1


async def test_apply_mapping_reuses_existing_knx_datapoints(db: Database) -> None:
    from unittest.mock import AsyncMock, MagicMock
    from obs.core.registry import DataPointRegistry

    iobroker_id = str(uuid.uuid4())
    switch_dp = str(uuid.uuid4())
    status_dp = str(uuid.uuid4())
    await _insert_iobroker_instance(db, iobroker_id)
    await _insert_dp(db, switch_dp, "Küche Licht", "UNKNOWN")
    await _insert_dp(db, status_dp, "Küche Licht Status", "BOOLEAN")
    await _insert_knx_binding(db, switch_dp, "1/2/3", direction="BOTH")
    await _insert_knx_binding(db, status_dp, "1/2/4", direction="BOTH")

    root_id = "root"
    floor_id = "eg"
    page_id = "kueche"
    await _insert_node(db, root_id, "Home", None, "LOCATION")
    await _insert_node(db, floor_id, "EG", root_id, "LOCATION")
    await _insert_node(
        db,
        page_id,
        "Küche",
        floor_id,
        "PAGE",
        widgets=[
            {
                "id": "w-light",
                "type": "Licht",
                "config": {
                    "label": "Licht",
                    "dp_switch": switch_dp,
                    "dp_switch_status": status_dp,
                },
            },
        ],
    )
    await db.commit()

    mqtt = MagicMock()
    mqtt.publish_value = AsyncMock()
    bus = MagicMock()
    registry = DataPointRegistry(db, mqtt, bus)
    await registry.load_from_db()

    result = await apply_mapping(
        db,
        registry,
        HomeKitApplyRequest(
            root_node_id=root_id,
            iobroker_instance_id=uuid.UUID(iobroker_id),
            dry_run=False,
        ),
    )

    assert result.created_datapoints == 0
    assert result.created_bindings == 1
    assert result.items[0].action == "reuse_existing"
    assert result.items[0].datapoint_id == switch_dp

    dps = await db.fetchall("SELECT * FROM datapoints ORDER BY name")
    assert len(dps) == 2
    switch_row = next(row for row in dps if row["id"] == switch_dp)
    assert switch_row["data_type"] == "BOOLEAN"
    assert "homekit" in json.loads(switch_row["tags"])

    iobroker_rows = await db.fetchall(
        "SELECT * FROM adapter_bindings WHERE adapter_type='IOBROKER'"
    )
    assert len(iobroker_rows) == 1
    assert iobroker_rows[0]["datapoint_id"] == switch_dp
    assert iobroker_rows[0]["direction"] == "BOTH"

    knx_rows = await db.fetchall(
        "SELECT * FROM adapter_bindings WHERE adapter_type='KNX'"
    )
    directions = {row["datapoint_id"]: row["direction"] for row in knx_rows}
    assert directions[switch_dp] == "DEST"
    assert directions[status_dp] == "SOURCE"


async def test_apply_mapping_can_create_iobroker_states_for_existing_bindings(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock, MagicMock
    from obs.core.registry import DataPointRegistry

    iobroker_id = str(uuid.uuid4())
    await _insert_iobroker_instance(db, iobroker_id)
    root_id = "root"
    floor_id = "eg"
    page_id = "kueche"
    await _insert_node(db, root_id, "Home", None, "LOCATION")
    await _insert_node(db, floor_id, "EG", root_id, "LOCATION")
    await _insert_node(
        db,
        page_id,
        "Küche",
        floor_id,
        "PAGE",
        widgets=[
            {"id": "w-light", "type": "Licht", "config": {"label": "Licht"}},
        ],
    )
    await db.commit()

    mqtt = MagicMock()
    mqtt.publish_value = AsyncMock()
    bus = MagicMock()
    registry = DataPointRegistry(db, mqtt, bus)
    await registry.load_from_db()

    await apply_mapping(
        db,
        registry,
        HomeKitApplyRequest(
            root_node_id=root_id,
            iobroker_instance_id=uuid.UUID(iobroker_id),
            dry_run=False,
        ),
    )

    class AdapterInstance:
        connected = True

        def __init__(self) -> None:
            self.ensure_state = AsyncMock()

    adapter = AdapterInstance()

    def get_instance_by_id(instance_id: str):
        assert instance_id == iobroker_id
        return adapter

    monkeypatch.setattr("obs.adapters.registry.get_instance_by_id", get_instance_by_id)

    result = await apply_mapping(
        db,
        registry,
        HomeKitApplyRequest(
            root_node_id=root_id,
            iobroker_instance_id=uuid.UUID(iobroker_id),
            dry_run=False,
            create_iobroker_states=True,
        ),
    )

    assert result.created_datapoints == 0
    assert result.created_bindings == 0
    assert result.skipped_existing == 1
    assert result.created_iobroker_states == 1
    assert result.items[0].action == "skip_existing"
    assert result.items[0].iobroker_state_created is True
    adapter.ensure_state.assert_awaited_once()
    payload = adapter.ensure_state.await_args.args[0]
    assert payload["state_id"] == "0_userdata.0.obs.home.eg.kueche.licht.on"
    assert payload["write"] is True
