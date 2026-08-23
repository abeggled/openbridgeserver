from __future__ import annotations

from obs.logic.nodes.logic.edge_detect import NODE_TYPE


def test_category_is_logic_and_colour_matches_the_other_logic_blocks():
    assert NODE_TYPE.type == "edge_detect"
    assert NODE_TYPE.category == "logic"
    assert NODE_TYPE.color == "#1d4ed8"


def test_value_input_and_reset_trigger_are_declared():
    assert [(p.id, p.type) for p in NODE_TYPE.inputs] == [("in", "value"), ("reset", "trigger")]


def test_edge_outputs_are_one_value_and_two_triggers():
    assert [(p.id, p.type) for p in NODE_TYPE.outputs] == [
        ("out", "value"),
        ("rising", "trigger"),
        ("falling", "trigger"),
    ]


def test_trigger_outputs_are_prefixed_to_separate_them_from_the_config_fields():
    # The on_rising/on_falling settings are labelled "Steigende/Fallende
    # Flanke"; without the prefix the ports read as the same thing.
    labels = {p.id: p.label for p in NODE_TYPE.outputs}

    assert labels["rising"] == "Trigger-Steigend"
    assert labels["falling"] == "Trigger-Fallend"


def test_each_edge_direction_has_one_setting_defaulting_to_trigger_and_value():
    schema = NODE_TYPE.config_schema

    for field in ("on_rising", "on_falling"):
        assert schema[field]["type"] == "string"
        # "value" pulses the trigger AND sends, "trigger" pulses only, "off"
        # stays silent — one setting answers the whole question per direction.
        assert schema[field]["enum"] == ["value", "trigger", "off"]
        assert schema[field]["default"] == "value"


def test_no_separate_edge_selector_or_send_switches_remain():
    # A "which edge" enum next to per-edge send switches overlapped: "only
    # rising" and "do not send on falling" differed solely on the falling
    # trigger, which reads as a contradiction in the editor.
    assert "mode" not in NODE_TYPE.config_schema
    assert "send_on_rising" not in NODE_TYPE.config_schema
    assert "send_on_falling" not in NODE_TYPE.config_schema


def test_edge_values_default_to_true_and_false_typed_as_bool():
    schema = NODE_TYPE.config_schema

    assert schema["value_rising"]["default"] == "true"
    assert schema["value_falling"]["default"] == "false"
    # The factory values are the strings "true"/"false" and must reach a Write
    # Object as real booleans, so bool is the default. Memory's "auto" is
    # deliberately not offered: every edge value has one definite type here.
    assert schema["data_type"]["default"] == "bool"
    assert schema["data_type"]["enum"] == ["bool", "number", "string"]


def test_edge_values_declare_the_field_that_types_them():
    # Lets the editor pick the right widget (true/false dropdown, number input,
    # free text) without NodeConfigPanel knowing about this block.
    schema = NODE_TYPE.config_schema

    assert schema["value_rising"]["value_type_field"] == "data_type"
    assert schema["value_falling"]["value_type_field"] == "data_type"


def test_each_edge_value_is_hidden_unless_its_direction_sends_one():
    schema = NODE_TYPE.config_schema

    # Stated as an exclusion, matching how the executor decides it: anything
    # that is not off/trigger sends, so a legacy or future setting keeps its
    # value field rather than having it silently hidden.
    assert schema["value_rising"]["visible_when"] == {"field": "on_rising", "not_in": ["off", "trigger"]}
    assert schema["value_falling"]["visible_when"] == {"field": "on_falling", "not_in": ["off", "trigger"]}


def test_persist_state_defaults_to_true():
    assert NODE_TYPE.config_schema["persist_state"]["type"] == "boolean"
    assert NODE_TYPE.config_schema["persist_state"]["default"] is True
