"""Unit tests for obs/logic/executor.py

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
    """Python's built-in round() uses banker's rounding (round-half-to-even) AND
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
        assert GraphExecutor._round_half_up(3.5) == 4  # not 4 via banker's

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
    @pytest.mark.parametrize(
        "a, b, expected",
        [
            (True, True, True),
            (True, False, False),
            (False, True, False),
            (False, False, False),
        ],
    )
    def test_and(self, a, b, expected):
        out = run_single("and", {}, {"in1": a, "in2": b})
        assert out["out"] is expected

    @pytest.mark.parametrize(
        "a, b, expected",
        [
            (True, True, True),
            (True, False, True),
            (False, True, True),
            (False, False, False),
        ],
    )
    def test_or(self, a, b, expected):
        out = run_single("or", {}, {"in1": a, "in2": b})
        assert out["out"] is expected

    @pytest.mark.parametrize(
        "inp, expected",
        [
            (True, False),
            (False, True),
            (1, False),
            (0, True),
        ],
    )
    def test_not(self, inp, expected):
        out = run_single("not", {}, {"in1": inp})
        assert out["out"] is expected

    @pytest.mark.parametrize(
        "a, b, expected",
        [
            (True, True, False),
            (True, False, True),
            (False, True, True),
            (False, False, False),
        ],
    )
    def test_xor(self, a, b, expected):
        out = run_single("xor", {}, {"in1": a, "in2": b})
        assert out["out"] is expected

    def test_and_with_none_input_is_false(self):
        out = run_single("and", {}, {"in1": True, "in2": None})
        assert out["out"] is False


# ===========================================================================
# compare node
# ===========================================================================


class TestCompareNode:
    @pytest.mark.parametrize(
        "op, a, b, expected",
        [
            (">", 5, 3, True),
            (">", 3, 5, False),
            ("<", 3, 5, True),
            ("=", 5, 5, True),
            ("=", 5, 6, False),
            (">=", 5, 5, True),
            ("<=", 4, 5, True),
            ("!=", 4, 5, True),
            ("!=", 5, 5, False),
        ],
    )
    def test_numeric_operators(self, op, a, b, expected):
        out = run_single("compare", {"operator": op}, {"in1": a, "in2": b})
        assert out["out"] is expected

    def test_none_input_returns_false(self):
        out = run_single("compare", {"operator": ">"}, {"in1": None, "in2": 5})
        assert out["out"] is False

    def test_default_operator_is_greater_than(self):
        out = run_single("compare", {}, {"in1": 10, "in2": 5})
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
        exc.execute({"h": {"value": 26.0}})  # turns on
        assert state["h"] is True

        exc2 = make_executor([n1], hysteresis_state=state)
        out = exc2.execute({"h": {"value": 22.0}})  # in hysteresis zone
        assert out["h"]["out"] is True  # still on

    def test_empty_state_dict_is_not_replaced(self):
        """Regression: hysteresis_state={} must not be treated as None."""
        state = {}
        n1 = node("h", "hysteresis", {"threshold_on": 25.0, "threshold_off": 20.0})
        exc = GraphExecutor(
            flow=__import__("obs.logic.models", fromlist=["FlowData"]).FlowData.model_validate({"nodes": [n1], "edges": []}),
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
        out = run_single("math_formula", {"formula": "a + b"}, {"in1": 3, "in2": 4})
        assert out["result"] == 7

    def test_multiplication(self):
        out = run_single("math_formula", {"formula": "a * b"}, {"in1": 6, "in2": 7})
        assert out["result"] == 42

    def test_none_inputs_default_to_zero(self):
        out = run_single("math_formula", {"formula": "a + b"}, {})
        assert out["result"] == 0

    def test_output_formula_transforms_result(self):
        out = run_single(
            "math_formula",
            {"formula": "a + b", "output_formula": "x * 2"},
            {"in1": 5, "in2": 5},
        )
        assert out["result"] == 20  # (5+5)*2

    def test_output_formula_round(self):
        out = run_single(
            "math_formula",
            {"formula": "a / b", "output_formula": "round(x, 1)"},
            {"in1": 10, "in2": 3},
        )
        assert out["result"] == pytest.approx(3.3)

    def test_output_formula_empty_string_ignored(self):
        out = run_single(
            "math_formula",
            {"formula": "a + b", "output_formula": ""},
            {"in1": 2, "in2": 3},
        )
        assert out["result"] == 5

    def test_formula_uses_mathematical_rounding(self):
        out = run_single(
            "math_formula",
            {"formula": "a", "output_formula": "round(x, 1)"},
            {"in1": 21.15},
        )
        assert out["result"] == pytest.approx(21.2)


# ===========================================================================
# math_map node
# ===========================================================================


class TestMathMapNode:
    def test_linear_scale(self):
        # 0–255 → 0–100
        out = run_single(
            "math_map",
            {"in_min": 0, "in_max": 255, "out_min": 0, "out_max": 100},
            {"value": 127.5},
        )
        assert out["result"] == pytest.approx(50.0, abs=0.5)

    def test_min_boundary(self):
        out = run_single(
            "math_map",
            {"in_min": 0, "in_max": 100, "out_min": 0, "out_max": 1},
            {"value": 0},
        )
        assert out["result"] == pytest.approx(0.0)

    def test_max_boundary(self):
        out = run_single(
            "math_map",
            {"in_min": 0, "in_max": 100, "out_min": 0, "out_max": 1},
            {"value": 100},
        )
        assert out["result"] == pytest.approx(1.0)

    def test_divide_by_zero_returns_out_min(self):
        # in_min == in_max → return out_min
        out = run_single(
            "math_map",
            {"in_min": 50, "in_max": 50, "out_min": 7, "out_max": 42},
            {"value": 50},
        )
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
# random_value node
# ===========================================================================


class TestRandomValueNode:
    def test_no_output_without_trigger(self):
        out = run_single("random_value", {"data_type": "int", "min": 0, "max": 100}, {"trigger": False})
        assert out["value"] is None

    def test_no_output_when_trigger_absent(self):
        out = run_single("random_value", {"data_type": "int", "min": 0, "max": 100})
        assert out["value"] is None

    def test_integer_in_range(self):
        for _ in range(50):
            out = run_single("random_value", {"data_type": "int", "min": 10, "max": 20}, {"trigger": True})
            assert isinstance(out["value"], int)
            assert 10 <= out["value"] <= 20

    def test_float_in_range(self):
        for _ in range(50):
            out = run_single("random_value", {"data_type": "float", "min": 1.5, "max": 3.5, "decimal_places": 2}, {"trigger": True})
            assert isinstance(out["value"], float)
            assert 1.5 <= out["value"] <= 3.5

    def test_float_decimal_places(self):
        for _ in range(20):
            out = run_single("random_value", {"data_type": "float", "min": 0, "max": 1, "decimal_places": 1}, {"trigger": True})
            val = out["value"]
            assert round(val, 1) == val

    def test_swapped_min_max_corrected(self):
        for _ in range(20):
            out = run_single("random_value", {"data_type": "int", "min": 100, "max": 10}, {"trigger": True})
            assert 10 <= out["value"] <= 100

    def test_integer_zero_decimal_places_returns_int(self):
        out = run_single("random_value", {"data_type": "int", "min": 5, "max": 5}, {"trigger": True})
        assert out["value"] == 5
        assert isinstance(out["value"], int)

    def test_float_zero_decimal_places_is_whole_number(self):
        for _ in range(10):
            out = run_single("random_value", {"data_type": "float", "min": 0, "max": 100, "decimal_places": 0}, {"trigger": True})
            assert out["value"] == int(out["value"])


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

    # ── value_map on datapoint_read ─────────────────────────────────────────

    def test_read_applies_value_map(self):
        m = {"0": "Aus", "1": "An"}
        out = run_single("datapoint_read", {"value_map": m}, {"value": 1})
        assert out["value"] == "An"

    def test_read_value_map_n_values(self):
        m = {"0": "Aus", "1": "Init", "2": "Aktiv", "3": "Fehler", "10": "Standby"}
        out = run_single("datapoint_read", {"value_map": m}, {"value": 10})
        assert out["value"] == "Standby"

    def test_read_value_map_float_key(self):
        # Modbus delivers integer-valued floats — must still match
        m = {"5": "Betrieb"}
        out = run_single("datapoint_read", {"value_map": m}, {"value": 5.0})
        assert out["value"] == "Betrieb"

    def test_read_value_map_no_match_returns_original(self):
        m = {"0": "Aus", "1": "An"}
        out = run_single("datapoint_read", {"value_map": m}, {"value": 99})
        assert out["value"] == 99

    def test_read_formula_then_value_map(self):
        # Formula runs first: x*2 → 2; then map: "2" → "Zwei"
        m = {"2": "Zwei"}
        out = run_single("datapoint_read", {"value_formula": "x * 2", "value_map": m}, {"value": 1})
        assert out["value"] == "Zwei"

    # ── value_map on datapoint_write ────────────────────────────────────────

    def test_write_applies_value_map(self):
        m = {"true": "on", "false": "off"}
        out = run_single("datapoint_write", {"value_map": m}, {"value": True})
        assert out["_write_value"] == "on"

    def test_write_value_map_float_key(self):
        m = {"3": "Aktiv"}
        out = run_single("datapoint_write", {"value_map": m}, {"value": 3.0})
        assert out["_write_value"] == "Aktiv"


# ===========================================================================
# python_script node
# ===========================================================================


class TestPythonScriptNode:
    def test_simple_result(self):
        out = run_single("python_script", {"script": "result = inputs['a'] * 2"}, {"a": 5})
        assert out["result"] == 10

    def test_math_available(self):
        out = run_single("python_script", {"script": "result = math.sqrt(inputs['a'])"}, {"a": 9})
        assert out["result"] == pytest.approx(3.0)

    def test_script_error_returns_empty_output(self):
        # execute() catches all errors internally and logs them — never raises to caller
        n1 = node("p", "python_script", {"script": "result = 1 / 0"})
        exc = make_executor([n1])
        out = exc.execute({"p": {}})
        assert out.get("p") == {}  # node output is empty on error

    def test_os_import_blocked_returns_empty_output(self):
        # __import__ is not in builtins → ExecutionError caught internally → empty output
        n1 = node("p", "python_script", {"script": "import os; result = os.getcwd()"})
        exc = make_executor([n1])
        out = exc.execute({"p": {}})
        assert out.get("p") == {}

    def test_round_uses_mathematical_rounding(self):
        out = run_single("python_script", {"script": "result = round(inputs['a'], 1)"}, {"a": 21.15})
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
        edges = [edge("c", "f", source_handle="value", target_handle="in1")]
        exc = make_executor(nodes, edges)
        out = exc.execute()
        assert out["f"]["result"] == pytest.approx(10.0)  # in2/b defaults to 0

    def test_three_node_pipeline(self):
        """Const → formula → clamp"""
        nodes = [
            node("c", "const_value", {"value": "150", "data_type": "number"}),
            node("f", "math_formula", {"formula": "a"}),
            node("cl", "clamp", {"min": 0, "max": 100}),
        ]
        edges = [
            edge("c", "f", source_handle="value", target_handle="in1"),
            edge("f", "cl", source_handle="result", target_handle="value"),
        ]
        exc = make_executor(nodes, edges)
        out = exc.execute()
        assert out["cl"]["result"] == 100

    def test_logic_pipeline(self):
        """Two const_value booleans → AND → NOT"""
        nodes = [
            node("t", "const_value", {"value": "true", "data_type": "bool"}),
            node("f", "const_value", {"value": "false", "data_type": "bool"}),
            node("a", "and", {}),
            node("n", "not", {}),
        ]
        edges = [
            edge("t", "a", source_handle="value", target_handle="in1"),
            edge("f", "a", source_handle="value", target_handle="in2"),
            edge("a", "n", source_handle="out", target_handle="in1"),
        ]
        exc = make_executor(nodes, edges)
        out = exc.execute()
        assert out["a"]["out"] is False  # True AND False
        assert out["n"]["out"] is True  # NOT False

    def test_input_override_wins_over_const(self):
        """input_override for a node replaces whatever the graph computes."""
        nodes = [
            node("c", "const_value", {"value": "5", "data_type": "number"}),
            node("f", "math_formula", {"formula": "a + b"}),
        ]
        edges = [edge("c", "f", source_handle="value", target_handle="in1")]
        exc = make_executor(nodes, edges)
        # Override in1 input of formula node to 100
        out = exc.execute({"f": {"in1": 100, "in2": 0}})
        assert out["f"]["result"] == pytest.approx(100.0)


# ===========================================================================
# Enhanced AND / OR / XOR  (variable inputs 2–30, per-input/output negation)
# ===========================================================================


class TestEnhancedGateInputs:
    """Variable-input count and negation for AND, OR, XOR."""

    # ── AND multi-input ───────────────────────────────────────────────────

    def test_and_3_inputs_all_true(self):
        out = run_single("and", {"input_count": 3}, {"in1": True, "in2": True, "in3": True})
        assert out["out"] is True

    def test_and_3_inputs_one_false(self):
        out = run_single("and", {"input_count": 3}, {"in1": True, "in2": True, "in3": False})
        assert out["out"] is False

    def test_and_negate_single_input(self):
        # negate_in1: AND(¬False, True) = AND(True, True) = True
        out = run_single("and", {"negate_in1": True}, {"in1": False, "in2": True})
        assert out["out"] is True

    def test_and_negate_output(self):
        # AND(True, True) = True; negate_out → False
        out = run_single("and", {"negate_out": True}, {"in1": True, "in2": True})
        assert out["out"] is False

    def test_and_negate_input_and_output(self):
        # negate_in2: AND(True, ¬False) = True; negate_out → False
        out = run_single("and", {"negate_in2": True, "negate_out": True}, {"in1": True, "in2": False})
        assert out["out"] is False

    def test_and_5_inputs_all_true(self):
        inputs = {"in1": True, "in2": True, "in3": True, "in4": True, "in5": True}
        out = run_single("and", {"input_count": 5}, inputs)
        assert out["out"] is True

    def test_and_5_inputs_last_false(self):
        inputs = {"in1": True, "in2": True, "in3": True, "in4": True, "in5": False}
        out = run_single("and", {"input_count": 5}, inputs)
        assert out["out"] is False

    # ── OR multi-input ────────────────────────────────────────────────────

    def test_or_3_inputs_all_false(self):
        out = run_single("or", {"input_count": 3}, {"in1": False, "in2": False, "in3": False})
        assert out["out"] is False

    def test_or_3_inputs_one_true(self):
        out = run_single("or", {"input_count": 3}, {"in1": False, "in2": False, "in3": True})
        assert out["out"] is True

    def test_or_negate_input(self):
        # negate_in1: OR(¬False, False) = OR(True, False) = True
        out = run_single("or", {"negate_in1": True}, {"in1": False, "in2": False})
        assert out["out"] is True

    def test_or_negate_output(self):
        # OR(False, False) = False; negate_out → True
        out = run_single("or", {"negate_out": True}, {"in1": False, "in2": False})
        assert out["out"] is True

    # ── XOR multi-input ───────────────────────────────────────────────────

    def test_xor_3_inputs_exactly_one_true(self):
        out = run_single("xor", {"input_count": 3}, {"in1": True, "in2": False, "in3": False})
        assert out["out"] is True

    def test_xor_3_inputs_two_true(self):
        # Two inputs true → XOR false (not exactly one)
        out = run_single("xor", {"input_count": 3}, {"in1": True, "in2": True, "in3": False})
        assert out["out"] is False

    def test_xor_3_inputs_all_false(self):
        out = run_single("xor", {"input_count": 3}, {"in1": False, "in2": False, "in3": False})
        assert out["out"] is False

    def test_xor_negate_output(self):
        # XOR(True, False) = True; negate_out → False
        out = run_single("xor", {"negate_out": True}, {"in1": True, "in2": False})
        assert out["out"] is False

    # ── 2-input default behaviour ─────────────────────────────────────────

    def test_and_2_inputs_default(self):
        out = run_single("and", {}, {"in1": True, "in2": True})
        assert out["out"] is True

    def test_or_2_inputs_default(self):
        out = run_single("or", {}, {"in1": False, "in2": True})
        assert out["out"] is True

    def test_xor_2_inputs_default(self):
        out = run_single("xor", {}, {"in1": True, "in2": True})
        assert out["out"] is False

    def test_input_count_clamped_to_max_30(self):
        # Even with absurd value, must not raise
        inputs = {f"in{i}": True for i in range(1, 11)}
        out = run_single("and", {"input_count": 999}, inputs)
        assert isinstance(out["out"], bool)


# ===========================================================================
# gate (TOR) node
# ===========================================================================


class TestGateNode:
    """Tests for the 'gate' (TOR) function block.

    Behaviour:
      - enable=true  → output = input value; last value is stored in state
      - enable=false, closed_behavior='retain'        → output = last stored value
      - enable=false, closed_behavior='default_value' → output = configured default
      - negate_enable inverts the gate control signal
    """

    def test_gate_open_passes_value(self):
        out = run_single("gate", {}, {"in": 42, "enable": True})
        assert out["out"] == 42

    def test_gate_open_passes_false(self):
        out = run_single("gate", {}, {"in": False, "enable": True})
        assert out["out"] is False

    def test_gate_closed_retain_returns_last(self):
        state: dict = {}
        n = node("g", "gate", {"closed_behavior": "retain"})
        exc = make_executor([n], hysteresis_state=state)
        # First run: gate open → stores 99
        exc.execute({"g": {"in": 99, "enable": True}})
        # Second run: gate closed → must return 99
        out = exc.execute({"g": {"in": 0, "enable": False}})
        assert out["g"]["out"] == 99

    def test_gate_closed_retain_none_before_first_open(self):
        out = run_single("gate", {"closed_behavior": "retain"}, {"in": 5, "enable": False})
        assert out["out"] is None

    def test_gate_closed_default_value_numeric(self):
        out = run_single(
            "gate",
            {"closed_behavior": "default_value", "default_value": "7"},
            {"in": 99, "enable": False},
        )
        assert out["out"] == pytest.approx(7.0)

    def test_gate_closed_default_value_string(self):
        out = run_single(
            "gate",
            {"closed_behavior": "default_value", "default_value": "aus"},
            {"in": "ein", "enable": False},
        )
        assert out["out"] == "aus"

    def test_gate_negate_enable_closed_when_true(self):
        # negate_enable: enable=True → inverted → gate closed
        out = run_single(
            "gate",
            {
                "negate_enable": True,
                "closed_behavior": "default_value",
                "default_value": "0",
            },
            {"in": 55, "enable": True},
        )
        assert out["out"] == pytest.approx(0.0)

    def test_gate_negate_enable_open_when_false(self):
        # negate_enable: enable=False → inverted → gate open
        out = run_single("gate", {"negate_enable": True}, {"in": 77, "enable": False})
        assert out["out"] == 77

    def test_gate_enable_string_true(self):
        out = run_single("gate", {}, {"in": 10, "enable": "true"})
        assert out["out"] == 10

    def test_gate_enable_string_false(self):
        out = run_single(
            "gate",
            {"closed_behavior": "default_value", "default_value": "0"},
            {"in": 10, "enable": "false"},
        )
        assert out["out"] == pytest.approx(0.0)

    def test_gate_updates_stored_value_on_each_open(self):
        state: dict = {}
        n = node("g", "gate", {"closed_behavior": "retain"})
        exc = make_executor([n], hysteresis_state=state)
        exc.execute({"g": {"in": 10, "enable": True}})
        exc.execute({"g": {"in": 20, "enable": True}})
        exc.execute({"g": {"in": 99, "enable": False}})  # gate closed
        out = exc.execute({"g": {"in": 99, "enable": False}})
        # Last stored value was 20
        assert out["g"]["out"] == 20


# ===========================================================================
# heating_circuit  (Winter/Sommer-Umschaltung, DIN-Norm)
# ===========================================================================


class TestHeatingCircuit:
    """Tests for the redesigned heating_circuit node.

    New API: single 'value' input; time slot assigned via '_slot' override
    (t1 / t2 / t3).  All three slots in the same date must be received
    before a daily_avg is computed.
    Hysteresis: heating ON when ref_temp < temp_winter, OFF when > temp_summer,
    stays unchanged between thresholds.
    """

    # Default: heating ON below 15 °C, OFF above 20 °C
    _CFG = {"temp_winter": 15.0, "temp_summer": 20.0}

    def _run_slot(self, slot, value, config=None, state=None):
        """Feed a single temperature reading at the given time slot."""
        if state is None:
            state = {}
        if config is None:
            config = self._CFG
        n1 = node("h", "heating_circuit", config)
        exc = make_executor([n1], hysteresis_state=state)
        return exc.execute({"h": {"value": value, "_slot": slot}})["h"], state

    def _run_full_day(self, t1, t2, t3, config=None, state=None):
        """Simulate a full day (all three slots) and return the final output."""
        if state is None:
            state = {}
        if config is None:
            config = self._CFG
        n1 = node("h", "heating_circuit", config)
        out = None
        for slot, val in [("t1", t1), ("t2", t2), ("t3", t3)]:
            exc = make_executor([n1], hysteresis_state=state)
            out = exc.execute({"h": {"value": val, "_slot": slot}})["h"]
        return out, state

    def test_din_formula_daily_avg(self):
        # T_avg = (10 + 12 + 2*8) / 4 = 38/4 = 9.5
        out, _ = self._run_full_day(10, 12, 8)
        assert out["daily_avg"] == pytest.approx(9.5)

    def test_heating_on_below_temp_winter(self):
        # daily_avg = (5+6+2*4)/4 = 4.75 < temp_winter=15 → ON
        out, _ = self._run_full_day(5, 6, 4)
        assert out["heating_mode"] == 1

    def test_heating_off_above_temp_summer(self):
        # daily_avg = (22+24+2*22)/4 = 22.5 > temp_summer=20 → OFF
        out, _ = self._run_full_day(22, 24, 22)
        assert out["heating_mode"] == 0

    def test_hysteresis_stays_on_between_thresholds(self):
        """Once ON, heating stays ON when temp is between winter and summer."""
        state = {}
        # Cold day → heating ON
        self._run_full_day(5, 6, 4, state=state)
        # Mild day (17 °C avg) — between 15 and 20 → must stay ON
        out, _ = self._run_full_day(17, 18, 17, state=state)
        assert out["heating_mode"] == 1

    def test_hysteresis_stays_off_between_thresholds(self):
        """Once OFF, heating stays OFF when temp is between thresholds."""
        state = {}
        # Warm day → heating OFF
        self._run_full_day(22, 24, 22, state=state)
        # Mild day (17 °C avg) — between 15 and 20 → must stay OFF
        out, _ = self._run_full_day(17, 18, 17, state=state)
        assert out["heating_mode"] == 0

    def test_hysteresis_turns_off_above_temp_summer(self):
        """Heating turns OFF once monthly_avg rises above temp_summer.

        After 1 cold day (avg≈4.75) we need ≥7 warm days (avg≈22.5) so that
        the rolling monthly average exceeds temp_summer=20 °C.
        """
        state = {}
        self._run_full_day(5, 6, 4, state=state)  # cold day → ON
        # 9 warm days: monthly_avg ≈ (4.75 + 9×22.5) / 10 = 206.75/10 = 20.675 > 20
        for _ in range(9):
            self._run_full_day(22, 24, 22, state=state)
        out, _ = self._run_full_day(22, 24, 22, state=state)  # 10th warm → OFF
        assert out["heating_mode"] == 0

    def test_debug_outputs_visible_before_day_complete(self):
        """t1/t2/t3 debug ports reflect stored slot values."""
        state = {}
        out, _ = self._run_slot("t1", 10.0, state=state)
        assert out["t1"] == pytest.approx(10.0)
        assert out["t2"] is None
        out2, _ = self._run_slot("t2", 14.0, state=state)
        assert out2["t2"] == pytest.approx(14.0)

    def test_monthly_avg_after_multiple_days(self):
        state = {}
        # Day 1: T_avg = (4+6+2*2)/4 = 14/4 = 3.5
        self._run_full_day(4, 6, 2, state=state)
        # Day 2: T_avg = (8+10+2*6)/4 = 30/4 = 7.5
        out, _ = self._run_full_day(8, 10, 6, state=state)
        # monthly_avg = (3.5 + 7.5) / 2 = 5.5
        assert out["monthly_avg"] == pytest.approx(5.5)

    def test_heating_mode_uses_monthly_avg(self):
        """When monthly_avg is available it determines heating_mode, not daily_avg."""
        state = {}
        # Build up warm monthly average (>20 °C → above temp_summer → OFF)
        for _ in range(3):
            self._run_full_day(22, 24, 22, state=state)  # daily_avg ≈ 22.5
        # Now send a cold day — monthly_avg is still warm → heating stays OFF
        out, _ = self._run_full_day(5, 6, 4, state=state)
        assert out["heating_mode"] == 0

    def test_no_input_returns_default_heating_mode(self):
        state = {}
        n1 = node("h", "heating_circuit", self._CFG)
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"h": {}})["h"]
        assert out["heating_mode"] == 0
        assert out["daily_avg"] is None

    def test_monthly_buffer_capped_at_31_days(self):
        state = {}
        for _ in range(40):  # push 40 days
            self._run_full_day(10, 12, 8, state=state)
        assert len(state["h"]["daily_temps"]) <= 31


# ===========================================================================
# min_max_tracker
# ===========================================================================


class TestMinMaxTracker:
    def _run(self, value, state=None):
        if state is None:
            state = {}
        n1 = node("m", "min_max_tracker", {})
        exc = make_executor([n1], hysteresis_state=state)
        return exc.execute({"m": {"value": value}})["m"], state

    def test_first_value_sets_min_and_max(self):
        out, _ = self._run(42.0)
        assert out["min_abs"] == pytest.approx(42.0)
        assert out["max_abs"] == pytest.approx(42.0)
        assert out["min_daily"] == pytest.approx(42.0)
        assert out["max_daily"] == pytest.approx(42.0)

    def test_lower_value_updates_min(self):
        state = {}
        out, state = self._run(10.0, state)
        out, state = self._run(5.0, state)
        assert out["min_abs"] == pytest.approx(5.0)
        assert out["max_abs"] == pytest.approx(10.0)

    def test_higher_value_updates_max(self):
        state = {}
        out, state = self._run(10.0, state)
        out, state = self._run(20.0, state)
        assert out["max_abs"] == pytest.approx(20.0)
        assert out["min_abs"] == pytest.approx(10.0)

    def test_all_periods_track_simultaneously(self):
        out, _ = self._run(7.5)
        for key in (
            "min_daily",
            "max_daily",
            "min_weekly",
            "max_weekly",
            "min_monthly",
            "max_monthly",
            "min_yearly",
            "max_yearly",
            "min_abs",
            "max_abs",
        ):
            assert out[key] == pytest.approx(7.5), f"{key} should be 7.5"

    def test_no_value_returns_current_state(self):
        state = {}
        out, state = self._run(5.0, state)
        # Execute without a new value — state must persist
        n1 = node("m", "min_max_tracker", {})
        exc = make_executor([n1], hysteresis_state=state)
        out2 = exc.execute({"m": {}})["m"]
        assert out2["min_abs"] == pytest.approx(5.0)

    def test_period_reset_on_new_day(self):
        state = {}
        out, state = self._run(100.0, state)
        # Simulate next day by clearing last_day key
        state["m"]["last_day"] = "1970-01-01"
        out, state = self._run(5.0, state)
        # Daily min/max reset; absolute stays
        assert out["min_daily"] == pytest.approx(5.0)
        assert out["max_daily"] == pytest.approx(5.0)
        assert out["min_abs"] == pytest.approx(5.0)  # 5 < 100
        assert out["max_abs"] == pytest.approx(100.0)

    def test_seed_abs_min_max_applied_once(self):
        """Startwerte für abs_min/abs_max werden einmalig übernommen."""
        cfg = {"init_abs_min": -10.0, "init_abs_max": 999.0}
        state = {}
        n1 = node("m", "min_max_tracker", cfg)
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"m": {"value": 50.0}})["m"]
        assert out["min_abs"] == pytest.approx(-10.0)  # seed beats first value
        assert out["max_abs"] == pytest.approx(999.0)

    def test_seed_not_reapplied_after_first_run(self):
        """Nach dem ersten Lauf überschreibt der Seed die laufenden Werte nicht."""
        cfg = {"init_abs_min": 100.0, "init_abs_max": 200.0}
        state = {}
        n1 = node("m", "min_max_tracker", cfg)
        # First run — seed applied
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"m": {"value": 150.0}})
        # Second run with a value below seed — abs_min must be updated
        exc2 = make_executor([n1], hysteresis_state=state)
        out = exc2.execute({"m": {"value": 50.0}})["m"]
        assert out["min_abs"] == pytest.approx(50.0)  # new minimum, seed not re-applied

    def test_seed_period_values(self):
        """Startwerte für Tages- und Monats-Min/Max werden korrekt gesetzt."""
        cfg = {
            "init_day_min": 5.0,
            "init_day_max": 25.0,
            "init_year_min": -5.0,
            "init_year_max": 40.0,
        }
        state = {}
        n1 = node("m", "min_max_tracker", cfg)
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"m": {"value": 15.0}})["m"]
        assert out["min_daily"] == pytest.approx(5.0)
        assert out["max_daily"] == pytest.approx(25.0)
        assert out["min_yearly"] == pytest.approx(-5.0)
        assert out["max_yearly"] == pytest.approx(40.0)


# ===========================================================================
# consumption_counter
# ===========================================================================


class TestConsumptionCounter:
    def _run(self, value, state=None):
        if state is None:
            state = {}
        n1 = node("c", "consumption_counter", {})
        exc = make_executor([n1], hysteresis_state=state)
        return exc.execute({"c": {"value": value}})["c"], state

    def test_first_value_sets_no_consumption(self):
        # First reading — no previous value, so delta = 0
        out, _ = self._run(1000.0)
        assert out["daily"] == pytest.approx(0.0)
        assert out["yearly"] == pytest.approx(0.0)

    def test_second_value_computes_delta(self):
        state = {}
        out, state = self._run(1000.0, state)
        out, state = self._run(1010.0, state)
        assert out["daily"] == pytest.approx(10.0)
        assert out["weekly"] == pytest.approx(10.0)
        assert out["monthly"] == pytest.approx(10.0)
        assert out["yearly"] == pytest.approx(10.0)

    def test_multiple_deltas_accumulate(self):
        state = {}
        self._run(0.0, state)
        self._run(5.0, state)
        out, _ = self._run(12.0, state)
        assert out["daily"] == pytest.approx(12.0)

    def test_counter_rollover_ignored(self):
        """A lower value than previous (e.g. meter rollover) must not subtract."""
        state = {}
        self._run(9990.0, state)
        out, _ = self._run(5.0, state)  # rollover: 5 < 9990
        assert out["daily"] == pytest.approx(0.0)

    def test_period_reset_saves_previous_period(self):
        state = {}
        self._run(0.0, state)
        self._run(50.0, state)  # daily = 50
        # Simulate new day
        state["c"]["last_day"] = "1970-01-01"
        # First call in new day: triggers reset (prev_daily = 50, daily = 0), then adds delta 60-50=10
        out, _ = self._run(60.0, state)
        assert out["prev_daily"] == pytest.approx(50.0)
        assert out["daily"] == pytest.approx(10.0)

    def test_no_value_returns_current_state(self):
        state = {}
        self._run(100.0, state)
        self._run(120.0, state)
        n1 = node("c", "consumption_counter", {})
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"c": {}})["c"]
        assert out["daily"] == pytest.approx(20.0)

    def test_all_periods_accumulate_independently(self):
        state = {}
        self._run(0.0, state)
        out, _ = self._run(100.0, state)
        assert out["daily"] == pytest.approx(100.0)
        assert out["weekly"] == pytest.approx(100.0)
        assert out["monthly"] == pytest.approx(100.0)
        assert out["yearly"] == pytest.approx(100.0)

    def test_seed_meter_used_as_first_value(self):
        """init_meter setzt den Startzählerstand; erster Delta rechnet korrekt."""
        cfg = {"init_meter": 1000.0}
        state = {}
        n1 = node("c", "consumption_counter", cfg)
        exc = make_executor([n1], hysteresis_state=state)
        # Without seed, first value would set last_value=1050, delta=0.
        # With seed, last_value=1000 → delta = 1050-1000 = 50.
        out = exc.execute({"c": {"value": 1050.0}})["c"]
        assert out["daily"] == pytest.approx(50.0)

    def test_seed_period_totals_applied_once(self):
        """Startwerte für Perioden werden übernommen und korrekt weitergeführt."""
        cfg = {"init_meter": 500.0, "init_monthly": 300.0, "init_yearly": 1200.0}
        state = {}
        n1 = node("c", "consumption_counter", cfg)
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"c": {"value": 510.0}})["c"]
        assert out["monthly"] == pytest.approx(310.0)  # 300 seed + 10 delta
        assert out["yearly"] == pytest.approx(1210.0)  # 1200 seed + 10 delta

    def test_seed_not_reapplied_after_first_run(self):
        """Seed wird nur einmal angewendet, nicht bei jedem Executor-Aufruf."""
        cfg = {"init_meter": 0.0, "init_daily": 50.0}
        state = {}
        n1 = node("c", "consumption_counter", cfg)
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"c": {"value": 10.0}})  # daily = 50 + 10 = 60
        exc2 = make_executor([n1], hysteresis_state=state)
        out = exc2.execute({"c": {"value": 20.0}})["c"]  # daily = 60 + 10 = 70
        assert out["daily"] == pytest.approx(70.0)  # seed NOT added again
