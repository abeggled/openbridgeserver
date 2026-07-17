from __future__ import annotations

import json

import pytest

from obs.logic.models import FlowData, LogicGraphCreate, LogicGraphImport, LogicGraphUpdate, LogicNode
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
    assert _node_type("api_client").config_schema["timeout_s"]["min"] == 1
    assert _node_type("and").config_schema["input_count"]["type"] == "integer"
    assert _node_type("or").config_schema["input_count"]["type"] == "integer"
    assert _node_type("xor").config_schema["input_count"]["type"] == "integer"


@pytest.mark.parametrize(
    ("node_type", "data", "message"),
    [
        ("timer_delay", {"delay_s": -1}, "greater than or equal to 0"),
        ("timer_pulse", {"duration_s": "-0.5"}, "greater than or equal to 0"),
        ("api_client", {"timeout_s": 0}, "greater than or equal to 1"),
        ("api_client", {"timeout_s": "bad"}, "must be a number"),
        ("api_client", {"timeout_s": " "}, "must be a number"),
        ("timer_delay", {"delay_s": "bad"}, "must be a number"),
        ("timer_delay", {"delay_s": 10**400}, "must be a finite number"),
    ],
)
def test_write_validation_rejects_invalid_durations(node_type, data, message):
    from fastapi import HTTPException

    from obs.api.v1.logic import _validate_timer_durations

    flow_data = FlowData(nodes=[LogicNode(id="node", type=node_type, position={"x": 0, "y": 0}, data=data)])

    with pytest.raises(HTTPException, match=message) as exc_info:
        _validate_timer_durations(flow_data)
    assert exc_info.value.status_code == 422


@pytest.mark.parametrize(
    ("node_type", "data"),
    [
        ("timer_delay", {"delay_s": 0}),
        ("timer_pulse", {"duration_s": "1.5"}),
        ("timer_delay", {"delay_s": ""}),
        ("timer_delay", {"delay_s": None}),
        ("timer_cron", {"delay_s": -1}),
        ("api_client", {"timeout_s": 1}),
        ("api_client", {"timeout_s": ""}),
    ],
)
def test_write_validation_allows_non_negative_or_unrelated_timer_values(node_type, data):
    from obs.api.v1.logic import _validate_timer_durations

    flow_data = FlowData(nodes=[LogicNode(id="node", type=node_type, position={"x": 0, "y": 0}, data=data)])

    _validate_timer_durations(flow_data)


@pytest.mark.parametrize(
    ("request_model", "payload"),
    [
        (LogicGraphCreate, {"name": "Graph"}),
        (LogicGraphUpdate, {}),
        (LogicGraphImport, {"obs_export": "logic_graph", "version": 1, "name": "Graph"}),
    ],
)
def test_graph_request_models_allow_existing_negative_timer_durations(request_model, payload):
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

    graph = request_model.model_validate(payload)

    assert graph.flow_data.nodes[0].data["delay_s"] == -1


def test_persisted_negative_timer_durations_remain_readable():
    from obs.api.v1.logic import _row_to_out

    graph = _row_to_out(
        {
            "id": "graph",
            "name": "Graph",
            "description": "",
            "enabled": 1,
            "flow_data": json.dumps(
                {
                    "nodes": [
                        {
                            "id": "timer",
                            "type": "timer_pulse",
                            "position": {"x": 0, "y": 0},
                            "data": {"duration_s": -1},
                        }
                    ]
                }
            ),
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
    )

    assert graph.flow_data.nodes[0].data["duration_s"] == -1
