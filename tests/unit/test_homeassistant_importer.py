"""Home Assistant entity browser filters and import planning (#1392)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from obs.adapters.homeassistant.importer import (
    HaImportRequest,
    HaImportSelection,
    filter_entities,
    mapping_summary,
    plan_import,
)
from obs.adapters.homeassistant.profiles import identity_hash

UID = identity_hash("velux", "roof-1")


def _entity(entity_id: str, state, attributes=None, **extra):
    domain = entity_id.split(".", 1)[0]
    attrs = attributes or {}
    return {
        "entity_id": entity_id,
        "name": attrs.get("friendly_name", entity_id),
        "domain": domain,
        "state": state,
        "unit": attrs.get("unit_of_measurement"),
        "device_class": attrs.get("device_class"),
        "available": state != "unavailable",
        "attributes": attrs,
        "platform": extra.get("platform"),
        "device": extra.get("device"),
        "area": extra.get("area"),
        "ha_uid": extra.get("ha_uid"),
    }


ENTITIES = [
    _entity(
        "cover.roof",
        "open",
        {"friendly_name": "Roof window", "current_position": 40, "supported_features": 15},
        platform="velux",
        area="Bad",
        device="KLF",
        ha_uid=UID,
    ),
    _entity("lock.shed", "locked", {"friendly_name": "Shed"}, platform="nuki", area="Garten"),
    _entity("sensor.temp", "21.5", {"friendly_name": "Temp", "unit_of_measurement": "°C", "device_class": "temperature"}, area="Bad"),
    _entity("sensor.mode", "eco", {"friendly_name": "Mode"}),
    _entity("sensor.void", "unknown", {}),
    _entity("sensor.void_unit", "unavailable", {"unit_of_measurement": "W"}),
    _entity("switch.fan", "on", {}),
    _entity("climate.living", "heat", {"temperature": 21.0, "hvac_modes": None}),
    _entity("media_player.sonos", "playing", {"volume_level": 0.3, "is_volume_muted": False, "media_title": "Song", "supported_features": 4}),
    _entity("light.desk", "on", {"brightness": 100, "on": True, "label": "x"}),
]


def _plan(*selections, existing=(), tags=()):
    request = HaImportRequest(selections=[HaImportSelection(**s) for s in selections], tags=list(tags))
    # existing entries: (binding_id, config) read as BOTH, or (binding_id, config, direction)
    return plan_import(ENTITIES, request, [e if len(e) == 3 else (*e, "BOTH") for e in existing])


def _one(**selection):
    result = _plan(selection)
    assert len(result) == 1
    return result[0]


class TestFilter:
    def test_text_domain_area_and_paging(self):
        page = filter_entities(ENTITIES, q="ROOF")
        assert [i["entity_id"] for i in page["items"]] == ["cover.roof"]
        assert page["items"][0]["default_profile"] == "cover.boolean_open"
        assert {p["id"]: p["supported"] for p in page["items"][0]["profiles"]}["cover.tilt_position"] is False
        page = filter_entities(ENTITIES, area="Bad", offset=1, limit=1)
        assert page["total"] == 2 and [i["entity_id"] for i in page["items"]] == ["sensor.temp"]
        page = filter_entities(ENTITIES, q="temp")
        assert page["items"][0]["entity_id"] == "sensor.temp"

    def test_facets_ignore_their_own_filter(self):
        page = filter_entities(ENTITIES, domain="sensor")
        assert "cover" in page["facets"]["domain"]
        assert page["facets"]["area"] == ["Bad"]
        assert filter_entities(ENTITIES, platform="velux")["facets"]["platform"] == ["nuki", "velux"]
        assert filter_entities(ENTITIES, device="KLF")["total"] == 1

    @pytest.mark.parametrize(("availability", "count"), [("", 10), ("available", 9), ("unavailable", 1)])
    def test_availability(self, availability, count):
        assert filter_entities(ENTITIES, availability=availability)["total"] == count

    def test_no_match(self):
        page = filter_entities(ENTITIES, q="zzz")
        assert page["total"] == 0 and page["items"] == []


class TestPlan:
    def test_lock_auto_profile_preview(self):
        c = _one(entity_id="lock.shed")
        assert c.status == "new"
        assert c.profile == "lock.boolean_locked"
        assert (c.data_type, c.direction, c.convention) == ("BOOLEAN", "BOTH", "trueLocked")
        assert c.config == {"entity_id": "lock.shed", "profile": "lock.boolean_locked"}
        assert c.mapping["write"]["true"] == {"domain": "lock", "name": "lock"}
        assert c.mapping["read"]["state_map"] == {"locked": True, "unlocked": False, "open": False}
        assert c.tags == ["homeassistant", "lock", "nuki", "Garten"]

    def test_velux_cover_full_set(self):
        result = _plan(
            {"entity_id": "cover.roof"},
            {"entity_id": "cover.roof", "attribute": "current_position"},
            {"entity_id": "cover.roof", "profile": "cover.stop"},
            tags=["velux", " ", "velux"],
        )
        assert [(c.profile, c.direction, c.data_type, c.unit) for c in result] == [
            ("cover.boolean_open", "BOTH", "BOOLEAN", None),
            ("cover.position", "SOURCE", "INTEGER", "%"),
            ("cover.stop", "DEST", "BOOLEAN", None),
        ]
        assert result[1].mapping["write"] == {"domain": "cover", "name": "set_cover_position", "value_path": "position"}
        assert result[1].name == "Roof window (current_position)"
        assert result[2].name == "Roof window (stop)"
        assert all(c.config["ha_uid"] == UID for c in result)
        assert result[0].tags == ["homeassistant", "cover", "velux", "Bad"]

    def test_sensor_types_and_units(self):
        temp, mode, void, void_unit = _plan(
            {"entity_id": "sensor.temp"},
            {"entity_id": "sensor.mode"},
            {"entity_id": "sensor.void"},
            {"entity_id": "sensor.void_unit"},
        )
        assert (temp.data_type, temp.unit, temp.direction, temp.profile) == ("FLOAT", "°C", "SOURCE", "sensor.value")
        assert "temperature" in temp.tags
        assert mode.data_type == "STRING"
        assert void.data_type == "STRING" and void_unit.data_type == "FLOAT"

    def test_plain_state_and_attribute_without_profile(self):
        fan, brightness, flag = _plan(
            {"entity_id": "switch.fan", "profile": None},
            {"entity_id": "switch.fan", "profile": None, "direction": "SOURCE"},  # duplicate: planned once
            {"entity_id": "light.desk", "attribute": "label"},
            {"entity_id": "light.desk", "attribute": "on"},
        )
        assert (fan.data_type, fan.direction, fan.profile, fan.config) == (
            "BOOLEAN",
            "SOURCE",
            None,
            {"entity_id": "switch.fan", "read_as": "boolean"},
        )
        assert (brightness.data_type, brightness.config) == ("STRING", {"entity_id": "light.desk", "attribute": "label", "read_as": "string"})
        assert flag.data_type == "BOOLEAN"
        assert brightness.mapping["mode"] == "default"

    @pytest.mark.parametrize(
        ("attribute", "data_type"),
        [("volume_level", "FLOAT"), ("media_title", "STRING")],
    )
    def test_attribute_auto_matches_profile(self, attribute, data_type):
        c = _one(entity_id="media_player.sonos", attribute=attribute)
        assert c.data_type == data_type
        assert c.profile is not None and "attribute" not in c.config

    def test_attribute_types(self):
        c = _one(entity_id="climate.living", attribute="temperature")
        assert (c.data_type, c.profile, c.direction) == ("FLOAT", None, "SOURCE")
        c = _one(entity_id="light.desk", attribute="brightness", profile=None)
        assert c.data_type == "INTEGER"

    def test_ambiguous_domain_imports_read_only_with_notice(self):
        c = _one(entity_id="climate.living")
        assert (c.status, c.direction, c.reason, c.profile) == ("new", "SOURCE", "ambiguousReadOnly", None)

    def test_explicit_ambiguous_profile_choice(self):
        c = _one(entity_id="media_player.sonos", profile="media_player.mute")
        assert c.status == "unsupported" and c.reason == "featureMissing"

    @pytest.mark.parametrize(
        ("selection", "reason"),
        [
            ({"entity_id": "sensor.ghost"}, "entityNotFound"),
            ({"entity_id": "lock.shed", "profile": "cover.stop"}, "profileNotApplicable"),
            ({"entity_id": "lock.shed", "profile": "nope"}, "profileNotApplicable"),
            ({"entity_id": "cover.roof", "profile": "cover.position", "attribute": "other"}, "profileNotApplicable"),
            ({"entity_id": "cover.roof", "profile": "cover.tilt_position"}, "featureMissing"),
            ({"entity_id": "light.desk", "attribute": "missing"}, "attributeUnavailable"),
            ({"entity_id": "climate.living", "direction": "BOTH"}, "writeNeedsProfile"),
            ({"entity_id": "sensor.temp", "direction": "DEST"}, "profileReadOnly"),
            ({"entity_id": "cover.roof", "profile": "cover.stop", "direction": "BOTH"}, "profileWriteOnly"),
        ],
    )
    def test_unsupported(self, selection, reason):
        c = _one(**selection)
        assert (c.status, c.reason) == ("unsupported", reason)

    def test_unsupported_explicit_profile_is_reported(self):
        assert _one(entity_id="lock.shed", profile="cover.stop").profile == "cover.stop"
        assert _one(entity_id="sensor.ghost").profile is None

    def test_idempotent_existing_and_duplicates(self):
        existing = [("b1", {"entity_id": "lock.shed", "profile": "lock.boolean_locked"})]
        result = _plan({"entity_id": "lock.shed"}, {"entity_id": "lock.shed", "profile": "lock.boolean_locked"}, existing=existing)
        assert len(result) == 1
        assert (result[0].status, result[0].reason, result[0].existing_binding_id) == ("exists", "bindingExists", "b1")

    def test_attribute_selection_is_read_only_unless_the_profile_is_chosen(self):
        assert _one(entity_id="cover.roof", attribute="current_position").direction == "SOURCE"
        (merged,) = _plan(
            {"entity_id": "cover.roof", "attribute": "current_position"},
            {"entity_id": "cover.roof", "profile": "cover.position"},
        )
        assert (merged.profile, merged.direction) == ("cover.position", "BOTH")
        (kept,) = _plan(
            {"entity_id": "cover.roof", "profile": "cover.position"},
            {"entity_id": "cover.roof", "attribute": "current_position"},
        )
        assert kept.direction == "BOTH"
        assert _one(entity_id="cover.roof", attribute="current_position", direction="BOTH").direction == "BOTH"

    def test_manual_binding_on_same_value_is_a_conflict(self):
        existing = [
            ("m1", {"entity_id": "lock.shed", "service_name": "lock"}),
            ("m2", {"entity_id": "light.desk", "attribute": "brightness"}),
            ("p1", {"entity_id": "cover.roof", "profile": "cover.boolean_open"}),
            ("c1", {"entity_id": "switch.fan", "profile": "nope"}),
            ("d1", {"entity_id": "media_player.sonos", "profile": "media_player.next"}),
        ]
        lock, brightness, plain_cover, stop, other_profile, plain_state, sonos = _plan(
            {"entity_id": "lock.shed"},
            {"entity_id": "light.desk", "attribute": "brightness"},
            {"entity_id": "cover.roof", "profile": None},
            {"entity_id": "cover.roof", "profile": "cover.stop"},
            {"entity_id": "cover.roof", "attribute": "current_position"},
            {"entity_id": "lock.shed", "profile": None},
            {"entity_id": "media_player.sonos", "profile": None},
            existing=existing,
        )
        assert (lock.status, lock.reason, lock.existing_binding_id) == ("conflict", "entityAlreadyBound", "m1")
        assert (brightness.status, brightness.existing_binding_id) == ("conflict", "m2")
        assert (plain_cover.status, plain_cover.existing_binding_id) == ("conflict", "p1")
        assert stop.status == "new"  # a command reads nothing
        assert other_profile.status == "new"  # another profile on another value
        assert (plain_state.status, plain_state.reason) == ("exists", "bindingExists")
        assert sonos.status == "new"  # the existing command binding reads nothing

    def test_renamed_entity_is_a_conflict(self):
        existing = [("b9", {"entity_id": "cover.old_roof", "profile": "cover.boolean_open", "ha_uid": UID}), ("b8", {"entity_id": "x.y"})]
        c = _one(entity_id="cover.roof")
        assert c.status == "new"
        c = _plan({"entity_id": "cover.roof"}, existing=existing)[0]
        assert (c.status, c.reason, c.existing_binding_id) == ("conflict", "entityRenamed", "b9")

    def test_request_validation(self):
        with pytest.raises(ValidationError):
            HaImportSelection(entity_id="Not An Entity")
        with pytest.raises(ValidationError):
            HaImportSelection(entity_id="a.b", attribute="x;y")
        with pytest.raises(ValidationError):
            HaImportSelection(entity_id="a.b", direction="UP")
        with pytest.raises(ValidationError):
            HaImportRequest(tags=["x" * 65])


def test_mapping_summary_modes():
    assert mapping_summary({"entity_id": "switch.f", "service_name": "toggle"})["write"] == {"mode": "legacy"}
    summary = mapping_summary({"entity_id": "cover.t", "action": {"domain": "cover", "name": "stop_cover"}})
    assert summary["mode"] == "action" and summary["write"] == {"domain": "cover", "name": "stop_cover"}
    assert mapping_summary({"entity_id": "sensor.t", "profile": "sensor.value"})["write"] is None
    lock = mapping_summary({"entity_id": "lock.t", "profile": "lock.boolean_locked"})
    assert lock["read"]["transitional_states"] == ["locking", "opening", "unlocking"]
    assert lock["read"]["fault_states"] == ["jammed"]


class TestReviewFollowUps:
    @pytest.mark.parametrize(
        ("entity", "expected_type", "read_as"),
        [
            (_entity("automation.x", "on"), "BOOLEAN", "boolean"),
            (_entity("script.x", "off"), "BOOLEAN", "boolean"),
            (_entity("climate.x", "on"), "STRING", "string"),  # ambiguous: no Boolean guess
            (_entity("sensor.n", "unknown"), "STRING", "string"),
            (_entity("sensor.w", "unknown", {"unit_of_measurement": "W"}), "FLOAT", "number"),
            (_entity("sensor.m", "12"), "FLOAT", "number"),
        ],
    )
    def test_plain_state_type_matches_how_it_is_read(self, entity, expected_type, read_as):
        request = HaImportRequest(selections=[HaImportSelection(entity_id=entity["entity_id"], profile=None)])
        (c,) = plan_import([entity], request, [])
        assert (c.data_type, c.config["read_as"]) == (expected_type, read_as)

    def test_sensor_profile_reads_as_its_inferred_type(self):
        c = _one(entity_id="sensor.temp")
        assert (c.profile, c.data_type, c.config["read_as"]) == ("sensor.value", "FLOAT", "number")
        assert "read_as" not in _one(entity_id="lock.shed").config

    def test_write_only_existing_binding_is_no_reader(self):
        existing = [("w1", {"entity_id": "switch.fan", "service_name": "turn_on"}, "DEST")]
        assert _plan({"entity_id": "switch.fan"}, existing=existing)[0].status == "new"
        existing = [("r1", {"entity_id": "switch.fan", "service_name": "turn_on"}, "SOURCE")]
        assert _plan({"entity_id": "switch.fan"}, existing=existing)[0].reason == "entityAlreadyBound"

    def test_same_value_selected_twice_in_one_request(self):
        profiled, plain = _plan({"entity_id": "switch.fan"}, {"entity_id": "switch.fan", "profile": None})
        assert profiled.status == "new"
        assert (plain.status, plain.reason) == ("conflict", "selectedTwice")
        plain, profiled = _plan({"entity_id": "switch.fan", "profile": None}, {"entity_id": "switch.fan"})
        assert (plain.status, profiled.status, profiled.reason) == ("new", "conflict", "selectedTwice")

    def test_commands_and_rejected_candidates_do_not_block_others(self):
        stop, state = _plan({"entity_id": "cover.roof", "profile": "cover.stop"}, {"entity_id": "cover.roof", "profile": None})
        assert (stop.status, state.status) == ("new", "new")
        existing = [("m1", {"entity_id": "lock.shed", "service_name": "lock"})]
        first, second = _plan({"entity_id": "lock.shed"}, {"entity_id": "lock.shed", "profile": None, "direction": "SOURCE"}, existing=existing)
        assert first.reason == "entityAlreadyBound"
        assert second.reason == "bindingExists"

    def test_source_plus_dest_merges_to_both(self):
        (merged,) = _plan(
            {"entity_id": "light.desk", "attribute": "brightness"},
            {"entity_id": "light.desk", "profile": "light.brightness", "direction": "DEST"},
        )
        assert merged.direction == "BOTH"
        (same,) = _plan(
            {"entity_id": "light.desk", "profile": "light.brightness", "direction": "DEST"},
            {"entity_id": "light.desk", "profile": "light.brightness", "direction": "DEST"},
        )
        assert same.direction == "DEST"
