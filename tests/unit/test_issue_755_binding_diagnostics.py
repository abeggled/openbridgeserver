from __future__ import annotations

import datetime
import json
import uuid
from types import SimpleNamespace

import pytest

from obs.adapters import registry as adapter_registry
from obs.core.registry import DataPointRegistry, ValueState
from obs.db import database


class _Db:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self, _query, _params=()):
        return list(self._rows)


class _Instance:
    connected = True

    def __init__(self):
        self.bindings = None
        self.status_events = []
        self.last_severity = "ok"
        self.last_detail = ""

    async def reload_bindings(self, bindings):
        self.bindings = bindings

    async def _publish_status(self, connected, detail="", severity="ok"):
        self.status_events.append((connected, detail, severity))
        self.last_severity = severity
        self.last_detail = detail


class _PlainInstance:
    connected = False
    last_detail = ""


class _BadRow:
    def __getitem__(self, _key):
        raise KeyError("missing")


def _binding_row(**overrides):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    row = {
        "id": str(uuid.uuid4()),
        "datapoint_id": str(uuid.uuid4()),
        "adapter_type": "MQTT",
        "adapter_instance_id": str(uuid.uuid4()),
        "direction": "SOURCE",
        "config": "{}",
        "enabled": 1,
        "send_throttle_ms": None,
        "send_on_change": 0,
        "send_min_delta": None,
        "send_min_delta_pct": None,
        "value_formula": None,
        "value_map": None,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_reload_instance_bindings_skips_invalid_rows_and_sets_warning(monkeypatch):
    instance_id = str(uuid.uuid4())
    instance = _Instance()
    monkeypatch.setitem(adapter_registry._instances, instance_id, instance)
    rows = [
        _binding_row(adapter_instance_id=instance_id, datapoint_id="not-a-uuid"),
        _binding_row(adapter_instance_id=instance_id, config="{unit_id: 10}"),
        _binding_row(adapter_instance_id=instance_id, config=json.dumps({"topic": "ok"})),
    ]

    await adapter_registry.reload_instance_bindings(instance_id, _Db(rows))

    assert len(instance.bindings) == 1
    assert instance.bindings[0].config == {"topic": "ok"}
    assert instance.last_severity == "warning"
    assert "2 invalid binding" in instance.last_detail


@pytest.mark.asyncio
async def test_datapoint_registry_exposes_type_mismatch_diagnostic(monkeypatch):
    from obs.api.v1 import datapoints as dp_api

    dp_id = uuid.uuid4()
    registry = DataPointRegistry(db=SimpleNamespace(), mqtt_client=SimpleNamespace(), event_bus=SimpleNamespace())
    state = ValueState()
    registry._values[dp_id] = state
    monkeypatch.setattr(dp_api, "get_registry", lambda: registry)

    await registry.report_type_mismatch(
        dp_id,
        expected="float",
        got="str",
        source_adapter="MQTT",
        value="online",
    )

    result = dp_api._enrich(
        SimpleNamespace(
            id=dp_id,
            name="Deye/Micro/Status",
            data_type="FLOAT",
            unit=None,
            tags=[],
            mqtt_topic="dp/status/value",
            mqtt_alias=None,
            persist_value=True,
            record_history=True,
            created_at=datetime.datetime.now(datetime.UTC),
            updated_at=datetime.datetime.now(datetime.UTC),
        )
    )

    assert result.diagnostics[0].type == "type_mismatch"
    assert result.diagnostics[0].expected == "float"
    assert result.diagnostics[0].got == "str"
    assert result.diagnostics[0].count == 1


def test_new_adapter_binding_schema_requires_valid_json_config():
    assert "config          TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(config))" in database._MIGRATION_V1
    assert "value_map TEXT CHECK (value_map IS NULL OR json_valid(value_map))" in database._MIGRATION_V20


@pytest.mark.asyncio
async def test_bindings_api_reload_adapter_instance_delegates_to_registry(monkeypatch):
    from obs.api.v1 import bindings as bindings_api

    calls = []

    async def _reload(instance_id, db):
        calls.append((instance_id, db))

    db = _Db([])
    monkeypatch.setattr(adapter_registry, "reload_instance_bindings", _reload)

    await bindings_api._reload_adapter_instance("instance-1", db)

    assert calls == [("instance-1", db)]


def test_load_valid_bindings_reports_unknown_row_when_id_cannot_be_read():
    bindings, issues = adapter_registry.load_valid_bindings([_BadRow()])

    assert bindings == []
    assert issues[0].binding_id == "<unknown>"
    assert issues[0].adapter_instance_id is None
    assert "KeyError" in issues[0].reason


@pytest.mark.asyncio
async def test_binding_load_status_clears_previous_binding_warning():
    instance = _Instance()
    instance.last_detail = "Invalid adapter bindings skipped: old"

    await adapter_registry._publish_binding_load_status(instance, [])

    assert instance.last_severity == "ok"
    assert instance.last_detail == ""
    assert instance.status_events[-1] == (True, "", "ok")


@pytest.mark.asyncio
async def test_binding_load_status_does_not_clear_unrelated_warning():
    instance = _Instance()
    instance.last_detail = "Transport warning"

    await adapter_registry._publish_binding_load_status(instance, [])

    assert instance.status_events == []
    assert instance.last_detail == "Transport warning"


@pytest.mark.asyncio
async def test_binding_load_status_supports_instances_without_publish_status():
    instance = _PlainInstance()
    issue = adapter_registry.BindingLoadIssue(
        binding_id="b1",
        adapter_instance_id="i1",
        reason="ValueError: bad",
    )

    await adapter_registry._publish_binding_load_status(instance, [issue])

    assert instance._last_severity == "warning"
    assert "b1" in instance._last_detail


def test_binding_load_detail_shows_more_suffix():
    issues = [adapter_registry.BindingLoadIssue(f"b{i}", "i1", "ValueError: bad") for i in range(5)]

    detail = adapter_registry._format_binding_load_detail(issues)

    assert "+2 more" in detail


@pytest.mark.asyncio
async def test_datapoint_registry_type_mismatch_count_increments_and_clears():
    dp_id = uuid.uuid4()
    registry = DataPointRegistry(db=SimpleNamespace(), mqtt_client=SimpleNamespace(), event_bus=SimpleNamespace())
    registry._values[dp_id] = ValueState()

    await registry.report_type_mismatch(dp_id, expected="float", got="str", source_adapter="MQTT", value="online")
    await registry.report_type_mismatch(dp_id, expected="float", got="str", source_adapter="MQTT", value="offline")

    diagnostic = registry._values[dp_id].diagnostics["type_mismatch"]
    assert diagnostic["count"] == 2
    assert diagnostic["last_value"] == "offline"

    await registry.clear_diagnostic(dp_id, "type_mismatch")
    assert registry._values[dp_id].diagnostics == {}


@pytest.mark.asyncio
async def test_datapoint_registry_diagnostic_methods_ignore_unknown_datapoint():
    registry = DataPointRegistry(db=SimpleNamespace(), mqtt_client=SimpleNamespace(), event_bus=SimpleNamespace())

    await registry.report_type_mismatch(
        uuid.uuid4(),
        expected="float",
        got="str",
        source_adapter="MQTT",
        value="online",
    )
    await registry.clear_diagnostic(uuid.uuid4(), "type_mismatch")

    assert registry._values == {}
