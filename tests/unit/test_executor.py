"""
Unit tests for obs/logic/executor.py

Covers:
  - _safe_eval: mathematical rounding, all math functions, sandboxing
  - _round_half_up: correct behaviour vs Python built-in round()
  - _to_num / _to_bool: type coercion rules
  - Node types: const_value, and/or/not/xor, compare, hysteresis,
                math_formula (incl. output_formula), math_map, clamp,
                statistics, datapoint_read/write, python_script
  - Full graph execution via execute()
  - Topological sort (multi-node graphs)
"""
from __future__ import annotations

import pytest

from obs.logic.executor import ExecutionError, GraphExecutor
from tests.unit.conftest import edge, make_executor, node


# ===========================================================================
# _round_half_up
# ===========================================================================

class TestRoundHalfUp:
    """
    Python's built-in round() uses banker's rounding (round-half-to-even) AND
    is affected by IEEE 754 representation: round(21.16, 1) → 21.1 (not 21.2).
    _round_half_up must always round 0.5 up and use Decimal to avoid float errors.
    """

    def test_half_rounds_up(self):
        assert GraphExecutor._round_half_up(0.5) == 1

    def test_half_negative_rounds_away_from_zero(self):
        # ROUND_HALF_UP rounds away from zero: -0.5 → -1
        assert GraphExecutor._round_half_up(-0.5) == -1

    def test_21_16_one_decimal(self):
        # The canonical regression: Python round(21.16, 1) == 21.1 (wrong)
        assert GraphExecutor._round_half_up(21.16, 1) == pytest.approx(21.2)

    def test_21_15_one_decimal(self):
        assert GraphExecutor._round_half_up(21.15, 1) == pytest.approx(21.2)

    def test_zero_decimals(self):
        assert GraphExecutor._round_half_up(2.5) == 3
        assert GraphExecutor._round_half_up(3.5) == 4   # not 4 via banker's

    def test_two_decimals(self):
        assert GraphExecutor._round_half_up(1.005, 2) == pytest.approx(1.01)

    def test_negative_integer(self):
        assert GraphExecutor._round_half_up(-3.4) == -3

    def test_exact_integer_unchanged(self):
        assert GraphExecutor._round_half_up(5.0) == 5


# ===========================================================================
# _safe_eval
# ===========================================================================

class TestSafeEval:
    def test_simple_arithmetic(self):
        assert GraphExecutor._safe_eval("a + b", {"a": 3, "b": 4}) == 7

    def test_multiplication(self):
        assert GraphExecutor._safe_eval("a * 2", {"a": 5}) == 10

    def test_division(self):
        assert GraphExecutor._safe_eval("x / 10", {"x": 100}) == 10.0

    def test_round_uses_mathematical_rounding(self):
        # round() in _safe_eval is _round_half_up, NOT Python's built-in
        result = GraphExecutor._safe_eval("round(x, 1)", {"x": 21.15})
        assert result == pytest.approx(21.2)

    def test_min_max(self):
        assert GraphExecutor._safe_eval("min(a, b)", {"a": 3, "b": 7}) == 3
        assert GraphExecutor._safe_eval("max(a, b)", {"a": 3, "b": 7}) == 7

    def test_abs(self):
        assert GraphExecutor._safe_eval("abs(x)", {"x": -5}) == 5

    def test_math_sqrt(self):
        assert GraphExecutor._safe_eval("sqrt(x)", {"x": 9}) == pytest.approx(3.0)

    def test_math_pi(self):
        result = GraphExecutor._safe_eval("x * pi / 180", {"x": 180})
        assert result == pytest.approx(3.14159, abs=1e-4)

    def test_clamp_formula(self):
        result = GraphExecutor._safe_eval("max(0, min(100, x))", {"x": 150})
        assert result == 100

    def test_fahrenheit_to_celsius(self):
        result = GraphExecutor._safe_eval("(x - 32) * 5 / 9", {"x": 212})
        assert result == pytest.approx(100.0)

    def test_invalid_expression_raises_execution_error(self):
        with pytest.raises(ExecutionError):
            GraphExecutor._safe_eval("1 / 0", {})

    def test_undefined_variable_raises(self):
        with pytest.raises(ExecutionError):
            GraphExecutor._safe_eval("undefined_var + 1", {})

    def test_import_blocked(self):
        with pytest.raises(ExecutionError):
            GraphExecutor._safe_eval("__import__('os')", {})

    def test_builtins_blocked(self):
        with pytest.raises(ExecutionError):
            GraphExecutor._safe_eval("open('secret')", {})

    def test_attribute_access_allowed(self):
        # NOTE: current sandbox does not block __class__ access
        # This documents the actual behaviour (not a security guarantee)
        result = GraphExecutor._safe_eval("().__class__.__bases__", {})
        assert result is not None  # returns (<class 'object'>,)


# ===========================================================================
# Single-node execution helpers
# ===========================================================================

def run_single(node_type: str, data: dict, inputs: dict | None = None) -> dict:
    """Execute a single-node graph and return its outputs."""
    n = node("n1", node_type, data)
    exc = make_executor([n])
    overrides = {"n1": inputs} if inputs else {}
    return exc.execute(overrides).get("n1", {})


# ===========================================================================
# const_value node
# ===========================================================================

class TestConstValue:
    def test_number(self):
        out = run_single("const_value", {"value": "42", "data_type": "number"})
        assert out["value"] == 42.0

    def test_boolean_true(self):
        out = run_single("const_value", {"value": "true", "data_type": "bool"})
        assert out["value"] is True

    def test_boolean_false(self):
        out = run_single("const_value", {"value": "false", "data_type": "bool"})
        assert out["value"] is False

    def test_string(self):
        out = run_single("const_value", {"value": "hello", "data_type": "string"})
        assert out["value"] == "hello"


# ===========================================================================
# Logic nodes: and, or, not, xor
# ===========================================================================

class TestLogicNodes:
    @pytest.mark.parametrize("a, b, expected", [
        (True,  True,  True),
        (True,  False, False),
        (False, True,  False),
        (False, False, False),
    ])
    def test_and(self, a, b, expected):
        out = run_single("and", {}, {"a": a, "b": b})
        assert out["out"] is expected

    @pytest.mark.parametrize("a, b, expected", [
        (True,  True,  True),
        (True,  False, True),
        (False, True,  True),
        (False, False, False),
    ])
    def test_or(self, a, b, expected):
        out = run_single("or", {}, {"a": a, "b": b})
        assert out["out"] is expected

    @pytest.mark.parametrize("inp, expected", [
        (True, False), (False, True), (1, False), (0, True),
    ])
    def test_not(self, inp, expected):
        out = run_single("not", {}, {"in": inp})
        assert out["out"] is expected

    @pytest.mark.parametrize("a, b, expected", [
        (True,  True,  False),
        (True,  False, True),
        (False, True,  True),
        (False, False, False),
    ])
    def test_xor(self, a, b, expected):
        out = run_single("xor", {}, {"a": a, "b": b})
        assert out["out"] is expected

    def test_and_with_none_input_is_false(self):
        out = run_single("and", {}, {"a": True, "b": None})
        assert out["out"] is False


# ===========================================================================
# compare node
# ===========================================================================

class TestCompareNode:
    @pytest.mark.parametrize("op, a, b, expected", [
        (">",  5, 3, True),
        (">",  3, 5, False),
        ("<",  3, 5, True),
        ("=",  5, 5, True),
        ("=",  5, 6, False),
        (">=", 5, 5, True),
        ("<=", 4, 5, True),
        ("!=", 4, 5, True),
        ("!=", 5, 5, False),
    ])
    def test_numeric_operators(self, op, a, b, expected):
        out = run_single("compare", {"operator": op}, {"a": a, "b": b})
        assert out["out"] is expected

    def test_none_input_returns_false(self):
        out = run_single("compare", {"operator": ">"}, {"a": None, "b": 5})
        assert out["out"] is False

    def test_default_operator_is_greater_than(self):
        out = run_single("compare", {}, {"a": 10, "b": 5})
        assert out["out"] is True


# ===========================================================================
# hysteresis node
# ===========================================================================

class TestHysteresisNode:
    def test_turns_on_above_threshold(self):
        state = {}
        n1 = node("h", "hysteresis", {"threshold_on": 25.0, "threshold_off": 20.0})
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"h": {"value": 26.0}})
        assert out["h"]["out"] is True

    def test_stays_on_between_thresholds(self):
        state = {"h": True}
        n1 = node("h", "hysteresis", {"threshold_on": 25.0, "threshold_off": 20.0})
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"h": {"value": 22.0}})  # between thresholds
        assert out["h"]["out"] is True

    def test_turns_off_below_lower_threshold(self):
        state = {"h": True}
        n1 = node("h", "hysteresis", {"threshold_on": 25.0, "threshold_off": 20.0})
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"h": {"value": 19.0}})
        assert out["h"]["out"] is False

    def test_does_not_turn_on_below_upper_threshold(self):
        state = {"h": False}
        n1 = node("h", "hysteresis", {"threshold_on": 25.0, "threshold_off": 20.0})
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"h": {"value": 22.0}})  # between thresholds, was off
        assert out["h"]["out"] is False

    def test_state_persists_between_executions(self):
        state = {}
        n1 = node("h", "hysteresis", {"threshold_on": 25.0, "threshold_off": 20.0})

        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"h": {"value": 26.0}})   # turns on
        assert state["h"] is True

        exc2 = make_executor([n1], hysteresis_state=state)
        out = exc2.execute({"h": {"value": 22.0}})  # in hysteresis zone
        assert out["h"]["out"] is True   # still on

    def test_empty_state_dict_is_not_replaced(self):
        """Regression: hysteresis_state={} must not be treated as None."""
        state = {}
        n1 = node("h", "hysteresis", {"threshold_on": 25.0, "threshold_off": 20.0})
        exc = GraphExecutor(
            flow=__import__("obs.logic.models", fromlist=["FlowData"]).FlowData.model_validate(
                {"nodes": [n1], "edges": []}
            ),
            hysteresis_state=state,
        )
        exc.execute({"h": {"value": 26.0}})
        # State must have been written to the SAME dict object
        assert "h" in state


# ===========================================================================
# math_formula node
# ===========================================================================

class TestMathFormulaNode:
    def test_simple_addition(self):
        out = run_single("math_formula", {"formula": "a + b"}, {"a": 3, "b": 4})
        assert out["result"] == 7

    def test_multiplication(self):
        out = run_single("math_formula", {"formula": "a * b"}, {"a": 6, "b": 7})
        assert out["result"] == 42

    def test_none_inputs_default_to_zero(self):
        out = run_single("math_formula", {"formula": "a + b"}, {})
        assert out["result"] == 0

    def test_output_formula_transforms_result(self):
        out = run_single("math_formula",
                         {"formula": "a + b", "output_formula": "x * 2"},
                         {"a": 5, "b": 5})
        assert out["result"] == 20   # (5+5)*2

    def test_output_formula_round(self):
        out = run_single("math_formula",
                         {"formula": "a / b", "output_formula": "round(x, 1)"},
                         {"a": 10, "b": 3})
        assert out["result"] == pytest.approx(3.3)

    def test_output_formula_empty_string_ignored(self):
        out = run_single("math_formula",
                         {"formula": "a + b", "output_formula": ""},
                         {"a": 2, "b": 3})
        assert out["result"] == 5

    def test_formula_uses_mathematical_rounding(self):
        out = run_single("math_formula",
                         {"formula": "a", "output_formula": "round(x, 1)"},
                         {"a": 21.15})
        assert out["result"] == pytest.approx(21.2)


# ===========================================================================
# math_map node
# ===========================================================================

class TestMathMapNode:
    def test_linear_scale(self):
        # 0–255 → 0–100
        out = run_single("math_map",
                         {"in_min": 0, "in_max": 255, "out_min": 0, "out_max": 100},
                         {"value": 127.5})
        assert out["result"] == pytest.approx(50.0, abs=0.5)

    def test_min_boundary(self):
        out = run_single("math_map",
                         {"in_min": 0, "in_max": 100, "out_min": 0, "out_max": 1},
                         {"value": 0})
        assert out["result"] == pytest.approx(0.0)

    def test_max_boundary(self):
        out = run_single("math_map",
                         {"in_min": 0, "in_max": 100, "out_min": 0, "out_max": 1},
                         {"value": 100})
        assert out["result"] == pytest.approx(1.0)

    def test_divide_by_zero_returns_out_min(self):
        # in_min == in_max → return out_min
        out = run_single("math_map",
                         {"in_min": 50, "in_max": 50, "out_min": 7, "out_max": 42},
                         {"value": 50})
        assert out["result"] == 7


# ===========================================================================
# clamp node
# ===========================================================================

class TestClampNode:
    def test_value_within_range_unchanged(self):
        out = run_single("clamp", {"min": 0, "max": 100}, {"value": 50})
        assert out["result"] == 50

    def test_value_above_max_clamped(self):
        out = run_single("clamp", {"min": 0, "max": 100}, {"value": 150})
        assert out["result"] == 100

    def test_value_below_min_clamped(self):
        out = run_single("clamp", {"min": 0, "max": 100}, {"value": -10})
        assert out["result"] == 0

    def test_at_exact_boundaries(self):
        out = run_single("clamp", {"min": 0, "max": 100}, {"value": 0})
        assert out["result"] == 0
        out = run_single("clamp", {"min": 0, "max": 100}, {"value": 100})
        assert out["result"] == 100

    def test_negative_range(self):
        out = run_single("clamp", {"min": -50, "max": -10}, {"value": 0})
        assert out["result"] == -10


# ===========================================================================
# statistics node
# ===========================================================================

class TestStatisticsNode:
    def test_single_value(self):
        state = {}
        n1 = node("s", "statistics", {})
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"s": {"value": 10.0}})
        assert out["s"]["min"] == 10.0
        assert out["s"]["max"] == 10.0
        assert out["s"]["avg"] == pytest.approx(10.0)
        assert out["s"]["count"] == 1

    def test_accumulates_over_runs(self):
        state = {}
        n1 = node("s", "statistics", {})

        for v in [10.0, 20.0, 30.0]:
            exc = make_executor([n1], hysteresis_state=state)
            exc.execute({"s": {"value": v}})

        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"s": {}})  # no new value — just read state
        assert out["s"]["min"] == 10.0
        assert out["s"]["max"] == 30.0
        assert out["s"]["count"] == 3
        assert out["s"]["avg"] == pytest.approx(20.0)

    def test_reset_clears_state(self):
        state = {}
        n1 = node("s", "statistics", {})

        # Add some values
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"s": {"value": 99.0}})

        # Reset
        exc2 = make_executor([n1], hysteresis_state=state)
        out = exc2.execute({"s": {"reset": True}})
        assert out["s"]["count"] == 0
        assert out["s"]["min"] is None

    def test_state_survives_empty_dict(self):
        """Regression: `state or {}` bug — empty dict must not be discarded."""
        state = {}
        n1 = node("s", "statistics", {})

        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"s": {"value": 42.0}})

        # state must have been mutated — not lost
        assert "s" in state
        assert state["s"]["s_count"] == 1


# ===========================================================================
# datapoint_read / datapoint_write nodes
# ===========================================================================

class TestDatapointNodes:
    def test_read_passes_value_through(self):
        out = run_single("datapoint_read", {}, {"value": 21.4})
        assert out["value"] == pytest.approx(21.4)

    def test_read_applies_formula(self):
        out = run_single("datapoint_read", {"value_formula": "x / 10"}, {"value": 214})
        assert out["value"] == pytest.approx(21.4)

    def test_read_formula_error_returns_original(self):
        # Formula error must not propagate — original value preserved
        out = run_single("datapoint_read", {"value_formula": "1 / 0"}, {"value": 5.0})
        # On error, raw stays unchanged (per executor's try/except)
        assert out["value"] == 5.0

    def test_read_none_formula_skipped(self):
        out = run_single("datapoint_read", {"value_formula": None}, {"value": 7.0})
        assert out["value"] == 7.0

    def test_write_passes_value_through(self):
        out = run_single("datapoint_write", {}, {"value": 42.0})
        assert out["_write_value"] == pytest.approx(42.0)

    def test_write_applies_formula(self):
        out = run_single("datapoint_write", {"value_formula": "x * 3600"}, {"value": 1.0})
        assert out["_write_value"] == pytest.approx(3600.0)

    def test_write_trigger_passed_through(self):
        out = run_single("datapoint_write", {}, {"value": 1.0, "trigger": True})
        assert out["_triggered"] is True

    def test_write_none_value_skips_formula(self):
        out = run_single("datapoint_write", {"value_formula": "x * 2"}, {})
        assert out["_write_value"] is None


# ===========================================================================
# python_script node
# ===========================================================================

class TestPythonScriptNode:
    def test_simple_result(self):
        out = run_single("python_script", {"script": "result = inputs['a'] * 2"},
                         {"a": 5})
        assert out["result"] == 10

    def test_math_available(self):
        out = run_single("python_script", {"script": "result = math.sqrt(inputs['a'])"},
                         {"a": 9})
        assert out["result"] == pytest.approx(3.0)

    def test_script_error_returns_empty_output(self):
        # execute() catches all errors internally and logs them — never raises to caller
        n1 = node("p", "python_script", {"script": "result = 1 / 0"})
        exc = make_executor([n1])
        out = exc.execute({"p": {}})
        assert out.get("p") == {}   # node output is empty on error

    def test_os_import_blocked_returns_empty_output(self):
        # __import__ is not in builtins → ExecutionError caught internally → empty output
        n1 = node("p", "python_script", {"script": "import os; result = os.getcwd()"})
        exc = make_executor([n1])
        out = exc.execute({"p": {}})
        assert out.get("p") == {}

    def test_round_uses_mathematical_rounding(self):
        out = run_single("python_script",
                         {"script": "result = round(inputs['a'], 1)"},
                         {"a": 21.15})
        assert out["result"] == pytest.approx(21.2)


# ===========================================================================
# Multi-node graph execution (topological order)
# ===========================================================================

class TestMultiNodeGraph:
    def test_two_node_pipeline(self):
        """const_value → math_formula: value should flow correctly."""
        nodes = [
            node("c", "const_value", {"value": "10", "data_type": "number"}),
            node("f", "math_formula", {"formula": "a + b"}),
        ]
        edges = [edge("c", "f", source_handle="value", target_handle="a")]
        exc = make_executor(nodes, edges)
        out = exc.execute()
        assert out["f"]["result"] == pytest.approx(10.0)  # b defaults to 0

    def test_three_node_pipeline(self):
        """const → formula → clamp"""
        nodes = [
            node("c", "const_value", {"value": "150", "data_type": "number"}),
            node("f", "math_formula", {"formula": "a"}),
            node("cl", "clamp", {"min": 0, "max": 100}),
        ]
        edges = [
            edge("c", "f",  source_handle="value", target_handle="a"),
            edge("f", "cl", source_handle="result", target_handle="value"),
        ]
        exc = make_executor(nodes, edges)
        out = exc.execute()
        assert out["cl"]["result"] == 100

    def test_logic_pipeline(self):
        """Two const_value booleans → AND → NOT"""
        nodes = [
            node("t", "const_value", {"value": "true",  "data_type": "bool"}),
            node("f", "const_value", {"value": "false", "data_type": "bool"}),
            node("a", "and", {}),
            node("n", "not", {}),
        ]
        edges = [
            edge("t", "a", source_handle="value", target_handle="a"),
            edge("f", "a", source_handle="value", target_handle="b"),
            edge("a", "n", source_handle="out",   target_handle="in"),
        ]
        exc = make_executor(nodes, edges)
        out = exc.execute()
        assert out["a"]["out"] is False   # True AND False
        assert out["n"]["out"] is True    # NOT False

    def test_input_override_wins_over_const(self):
        """input_override for a node replaces whatever the graph computes."""
        nodes = [
            node("c", "const_value", {"value": "5", "data_type": "number"}),
            node("f", "math_formula", {"formula": "a + b"}),
        ]
        edges = [edge("c", "f", source_handle="value", target_handle="a")]
        exc = make_executor(nodes, edges)
        # Override 'a' input of formula node to 100
        out = exc.execute({"f": {"a": 100, "b": 0}})
        assert out["f"]["result"] == pytest.approx(100.0)
