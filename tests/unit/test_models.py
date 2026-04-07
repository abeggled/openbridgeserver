"""
Unit tests for obs/models/datapoint.py and obs/models/binding.py

Covers:
  - DataPoint: default values, auto-generated mqtt_topic, model_validator,
               max_length constraints, DataPointCreate / DataPointUpdate
  - AdapterBinding: direction semantics, send-filter fields, value_formula,
                    AdapterBindingCreate / AdapterBindingUpdate
"""
from __future__ import annotations

import uuid
import datetime

import pytest
from pydantic import ValidationError

from obs.models.datapoint import DataPoint, DataPointCreate, DataPointUpdate
from obs.models.binding import AdapterBinding, AdapterBindingCreate, AdapterBindingUpdate


# ===========================================================================
# DataPoint
# ===========================================================================

class TestDataPointDefaults:
    def test_id_is_uuid(self):
        dp = DataPoint(name="Test")
        assert isinstance(dp.id, uuid.UUID)

    def test_each_instance_gets_unique_id(self):
        a = DataPoint(name="A")
        b = DataPoint(name="B")
        assert a.id != b.id

    def test_default_data_type_is_unknown(self):
        dp = DataPoint(name="Test")
        assert dp.data_type == "UNKNOWN"

    def test_default_unit_is_none(self):
        dp = DataPoint(name="Test")
        assert dp.unit is None

    def test_default_tags_is_empty_list(self):
        dp = DataPoint(name="Test")
        assert dp.tags == []

    def test_default_persist_value_is_true(self):
        dp = DataPoint(name="Test")
        assert dp.persist_value is True

    def test_default_mqtt_alias_is_none(self):
        dp = DataPoint(name="Test")
        assert dp.mqtt_alias is None

    def test_created_at_is_utc(self):
        dp = DataPoint(name="Test")
        assert dp.created_at.tzinfo is not None

    def test_updated_at_is_utc(self):
        dp = DataPoint(name="Test")
        assert dp.updated_at.tzinfo is not None


class TestDataPointMqttTopic:
    def test_mqtt_topic_auto_generated(self):
        dp = DataPoint(name="Temp")
        assert dp.mqtt_topic == f"dp/{dp.id}/value"

    def test_mqtt_topic_contains_uuid(self):
        dp = DataPoint(name="Temp")
        assert str(dp.id) in dp.mqtt_topic

    def test_explicit_mqtt_topic_preserved(self):
        dp = DataPoint(name="Temp", mqtt_topic="custom/topic")
        assert dp.mqtt_topic == "custom/topic"

    def test_empty_string_topic_triggers_auto_generation(self):
        dp = DataPoint(name="Temp", mqtt_topic="")
        assert dp.mqtt_topic == f"dp/{dp.id}/value"

    def test_two_datapoints_have_different_topics(self):
        a = DataPoint(name="A")
        b = DataPoint(name="B")
        assert a.mqtt_topic != b.mqtt_topic


class TestDataPointValidation:
    def test_name_max_255_chars_ok(self):
        dp = DataPoint(name="A" * 255)
        assert len(dp.name) == 255

    def test_name_above_255_raises(self):
        with pytest.raises(ValidationError):
            DataPoint(name="A" * 256)

    def test_tags_list_of_strings(self):
        dp = DataPoint(name="Test", tags=["klima", "wohnzimmer"])
        assert dp.tags == ["klima", "wohnzimmer"]

    def test_explicit_id_respected(self):
        fixed_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        dp = DataPoint(name="Test", id=fixed_id)
        assert dp.id == fixed_id
        assert str(fixed_id) in dp.mqtt_topic

    def test_full_construction(self):
        dp = DataPoint(
            name="Wohnzimmer Temperatur",
            data_type="FLOAT",
            unit="°C",
            tags=["klima"],
            mqtt_alias="alias/klima/wohnzimmer/value",
            persist_value=False,
        )
        assert dp.data_type == "FLOAT"
        assert dp.unit == "°C"
        assert dp.persist_value is False


class TestDataPointCreate:
    def test_minimal_create(self):
        create = DataPointCreate(name="Sensor")
        assert create.name == "Sensor"
        assert create.data_type == "UNKNOWN"

    def test_full_create(self):
        create = DataPointCreate(
            name="Test",
            data_type="FLOAT",
            unit="°C",
            tags=["tag1"],
            persist_value=False,
        )
        assert create.unit == "°C"
        assert create.persist_value is False

    def test_name_max_length(self):
        with pytest.raises(ValidationError):
            DataPointCreate(name="X" * 256)

    def test_no_id_field(self):
        # DataPointCreate must not expose an id field (server assigns it)
        assert "id" not in DataPointCreate.model_fields


class TestDataPointUpdate:
    def test_all_fields_optional(self):
        upd = DataPointUpdate()
        assert upd.name is None
        assert upd.data_type is None
        assert upd.unit is None
        assert upd.tags is None

    def test_partial_update(self):
        upd = DataPointUpdate(name="New Name")
        assert upd.name == "New Name"
        assert upd.unit is None

    def test_name_max_length_enforced(self):
        with pytest.raises(ValidationError):
            DataPointUpdate(name="X" * 256)


# ===========================================================================
# AdapterBinding
# ===========================================================================

class TestAdapterBindingDefaults:
    def _make(self, **kwargs) -> AdapterBinding:
        defaults = {
            "datapoint_id": uuid.uuid4(),
            "adapter_type": "KNX",
            "direction": "SOURCE",
        }
        return AdapterBinding(**{**defaults, **kwargs})

    def test_id_is_uuid(self):
        b = self._make()
        assert isinstance(b.id, uuid.UUID)

    def test_enabled_default_true(self):
        b = self._make()
        assert b.enabled is True

    def test_send_on_change_default_false(self):
        b = self._make()
        assert b.send_on_change is False

    def test_send_throttle_default_none(self):
        b = self._make()
        assert b.send_throttle_ms is None

    def test_send_min_delta_default_none(self):
        b = self._make()
        assert b.send_min_delta is None

    def test_send_min_delta_pct_default_none(self):
        b = self._make()
        assert b.send_min_delta_pct is None

    def test_value_formula_default_none(self):
        b = self._make()
        assert b.value_formula is None

    def test_value_map_default_none(self):
        b = self._make()
        assert b.value_map is None

    def test_config_default_empty_dict(self):
        b = self._make()
        assert b.config == {}

    def test_created_at_utc(self):
        b = self._make()
        assert b.created_at.tzinfo is not None


class TestAdapterBindingDirection:
    def _make(self, direction: str) -> AdapterBinding:
        return AdapterBinding(
            datapoint_id=uuid.uuid4(),
            adapter_type="KNX",
            direction=direction,
        )

    def test_source(self):
        b = self._make("SOURCE")
        assert b.direction == "SOURCE"

    def test_dest(self):
        b = self._make("DEST")
        assert b.direction == "DEST"

    def test_both(self):
        b = self._make("BOTH")
        assert b.direction == "BOTH"

    def test_invalid_direction_raises(self):
        with pytest.raises(ValidationError):
            AdapterBinding(
                datapoint_id=uuid.uuid4(),
                adapter_type="KNX",
                direction="READ",   # invalid
            )

    def test_lowercase_direction_raises(self):
        with pytest.raises(ValidationError):
            AdapterBinding(
                datapoint_id=uuid.uuid4(),
                adapter_type="KNX",
                direction="source",  # must be uppercase
            )


class TestAdapterBindingConfig:
    def test_knx_config(self):
        b = AdapterBinding(
            datapoint_id=uuid.uuid4(),
            adapter_type="KNX",
            direction="SOURCE",
            config={"group_address": "1/2/3", "dpt_id": "DPT9.001"},
        )
        assert b.config["group_address"] == "1/2/3"
        assert b.config["dpt_id"] == "DPT9.001"

    def test_modbus_config(self):
        b = AdapterBinding(
            datapoint_id=uuid.uuid4(),
            adapter_type="MODBUS_TCP",
            direction="DEST",
            config={"unit_id": 1, "register_type": "holding",
                    "address": 100, "data_format": "float32"},
        )
        assert b.config["register_type"] == "holding"

    def test_send_filter_fields(self):
        b = AdapterBinding(
            datapoint_id=uuid.uuid4(),
            adapter_type="KNX",
            direction="DEST",
            send_throttle_ms=500,
            send_on_change=True,
            send_min_delta=0.5,
            send_min_delta_pct=5.0,
        )
        assert b.send_throttle_ms == 500
        assert b.send_on_change is True
        assert b.send_min_delta == 0.5
        assert b.send_min_delta_pct == 5.0

    def test_value_formula_stored(self):
        b = AdapterBinding(
            datapoint_id=uuid.uuid4(),
            adapter_type="KNX",
            direction="SOURCE",
            value_formula="x / 10",
        )
        assert b.value_formula == "x / 10"

    def test_value_map_stored(self):
        b = AdapterBinding(
            datapoint_id=uuid.uuid4(),
            adapter_type="KNX",
            direction="SOURCE",
            value_map={"0": "off", "1": "on"},
        )
        assert b.value_map["1"] == "on"


class TestAdapterBindingCreate:
    def test_minimal_create(self):
        create = AdapterBindingCreate(
            adapter_instance_id=uuid.uuid4(),
            direction="SOURCE",
        )
        assert create.direction == "SOURCE"
        assert create.enabled is True

    def test_adapter_type_optional(self):
        create = AdapterBindingCreate(
            adapter_instance_id=uuid.uuid4(),
            direction="BOTH",
        )
        assert create.adapter_type is None

    def test_invalid_direction_raises(self):
        with pytest.raises(ValidationError):
            AdapterBindingCreate(
                adapter_instance_id=uuid.uuid4(),
                direction="INVALID",
            )


class TestAdapterBindingUpdate:
    def test_all_fields_optional(self):
        upd = AdapterBindingUpdate()
        assert upd.direction is None
        assert upd.enabled is None
        assert upd.config is None

    def test_partial_update(self):
        upd = AdapterBindingUpdate(enabled=False, send_on_change=True)
        assert upd.enabled is False
        assert upd.send_on_change is True
        assert upd.direction is None

    def test_invalid_direction_in_update_raises(self):
        with pytest.raises(ValidationError):
            AdapterBindingUpdate(direction="WRONG")
