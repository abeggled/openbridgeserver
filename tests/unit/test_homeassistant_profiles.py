"""Home Assistant binding schema, profiles and value/action resolution (#1392)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from obs.adapters.homeassistant.profiles import (
    AMBIGUOUS_DOMAINS,
    PROFILES,
    HaBindingConfig,
    NoActionForValueError,
    action_key,
    coerce_state,
    default_profile,
    direction_problem,
    identity_hash,
    normalize,
    profile_catalog,
    profile_supported,
    profiles_for_domain,
    resolve,
    select_action,
)

INITIAL_DOMAINS = (
    "sensor",
    "binary_sensor",
    "switch",
    "light",
    "lock",
    "cover",
    "valve",
    "media_player",
    "number",
    "input_number",
    "select",
    "input_select",
    "text",
    "input_text",
    "button",
    "scene",
)


def _call(config: dict, value, logical=None):
    bc = HaBindingConfig(**config)
    call = select_action(bc, resolve(bc), value, value if logical is None else logical)
    return None if call is None else (call.domain, call.service, call.body)


def _read(config: dict, state, attributes=None):
    bc = HaBindingConfig(**config)
    obj = None if state is None else {"state": state, "attributes": attributes or {}}
    return normalize(resolve(bc), obj)


# ---------------------------------------------------------------------------
# Schema / migration compatibility
# ---------------------------------------------------------------------------


class TestBindingSchema:
    def test_legacy_config_still_valid(self):
        bc = HaBindingConfig(entity_id="input_number.t", attribute="x", service_domain="d", service_name="n", service_data_key="value")
        assert resolve(bc).mode == "legacy"

    def test_entity_id_is_trimmed(self):
        assert HaBindingConfig(entity_id="  sensor.temp ").entity_id == "sensor.temp"

    def test_entity_id_is_lowercased_like_home_assistant_does(self):
        # pre-#1392 bindings like "Light.Kitchen" worked: HA lowercases ids in actions
        assert HaBindingConfig(entity_id="Light.Kitchen").entity_id == "light.kitchen"

    def test_minimal_config_is_default_mode(self):
        assert resolve(HaBindingConfig(entity_id="switch.fan")).mode == "default"

    def test_empty_write_actions_mean_none_and_free_text_attributes_are_valid(self):
        bc = HaBindingConfig(entity_id="light.a", profile="light.on_off", write_actions={})
        assert bc.write_actions is None
        assert resolve(bc).mode == "profile"  # the profile's actions stay in force
        bc = HaBindingConfig(entity_id="lock.a", profile="lock.boolean_locked", state_map={})
        assert bc.state_map is None
        assert normalize(resolve(bc), {"state": "locked", "attributes": {}})[:2] == (True, "good")  # profile map stays
        assert HaBindingConfig(entity_id="sensor.bus", attribute="Next Departure").attribute == "Next Departure"

    @pytest.mark.parametrize(
        "config",
        [
            {"entity_id": "lock.x", "action": {"domain": "lock", "name": "lock"}, "write_actions": {"true": {"domain": "lock", "name": "lock"}}},
            {"entity_id": "lock.x", "attribute": "bad\nkey"},
            {"entity_id": "lock.x", "service_name": "lock", "action": {"domain": "lock", "name": "lock"}},
            {"entity_id": "lock.x", "service_domain": "lock", "write_actions": {"true": {"domain": "lock", "name": "lock"}}},
            {"entity_id": "lock.x", "profile": "nope"},
            {"entity_id": "cover.x", "profile": "lock.boolean_locked"},
            {"entity_id": "lock.x", "action": {"domain": "lock", "name": "lock", "target": {"evil": "x"}}},
            {"entity_id": "lock.x", "action": {"domain": "lock", "name": "lock", "data": {"a b": 1}}},
            {"entity_id": "lock.x", "action": {"domain": "Lock", "name": "lock"}},
            {"entity_id": "lock.x", "action": {"domain": "lock", "name": "lock", "value_path": "a..b"}},
            {"entity_id": "lock.x", "action": {"domain": "lock", "name": "lock", "template": "{{ x }}"}},
            {"entity_id": "lock.x", "action": {"domain": "lock", "name": "lock", "data": {"x": {"nested": "dict"}}}},
            {"entity_id": "lock.x", "ha_uid": "NOT-HEX"},
            {"entity_id": "sensor.my-temp"},
            {"entity_id": "sensor"},
            {"entity_id": "sensor.a.b"},
        ],
    )
    def test_invalid_mappings_rejected(self, config):
        with pytest.raises(ValidationError):
            HaBindingConfig(**config)

    def test_valid_declarative_action(self):
        bc = HaBindingConfig(
            entity_id="cover.x",
            action={
                "domain": "cover",
                "name": "set_cover_position",
                "target": {"area_id": "kitchen"},
                "data": {"a.b": [1, 2]},
                "value_path": "position",
            },
        )
        assert resolve(bc).mode == "action"

    def test_identity_hash(self):
        assert identity_hash("sonos", None) is None
        uid = identity_hash("sonos", "RINCON_1")
        assert len(uid) == 32 and uid == identity_hash("sonos", "RINCON_1") != identity_hash("hue", "RINCON_1")
        HaBindingConfig(entity_id="media_player.x", ha_uid=uid)


# ---------------------------------------------------------------------------
# Profile table
# ---------------------------------------------------------------------------


class TestProfileTable:
    @pytest.mark.parametrize("domain", INITIAL_DOMAINS)
    def test_every_initial_domain_has_profiles(self, domain):
        assert profiles_for_domain(domain)

    @pytest.mark.parametrize("profile_id", sorted(PROFILES))
    def test_every_profile_resolves_to_a_valid_binding(self, profile_id):
        profile = PROFILES[profile_id]
        bc = HaBindingConfig(entity_id=f"{profile.domains[0]}.x", profile=profile_id)
        mapping = resolve(bc)
        assert mapping.mode == "profile"
        writes = profile.direction in ("DEST", "BOTH")
        assert (mapping.action is not None or mapping.write_actions is not None) is writes
        if profile.direction == "DEST":
            assert profile.attribute is None and profile.state_map is None

    def test_catalog_is_serializable_and_complete(self):
        catalog = profile_catalog()
        assert {p["id"] for p in catalog} == set(PROFILES)
        lock = next(p for p in catalog if p["id"] == "lock.boolean_locked")
        assert lock["write_actions"]["true"] == {"domain": None, "name": "lock", "value_path": None}
        assert lock["convention"] == "trueLocked"
        cover = next(p for p in catalog if p["id"] == "cover.position")
        assert cover["action"]["value_path"] == "position"

    def test_ambiguous_domains_get_no_silent_write_default(self):
        for domain in AMBIGUOUS_DOMAINS:
            proposed = default_profile(domain, {"state": "on", "attributes": {}})
            assert proposed is None or proposed.direction == "SOURCE"

    @pytest.mark.parametrize(
        ("domain", "attrs", "expected"),
        [
            ("cover", {"supported_features": 15, "current_position": 0}, "cover.boolean_open"),
            ("cover", {"supported_features": 4, "current_position": 0}, "cover.position"),
            ("cover", {"supported_features": 3}, "cover.boolean_open"),
            ("cover", {}, "cover.boolean_open"),
            ("valve", {"supported_features": 15, "current_position": 3}, "valve.boolean_open"),
            ("valve", {"supported_features": 4, "current_position": 3}, "valve.position"),
            ("valve", {"supported_features": 3}, "valve.boolean_open"),
            ("lock", {}, "lock.boolean_locked"),
            ("number", {}, "number.value"),
            ("input_select", {}, "select.option"),
            ("input_text", {}, "text.value"),
            ("button", {}, "button.press"),
            ("scene", {}, "scene.activate"),
            ("media_player", {}, "media_player.state"),
            ("climate", {}, None),
        ],
    )
    def test_default_profile(self, domain, attrs, expected):
        proposed = default_profile(domain, {"state": "x", "attributes": attrs})
        assert (proposed.id if proposed else None) == expected

    def test_profile_supported_reasons(self):
        assert profile_supported(PROFILES["lock.open"], {"attributes": {"supported_features": 0}}) == (False, "featureMissing")
        assert profile_supported(PROFILES["lock.open"], {"attributes": {"supported_features": 1}}) == (True, None)
        assert profile_supported(PROFILES["lock.open"], {"attributes": {}}) == (True, None)
        assert profile_supported(PROFILES["cover.position"], {"attributes": {}}) == (False, "attributeMissing")
        assert profile_supported(PROFILES["cover.position"], None) == (True, None)
        # an off light has no brightness attribute but can still be dimmed
        assert profile_supported(PROFILES["light.brightness"], {"state": "off", "attributes": {"supported_color_modes": ["brightness"]}}) == (
            True,
            None,
        )
        assert profile_supported(PROFILES["light.brightness"], {"attributes": {"supported_color_modes": ["onoff"]}}) == (False, "featureMissing")


# ---------------------------------------------------------------------------
# Inbound normalization
# ---------------------------------------------------------------------------


class TestNormalize:
    @pytest.mark.parametrize(
        ("config", "state", "attrs", "expected"),
        [
            # legacy coercion
            ({"entity_id": "sensor.t"}, "21.5", None, (21.5, "good", True)),
            ({"entity_id": "switch.t"}, "on", None, (True, "good", True)),
            ({"entity_id": "sensor.t"}, "text", None, ("text", "good", True)),
            ({"entity_id": "sensor.t", "attribute": "x"}, "1", {"x": [1]}, ([1], "good", True)),
            ({"entity_id": "sensor.t", "attribute": "x"}, "1", {}, (None, "good", True)),
            # quality contract
            ({"entity_id": "sensor.t"}, None, None, (None, "bad", False)),
            ({"entity_id": "sensor.t"}, "unavailable", None, (None, "bad", False)),
            ({"entity_id": "sensor.t"}, "unknown", None, (None, "uncertain", False)),
            # lock profile
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked"}, "locked", None, (True, "good", True)),
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked"}, "unlocked", None, (False, "good", True)),
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked"}, "open", None, (False, "good", True)),
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked"}, "locking", None, (None, "uncertain", False)),
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked"}, "unlocking", None, (None, "uncertain", False)),
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked"}, "opening", None, (None, "uncertain", False)),
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked"}, "jammed", None, (None, "bad", False)),
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked"}, "other", None, (None, "uncertain", False)),
            # cover / valve
            ({"entity_id": "cover.t", "profile": "cover.boolean_open"}, "open", None, (True, "good", True)),
            ({"entity_id": "cover.t", "profile": "cover.boolean_open"}, "closed", None, (False, "good", True)),
            ({"entity_id": "cover.t", "profile": "cover.boolean_open"}, "closing", None, (None, "uncertain", False)),
            ({"entity_id": "cover.t", "profile": "cover.position"}, "opening", {"current_position": 30}, (30, "good", True)),
            ({"entity_id": "cover.t", "profile": "cover.position"}, "open", {"current_position": "x"}, (None, "uncertain", False)),
            ({"entity_id": "cover.t", "profile": "cover.position"}, "open", {}, (None, "uncertain", False)),
            ({"entity_id": "cover.t", "profile": "cover.position"}, "open", {"current_position": None}, (None, "uncertain", False)),
            ({"entity_id": "media_player.t", "profile": "media_player.mute"}, "on", {}, (None, "uncertain", False)),
            ({"entity_id": "media_player.t", "profile": "media_player.mute"}, "on", {"is_volume_muted": 1}, (None, "uncertain", False)),
            ({"entity_id": "media_player.t", "profile": "media_player.title"}, "on", {}, (None, "good", True)),
            ({"entity_id": "media_player.t", "profile": "media_player.title"}, "on", {"media_title": 5}, ("5", "good", True)),
            ({"entity_id": "sensor.t", "read_as": "string"}, "21", None, ("21", "good", True)),
            ({"entity_id": "sensor.t", "read_as": "number"}, "eco", None, (None, "uncertain", False)),
            ({"entity_id": "automation.t", "read_as": "boolean"}, "on", None, (True, "good", True)),
            ({"entity_id": "automation.t", "read_as": "boolean"}, "false", None, (False, "good", True)),
            ({"entity_id": "automation.t", "read_as": "boolean"}, "idle", None, (None, "uncertain", False)),
            ({"entity_id": "sensor.t", "attribute": "x", "read_as": "boolean"}, "1", {"x": False}, (False, "good", True)),
            ({"entity_id": "sensor.t", "attribute": "x", "read_as": "boolean"}, "1", {"x": 0}, (None, "uncertain", False)),
            ({"entity_id": "sensor.t", "attribute": "x", "read_as": "auto"}, "1", {"x": None}, (None, "good", True)),
            ({"entity_id": "valve.t", "profile": "valve.boolean_open"}, "open", None, (True, "good", True)),
            # number / string kinds
            ({"entity_id": "number.t", "profile": "number.value"}, "4.5", None, (4.5, "good", True)),
            ({"entity_id": "number.t", "profile": "number.value"}, "abc", None, (None, "uncertain", False)),
            ({"entity_id": "select.t", "profile": "select.option"}, "1", None, ("1", "good", True)),
            ({"entity_id": "text.t", "profile": "text.value"}, "on", None, ("on", "good", True)),
            ({"entity_id": "media_player.t", "profile": "media_player.state"}, "on", None, ("on", "good", True)),
            ({"entity_id": "media_player.t", "profile": "media_player.volume"}, "on", {"volume_level": 0.25}, (0.25, "good", True)),
            ({"entity_id": "media_player.t", "profile": "media_player.mute"}, "on", {"is_volume_muted": True}, (True, "good", True)),
            ({"entity_id": "binary_sensor.t", "profile": "binary_sensor.state"}, "off", None, (False, "good", True)),
            # explicit inbound overrides beat the profile
            (
                {
                    "entity_id": "lock.t",
                    "profile": "lock.boolean_locked",
                    "state_map": {"locked": 1},
                    "transitional_states": ["x"],
                    "fault_states": ["y"],
                },
                "y",
                None,
                (None, "bad", False),
            ),
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked", "state_map": {"locked": 1}}, "locked", None, (1, "good", True)),
            ({"entity_id": "cover.t", "profile": "cover.position", "attribute": "other"}, "open", {"other": 7}, (7, "good", True)),
        ],
    )
    def test_table(self, config, state, attrs, expected):
        assert _read(config, state, attrs) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("on", True), ("true", True), ("off", False), ("false", False), ("42", 42), ("2.5", 2.5), ("x", "x"), (None, None)],
    )
    def test_coerce_state(self, raw, expected):
        assert coerce_state(raw) == expected

    @pytest.mark.parametrize("raw", [True, math.inf, "nan", "inf"])
    def test_non_finite_and_bool_numbers_are_uncertain(self, raw):
        bc = HaBindingConfig(entity_id="cover.t", profile="cover.position")
        assert normalize(resolve(bc), {"state": "open", "attributes": {"current_position": raw}}) == (None, "uncertain", False)

    def test_state_map_applies_to_the_attribute_value(self):
        cfg = {"entity_id": "climate.x", "attribute": "hvac_action", "state_map": {"heating": True, "idle": False, "true": 1, "null": 0}}
        assert _read(cfg, "heat", {"hvac_action": "heating"}) == (True, "good", True)
        assert _read(cfg, "heat", {"hvac_action": "idle"}) == (False, "good", True)
        assert _read(cfg, "heat", {"hvac_action": True}) == (1, "good", True)  # non-text values as JSON
        assert _read(cfg, "heat", {}) == (0, "good", True)
        assert _read(cfg, "heat", {"hvac_action": "cooling"}) == (None, "uncertain", False)

    def test_profile_value_type_only_applies_to_the_profile_value(self):
        power = {"entity_id": "switch.x", "profile": "switch.on_off", "attribute": "current_power_w"}
        assert _read(power, "on", {"current_power_w": 85}) == (85, "good", True)  # auto, not the profile's Boolean
        assert _read({"entity_id": "switch.x", "profile": "switch.on_off"}, "on") == (True, "good", True)
        assert _read({"entity_id": "cover.x", "profile": "cover.position", "attribute": "current_position"}, "open", {"current_position": "40"}) == (
            40,
            "good",
            True,
        )

    def test_profile_state_map_only_applies_to_the_profile_value(self):
        assert _read(
            {"entity_id": "lock.x", "profile": "lock.boolean_locked", "attribute": "battery", "read_as": "number"}, "locked", {"battery": 80}
        ) == (80, "good", True)
        assert _read({"entity_id": "lock.x", "profile": "lock.boolean_locked"}, "locked") == (True, "good", True)

    def test_numeric_attribute_from_string_and_int(self):
        bc = HaBindingConfig(entity_id="cover.t", profile="cover.position")
        assert normalize(resolve(bc), {"state": "open", "attributes": {"current_position": "12"}}) == (12, "good", True)
        assert normalize(resolve(bc), {"state": "open", "attributes": {"current_position": "12.5"}}) == (12.5, "good", True)


# ---------------------------------------------------------------------------
# Outbound action selection
# ---------------------------------------------------------------------------


class TestSelectAction:
    @pytest.mark.parametrize(
        ("profile", "entity", "value", "expected"),
        [
            ("lock.boolean_locked", "lock.t", True, ("lock", "lock", {"entity_id": "lock.t"})),
            ("lock.boolean_locked", "lock.t", False, ("lock", "unlock", {"entity_id": "lock.t"})),
            ("lock.open", "lock.t", True, ("lock", "open", {"entity_id": "lock.t"})),
            ("lock.open", "lock.t", False, None),
            ("cover.boolean_open", "cover.t", True, ("cover", "open_cover", {"entity_id": "cover.t"})),
            ("cover.boolean_open", "cover.t", False, ("cover", "close_cover", {"entity_id": "cover.t"})),
            ("cover.position", "cover.t", 40, ("cover", "set_cover_position", {"entity_id": "cover.t", "position": 40})),
            ("cover.tilt_position", "cover.t", 10, ("cover", "set_cover_tilt_position", {"entity_id": "cover.t", "tilt_position": 10})),
            ("cover.stop", "cover.t", True, ("cover", "stop_cover", {"entity_id": "cover.t"})),
            ("cover.stop", "cover.t", False, None),
            ("valve.boolean_open", "valve.t", True, ("valve", "open_valve", {"entity_id": "valve.t"})),
            ("valve.boolean_open", "valve.t", False, ("valve", "close_valve", {"entity_id": "valve.t"})),
            ("valve.position", "valve.t", 5, ("valve", "set_valve_position", {"entity_id": "valve.t", "position": 5})),
            ("valve.stop", "valve.t", True, ("valve", "stop_valve", {"entity_id": "valve.t"})),
            ("switch.on_off", "input_boolean.t", True, ("input_boolean", "turn_on", {"entity_id": "input_boolean.t"})),
            ("light.on_off", "light.t", False, ("light", "turn_off", {"entity_id": "light.t"})),
            ("light.brightness", "light.t", 128, ("light", "turn_on", {"entity_id": "light.t", "brightness": 128})),
            ("number.value", "input_number.t", 3.5, ("input_number", "set_value", {"entity_id": "input_number.t", "value": 3.5})),
            ("select.option", "select.t", "eco", ("select", "select_option", {"entity_id": "select.t", "option": "eco"})),
            ("text.value", "input_text.t", "hi", ("input_text", "set_value", {"entity_id": "input_text.t", "value": "hi"})),
            ("button.press", "button.t", True, ("button", "press", {"entity_id": "button.t"})),
            ("button.press", "button.t", False, None),
            ("scene.activate", "scene.t", True, ("scene", "turn_on", {"entity_id": "scene.t"})),
            ("scene.activate", "scene.t", False, None),
            ("media_player.play_pause", "media_player.t", True, ("media_player", "media_play", {"entity_id": "media_player.t"})),
            ("media_player.play_pause", "media_player.t", False, ("media_player", "media_pause", {"entity_id": "media_player.t"})),
            ("media_player.power", "media_player.t", False, ("media_player", "turn_off", {"entity_id": "media_player.t"})),
            ("media_player.stop", "media_player.t", True, ("media_player", "media_stop", {"entity_id": "media_player.t"})),
            ("media_player.next", "media_player.t", True, ("media_player", "media_next_track", {"entity_id": "media_player.t"})),
            ("media_player.previous", "media_player.t", True, ("media_player", "media_previous_track", {"entity_id": "media_player.t"})),
            ("media_player.volume", "media_player.t", 0.4, ("media_player", "volume_set", {"entity_id": "media_player.t", "volume_level": 0.4})),
            ("media_player.mute", "media_player.t", True, ("media_player", "volume_mute", {"entity_id": "media_player.t", "is_volume_muted": True})),
            ("media_player.source", "media_player.t", "TV", ("media_player", "select_source", {"entity_id": "media_player.t", "source": "TV"})),
            ("media_player.title", "media_player.t", "x", None),
            ("sensor.value", "sensor.t", 1, None),
        ],
    )
    def test_profile_actions(self, profile, entity, value, expected):
        assert _call({"entity_id": entity, "profile": profile}, value) == expected

    def test_action_chosen_from_logical_value_payload_from_transformed(self):
        config = {"entity_id": "lock.t", "profile": "lock.boolean_locked"}
        # a value_map turning True into "1" must not change which action runs
        assert _call(config, "1", logical=True) == ("lock", "lock", {"entity_id": "lock.t"})
        config = {"entity_id": "cover.t", "profile": "cover.position"}
        assert _call(config, 60, logical=40) == ("cover", "set_cover_position", {"entity_id": "cover.t", "position": 60})

    def test_explicit_action_overrides_profile(self):
        config = {"entity_id": "lock.t", "profile": "lock.boolean_locked", "action": {"domain": "lock", "name": "open"}}
        assert _call(config, True) == ("lock", "open", {"entity_id": "lock.t"})
        config = {
            "entity_id": "lock.t",
            "profile": "lock.boolean_locked",
            "write_actions": {"true": {"domain": "script", "name": "turn_on", "target": {"entity_id": "script.a"}}},
        }
        assert _call(config, True) == ("script", "turn_on", {"entity_id": "script.a"})
        assert _call(config, False) is None

    def test_nested_data_paths_merge(self):
        config = {"entity_id": "a.b", "action": {"domain": "a", "name": "b", "target": None, "data": {"x.y": 1, "x.z": 2}}}
        assert _call(config, 5) == ("a", "b", {"entity_id": "a.b", "x": {"y": 1, "z": 2}})

    @pytest.mark.parametrize(
        ("config", "value", "expected"),
        [
            ({"entity_id": "switch.f"}, True, ("switch", "turn_on", {"entity_id": "switch.f"})),
            ({"entity_id": "switch.f"}, False, ("switch", "turn_off", {"entity_id": "switch.f"})),
            ({"entity_id": "input_number.f"}, 3, ("input_number", "set_value", {"entity_id": "input_number.f"})),
            (
                {"entity_id": "input_number.f", "service_data_key": "value"},
                3,
                ("input_number", "set_value", {"entity_id": "input_number.f", "value": 3}),
            ),
            ({"entity_id": "switch.f", "service_data_key": "value"}, True, ("switch", "turn_on", {"entity_id": "switch.f"})),
            (
                {"entity_id": "switch.f", "service_name": "toggle", "service_domain": "homeassistant"},
                1,
                ("homeassistant", "toggle", {"entity_id": "switch.f"}),
            ),
        ],
    )
    def test_legacy_and_default(self, config, value, expected):
        assert _call(config, value) == expected

    def test_value_path_overwrites_non_dict_intermediate(self):
        config = {"entity_id": "a.b", "action": {"domain": "a", "name": "b", "data": {"x": 1}, "value_path": "x.y"}}
        assert _call(config, 5) == ("a", "b", {"entity_id": "a.b", "x": {"y": 5}})

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1, ("switch", "turn_on", {"entity_id": "switch.t"})),
            (0, ("switch", "turn_off", {"entity_id": "switch.t"})),
            (1.0, ("switch", "turn_on", {"entity_id": "switch.t"})),
            (True, ("switch", "turn_on", {"entity_id": "switch.t"})),
        ],
    )
    def test_boolean_profile_on_numeric_datapoint(self, value, expected):
        assert _call({"entity_id": "switch.t", "profile": "switch.on_off"}, value) == expected

    def test_command_profile_numeric_zero_is_a_no_op(self):
        assert _call({"entity_id": "button.t", "profile": "button.press"}, 0) is None
        assert _call({"entity_id": "button.t", "profile": "button.press"}, 1) == ("button", "press", {"entity_id": "button.t"})

    @pytest.mark.parametrize(
        ("config", "value"),
        [
            ({"entity_id": "switch.t", "profile": "switch.on_off"}, 5),
            ({"entity_id": "switch.t", "profile": "switch.on_off"}, "1"),
            ({"entity_id": "switch.t", "profile": "switch.on_off"}, "on"),
            ({"entity_id": "select.t", "write_actions": {"eco": {"domain": "select", "name": "x"}}}, "comfort"),
        ],
    )
    def test_unmatched_values_raise(self, config, value):
        with pytest.raises(NoActionForValueError):
            _call(config, value)

    @pytest.mark.parametrize(("value", "key"), [(True, "true"), (False, "false"), (2.0, "2"), (2.5, "2.5"), (3, "3"), ("eco", "eco")])
    def test_action_key(self, value, key):
        assert action_key(value) == key


class TestDirection:
    @pytest.mark.parametrize(
        ("config", "direction", "problem"),
        [
            ({"entity_id": "sensor.t", "profile": "sensor.value"}, "SOURCE", False),
            ({"entity_id": "sensor.t", "profile": "sensor.value"}, "DEST", True),
            ({"entity_id": "sensor.t", "profile": "sensor.value"}, "BOTH", True),
            ({"entity_id": "sensor.t", "profile": "sensor.value", "service_name": "x"}, "BOTH", False),
            ({"entity_id": "sensor.t", "profile": "sensor.value", "action": {"domain": "a", "name": "b"}}, "DEST", False),
            ({"entity_id": "sensor.t", "profile": "sensor.value", "write_actions": {}}, "DEST", True),  # {} is no action
            ({"entity_id": "cover.t", "profile": "cover.stop"}, "DEST", False),
            ({"entity_id": "cover.t", "profile": "cover.stop"}, "SOURCE", True),
            ({"entity_id": "cover.t", "profile": "cover.stop"}, "BOTH", True),
            ({"entity_id": "cover.t", "profile": "cover.stop", "attribute": "current_position"}, "BOTH", False),
            ({"entity_id": "cover.t", "profile": "cover.stop", "state_map": {"open": True}}, "SOURCE", False),
            ({"entity_id": "lock.t", "profile": "lock.boolean_locked"}, "BOTH", False),
            ({"entity_id": "lock.t"}, "DEST", False),
        ],
    )
    def test_direction_problem(self, config, direction, problem):
        assert (direction_problem(HaBindingConfig(**config), direction) is not None) is problem


class TestMissingAttributeValues:
    @pytest.mark.parametrize(
        ("state", "attrs", "expected"),
        [
            ("off", {}, (0, "good", True)),  # HA drops brightness while off
            ("off", {"brightness": None}, (0, "good", True)),
            ("on", {}, (None, "uncertain", False)),
            ("on", {"brightness": 128}, (128, "good", True)),
        ],
    )
    def test_light_brightness(self, state, attrs, expected):
        assert _read({"entity_id": "light.t", "profile": "light.brightness"}, state, attrs) == expected

    def test_only_for_the_profiles_own_attribute(self):
        # an explicit other attribute inherits neither brightness' "off" value nor its type:
        # it reads like a manual attribute binding
        assert _read({"entity_id": "light.t", "profile": "light.brightness", "attribute": "color_temp"}, "off", {}) == (None, "good", True)
        assert _read({"entity_id": "media_player.t", "profile": "media_player.volume"}, "off", {}) == (None, "uncertain", False)
