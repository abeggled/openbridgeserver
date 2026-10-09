"""Definition and execution tests for the ``binary_stats`` function block."""

from __future__ import annotations

import pytest

from obs.logic.nodes.logic.binary_stats import NODE_TYPE
from tests.unit.conftest import edge, make_executor, node

OUTPUT_IDS = ["count_true", "count_false", "majority_true", "total", "percent_true", "tie", "threshold_reached"]


def test_definition_mirrors_the_gate_inputs_and_declares_all_outputs():
    assert NODE_TYPE.type == "binary_stats"
    assert NODE_TYPE.category == "logic"
    assert [p.id for p in NODE_TYPE.inputs] == ["in1", "in2"]
    assert [p.id for p in NODE_TYPE.outputs] == OUTPUT_IDS
    assert NODE_TYPE.config_schema["input_count"] == {
        "type": "integer",
        "default": 2,
        "min": 2,
        "max": 30,
        "label": "Anzahl Eingänge",
    }
    assert NODE_TYPE.config_schema["unwired_inputs"]["enum"] == ["ignore", "count_false"]
    assert NODE_TYPE.config_schema["unwired_inputs"]["default"] == "ignore"
    assert NODE_TYPE.config_schema["threshold_count"]["default"] == 0


def run(values: dict[int, object], data: dict | None = None, *, dangling: tuple[int, ...] = ()) -> dict:
    """Wire ``values`` (port index → constant) to a binary_stats node and execute it.

    ``dangling`` ports are wired to a handle their source never emits.
    """
    nodes = [node("stats", "binary_stats", data or {})]
    edges = []
    for index, value in values.items():
        nodes.append(node(f"c{index}", "const_value", {"value": value, "data_type": "bool"}))
        edges.append(edge(f"c{index}", "stats", "value", f"in{index}"))
    for index in dangling:
        nodes.append(node(f"d{index}", "const_value", {"value": 1, "data_type": "bool"}))
        edges.append(edge(f"d{index}", "stats", "missing", f"in{index}"))
    return make_executor(nodes, edges).execute()["stats"]


def test_counts_true_and_false_with_derived_values():
    out = run({1: True, 2: True, 3: False}, {"input_count": 3})

    assert out == {
        "count_true": 2,
        "count_false": 1,
        "majority_true": True,
        "total": 3,
        "percent_true": 66.7,
        "tie": False,
        "threshold_reached": False,
    }


def test_negation_is_applied_before_counting():
    out = run({1: True, 2: True}, {"negate_in1": True})

    assert (out["count_true"], out["count_false"]) == (1, 1)


@pytest.mark.parametrize(
    ("values", "majority", "tie"),
    [
        ({1: True, 2: True, 3: False}, True, False),
        ({1: True, 2: False, 3: False}, False, False),
        ({1: True, 2: False}, False, True),
        ({1: True, 2: True, 3: False, 4: False}, False, True),
        ({1: True, 2: True, 3: True, 4: False}, True, False),
    ],
)
def test_majority_is_strict_and_tie_is_separate(values, majority, tie):
    out = run(values, {"input_count": 4})

    assert out["majority_true"] is majority
    assert out["tie"] is tie


def test_unwired_inputs_are_ignored_by_default():
    out = run({1: True, 2: False}, {"input_count": 10})

    assert (out["total"], out["count_true"], out["count_false"]) == (2, 1, 1)


def test_unwired_inputs_count_false_reproduces_gate_behaviour():
    out = run({1: True, 2: False}, {"input_count": 10, "unwired_inputs": "count_false"})

    assert (out["total"], out["count_true"], out["count_false"]) == (10, 1, 9)
    assert out["percent_true"] == 10.0


@pytest.mark.parametrize("mode", [" COUNT_FALSE ", "Count_False"])
def test_unwired_mode_is_normalised_before_comparison(mode):
    out = run({1: True}, {"input_count": 3, "unwired_inputs": mode})

    assert (out["total"], out["count_false"]) == (3, 2)


def test_wired_input_without_value_counts_as_false():
    out = run({1: True}, {"input_count": 3}, dangling=(2,))

    assert (out["total"], out["count_true"], out["count_false"]) == (2, 1, 1)


def test_manual_input_override_counts_as_supplied():
    executor = make_executor([node("stats", "binary_stats", {"input_count": 3})])

    out = executor.execute({"stats": {"in1": True}})["stats"]

    assert (out["total"], out["count_true"]) == (1, 1)


def test_nothing_wired_yields_defined_zero_values():
    out = run({}, {"input_count": 5})

    assert out == {
        "count_true": 0,
        "count_false": 0,
        "majority_true": False,
        "total": 0,
        "percent_true": 0.0,
        "tie": False,
        "threshold_reached": False,
    }


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [(0, False), (2, True), (3, False), ("2", True), ("x", False), (None, False), (1.5, False), (2.9, False), (2.0, True)],
)
def test_threshold_follows_threshold_count(threshold, expected):
    out = run({1: True, 2: True, 3: False}, {"input_count": 3, "threshold_count": threshold})

    assert out["threshold_reached"] is expected


def test_percent_true_rounds_half_up_to_one_decimal():
    out = run({i: i <= 1 for i in range(1, 9)}, {"input_count": 8})

    assert out["percent_true"] == 12.5
    out = run({i: i <= 1 for i in range(1, 17)}, {"input_count": 16})
    assert out["percent_true"] == 6.3


def test_negated_wired_input_without_value_still_counts_as_false():
    out = run({1: True}, {"input_count": 2, "negate_in2": True}, dangling=(2,))

    assert (out["total"], out["count_true"], out["count_false"]) == (2, 1, 1)


def test_count_false_mode_with_nothing_wired_yields_zero_values():
    out = run({}, {"input_count": 5, "unwired_inputs": "count_false"})

    assert (out["total"], out["count_true"], out["count_false"], out["percent_true"]) == (0, 0, 0, 0.0)
    assert out["tie"] is False
    assert out["majority_true"] is False


def test_count_false_mode_negates_unwired_inputs_like_and_or():
    out = run({1: True}, {"input_count": 3, "unwired_inputs": "count_false", "negate_in3": True})

    assert (out["total"], out["count_true"], out["count_false"]) == (3, 2, 1)


@pytest.mark.parametrize("input_count", ["", None, "abc"])
def test_invalid_input_count_falls_back_to_the_default_of_two(input_count):
    out = run({1: True, 2: False, 3: True}, {"input_count": input_count})

    assert (out["total"], out["count_true"], out["count_false"]) == (2, 1, 1)
