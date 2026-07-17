from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from obs.logic.models import LogicGraphCreate, LogicGraphImport, LogicGraphUpdate, LogicNode
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


def test_timer_durations_are_non_negative():
    assert _node_type("timer_delay").config_schema["delay_s"]["min"] == 0
    assert _node_type("timer_pulse").config_schema["duration_s"]["min"] == 0


@pytest.mark.parametrize(
    ("node_type", "data"),
    [
        ("timer_delay", {"delay_s": 0}),
        ("timer_pulse", {"duration_s": "1.5"}),
        ("timer_delay", {"delay_s": ""}),
        ("timer_cron", {"delay_s": -1}),
    ],
)
def test_timer_duration_validation_allows_non_negative_or_unrelated_values(node_type, data):
    node = LogicNode(id="node", type=node_type, position={"x": 0, "y": 0}, data=data)

    assert node.data == data


@pytest.mark.parametrize(
    ("node_type", "data"),
    [
        ("timer_delay", {"delay_s": -1}),
        ("timer_pulse", {"duration_s": "-0.5"}),
    ],
)
def test_timer_duration_validation_rejects_negative_values(node_type, data):
    with pytest.raises(ValidationError, match="must be greater than or equal to 0"):
        LogicNode(id="node", type=node_type, position={"x": 0, "y": 0}, data=data)


@pytest.mark.parametrize(
    ("request_model", "payload"),
    [
        (LogicGraphCreate, {"name": "Graph"}),
        (LogicGraphUpdate, {}),
        (LogicGraphImport, {"obs_export": "logic_graph", "version": 1, "name": "Graph"}),
    ],
)
def test_api_graph_request_models_reject_negative_timer_durations(request_model, payload):
    payload["flow_data"] = {
        "nodes": [
            {
                "id": "timer",
                "type": "timer_delay",
                "position": {"x": 0, "y": 0},
                "data": {"delay_s": -1},
            }
        ]
    }

    with pytest.raises(ValidationError, match="must be greater than or equal to 0"):
        request_model.model_validate(payload)
