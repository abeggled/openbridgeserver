from __future__ import annotations

from obs.logic.nodes.logic.merge import NODE_TYPE


def test_input_count_is_an_integer_between_two_and_thirty():
    input_count = NODE_TYPE.config_schema["input_count"]

    assert input_count["type"] == "integer"
    assert input_count["default"] == 2
    assert (input_count["min"], input_count["max"]) == (2, 30)


def test_two_inputs_and_one_output_are_pre_declared():
    assert [port.id for port in NODE_TYPE.inputs] == ["in1", "in2"]
    assert [port.id for port in NODE_TYPE.outputs] == ["out"]


def test_persist_state_defaults_to_true():
    assert NODE_TYPE.config_schema["persist_state"]["type"] == "boolean"
    assert NODE_TYPE.config_schema["persist_state"]["default"] is True


def test_category_is_logic():
    assert NODE_TYPE.category == "logic"
