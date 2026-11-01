"""Timer/duration validation on the persistence and API boundary.

Assertions about a single node definition live next to that node under
``tests/unit/logic/nodes/<category>/``; this file covers the shared validation
that spans several node types.
"""

from __future__ import annotations

import json

import pytest

from obs.logic.models import FlowData, LogicGraphCreate, LogicGraphImport, LogicGraphUpdate, LogicNode


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
        # #1377: an unusable duration under the sibling's key is not adopted, so not rejected.
        ("timer_delay", {"duration_s": -1}),
        ("timer_pulse", {"delay_s": "bad"}),
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


@pytest.mark.parametrize(
    ("node_type", "data", "expected"),
    [
        # #1377: the sibling's key alone is adopted under the key the block reads.
        ("timer_delay", {"duration_s": 8}, {"delay_s": 8}),
        ("timer_delay", {"duration_s": 8, "delay_s": None}, {"delay_s": 8}),
        ("timer_delay", {"duration_s": "8"}, {"delay_s": "8"}),
        ("timer_delay", {"duration_s": 0}, {"delay_s": 0}),
        ("timer_pulse", {"delay_s": 2, "label": "Flur"}, {"label": "Flur", "duration_s": 2}),
        # An empty own key is a deliberate choice of the default: the sibling stays a stray key.
        ("timer_pulse", {"duration_s": "", "delay_s": 30}, {"duration_s": "", "delay_s": 30}),
        # The block's own duration wins; a stray sibling beside it stays untouched.
        ("timer_delay", {"delay_s": 2, "duration_s": 8}, {"delay_s": 2, "duration_s": 8}),
        ("timer_pulse", {"duration_s": 0, "delay_s": 8}, {"duration_s": 0, "delay_s": 8}),
        # A sibling value that is no usable duration is left as it was (the block keeps its default).
        ("timer_delay", {"duration_s": None}, {"duration_s": None}),
        ("timer_delay", {"duration_s": ""}, {"duration_s": ""}),
        ("timer_delay", {"duration_s": "bad"}, {"duration_s": "bad"}),
        ("timer_delay", {"duration_s": [8]}, {"duration_s": [8]}),
        ("timer_delay", {"duration_s": 10**400}, {"duration_s": 10**400}),
        ("timer_delay", {"duration_s": "inf"}, {"duration_s": "inf"}),
        ("timer_delay", {"duration_s": -1}, {"duration_s": -1}),
        ("timer_delay", {}, {}),
        # Other block types keep their keys.
        ("api_client", {"duration_s": 8}, {"duration_s": 8}),
    ],
)
def test_timer_nodes_adopt_a_duration_saved_under_the_sibling_key(node_type, data, expected):
    flow = FlowData.model_validate({"nodes": [{"id": "node", "type": node_type, "position": {"x": 0, "y": 0}, "data": data}]})

    assert flow.nodes[0].data == expected
