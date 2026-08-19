from __future__ import annotations

import pytest

from obs.logic.nodes.logic.and_node import NODE_TYPE as AND
from obs.logic.nodes.logic.not_node import NODE_TYPE as NOT
from obs.logic.nodes.logic.or_node import NODE_TYPE as OR
from obs.logic.nodes.logic.xor_node import NODE_TYPE as XOR


@pytest.mark.parametrize("node_type", [AND, OR, XOR], ids=lambda node_type: node_type.type)
def test_input_count_is_an_integer_between_two_and_thirty(node_type):
    input_count = node_type.config_schema["input_count"]

    assert input_count["type"] == "integer"
    assert input_count["default"] == 2
    assert (input_count["min"], input_count["max"]) == (2, 30)


@pytest.mark.parametrize("node_type", [AND, OR, XOR], ids=lambda node_type: node_type.type)
def test_two_inputs_are_pre_declared(node_type):
    assert [port.id for port in node_type.inputs] == ["in1", "in2"]
    assert [port.id for port in node_type.outputs] == ["out"]


def test_not_is_a_single_input_block_without_input_count():
    assert [port.id for port in NOT.inputs] == ["in1"]
    assert "input_count" not in NOT.config_schema
