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


def test_mode_selects_which_edge_is_reported_and_defaults_to_both():
    mode = NODE_TYPE.config_schema["mode"]

    assert mode["type"] == "string"
    assert mode["enum"] == ["both", "rising", "falling"]
    assert mode["default"] == "both"


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


def test_sending_on_either_edge_is_enabled_by_default():
    schema = NODE_TYPE.config_schema

    assert schema["send_on_rising"]["type"] == "boolean"
    assert schema["send_on_rising"]["default"] is True
    assert schema["send_on_falling"]["type"] == "boolean"
    assert schema["send_on_falling"]["default"] is True


def test_persist_state_defaults_to_true():
    assert NODE_TYPE.config_schema["persist_state"]["type"] == "boolean"
    assert NODE_TYPE.config_schema["persist_state"]["default"] is True
