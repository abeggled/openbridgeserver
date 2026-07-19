from __future__ import annotations

import json

from obs.logic.node_types import BUILTIN_NODE_TYPES


def _node_type(type_name: str):
    return next(node_type for node_type in BUILTIN_NODE_TYPES if node_type.type == type_name)


def test_decision_default_conditions_do_not_persist_localized_names():
    decision = _node_type("decision")
    conditions = json.loads(decision.config_schema["conditions"]["default"])

    assert conditions == [
        {"handle": "out_1", "operator": "eq"},
        {"handle": "out_2", "operator": "eq"},
    ]


def test_value_mapping_default_rules_do_not_persist_localized_names():
    mapping = _node_type("value_mapping")
    rules = json.loads(mapping.config_schema["rules"]["default"])

    assert rules == [
        {"operator": "eq", "result": ""},
        {"operator": "eq", "result": ""},
    ]


def test_comment_node_has_no_ports():
    comment = _node_type("comment")

    assert comment.inputs == []
    assert comment.outputs == []
    assert comment.config_schema["text"]["default"] == ""
    assert comment.config_schema["width"]["default"] == 220
    assert comment.config_schema["height"]["default"] == 140
