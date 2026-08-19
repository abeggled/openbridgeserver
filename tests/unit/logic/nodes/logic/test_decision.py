from __future__ import annotations

import json

from obs.logic.nodes.logic.decision import NODE_TYPE


def test_decision_default_conditions_do_not_persist_localized_names():
    conditions = json.loads(NODE_TYPE.config_schema["conditions"]["default"])

    assert conditions == [
        {"handle": "out_1", "operator": "eq"},
        {"handle": "out_2", "operator": "eq"},
    ]


def test_decision_outputs_one_boolean_handle_per_default_condition():
    assert [port.id for port in NODE_TYPE.outputs] == ["out_1", "out_2"]
    assert [port.id for port in NODE_TYPE.inputs] == ["value"]
