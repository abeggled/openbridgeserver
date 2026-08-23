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

import json
import math
import time
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import ClassVar
from zoneinfo import ZoneInfo

import pytest

from obs.logic.executor import ExecutionError, GraphExecutor, _OpaqueRecoveredDict, _OpaqueRecoveredSet, _OpaqueRecoveredStr
from tests.unit.conftest import edge, make_executor, node


def test_execute_captures_incoming_and_effective_inputs_without_mutating_values():
    base = make_executor(
        [node("source", "const_value", {"value": 3}), node("target", "math_formula", {"formula": "a * 2"})],
        [edge("source", "target", "value", "in1")],
    )
    captured = {}
    executor = GraphExecutor(base.flow, input_capture=captured)

    outputs = executor.execute({"target": {"in1": 7}})

    assert outputs["target"]["result"] == 14
    assert captured["target"]["in1"] == {"incoming": 3.0, "effective": 7, "overridden": True}


def test_execute_captures_configured_compare_operand_as_effective_input():
    base = make_executor(
        [node("source", "const_value", {"value": 3}), node("target", "compare", {"operator": ">", "operand": 2})],
        [edge("source", "target", "value", "in1")],
    )
    captured = {}
    executor = GraphExecutor(base.flow, input_capture=captured)

    outputs = executor.execute()

    assert outputs["target"]["out"] is True
    assert captured["target"]["in2"] == {"incoming": None, "effective": 2, "overridden": False}


def test_execute_captures_configured_string_values_and_override_precedence():
    base = make_executor(
        [node("target", "string_concat", {"count": 3, "separator": "-", "text_1": "A", "text_2": "B"})],
    )
    captured = {}
    executor = GraphExecutor(base.flow, input_capture=captured)

    outputs = executor.execute({"target": {"in_2": "override"}})

    assert outputs["target"]["result"] == "A-override-"
    assert captured["target"] == {
        "in_1": {"incoming": None, "effective": "A", "overridden": False},
        "in_2": {"incoming": None, "effective": "override", "overridden": True},
        "in_3": {"incoming": None, "effective": "", "overridden": False},
    }


def test_execute_isolates_mutable_inputs_from_python_script_mutation():
    mutable = {"x": 1}
    base = make_executor(
        [node("target", "python_script", {"script": "inputs['a']['x'] = 2; result = inputs['a']"})],
    )
    captured = {}
    executor = GraphExecutor(base.flow, input_capture=captured)

    outputs = executor.execute(
        {"target": {"a": mutable}},
        capture_incoming_overrides={"target": {"a": mutable}},
    )

    assert mutable == {"x": 1}
    assert outputs["target"]["result"] == {"x": 2}
    assert captured["target"]["a"] == {
        "incoming": {"x": 1},
        "effective": {"x": 1},
        "overridden": True,
    }


def test_python_script_cannot_mutate_shared_ical_outputs_between_replays():
    today = datetime.now(UTC).strftime("%Y%m%d")
    raw = f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:{today}\r\nSUMMARY:Original\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    base = make_executor(
        [
            node("calendar", "ical", {"filters": '[{"pattern": "Original"}]'}),
            node(
                "script",
                "python_script",
                {"script": "inputs['events'][0][3] = 'mutated'; result = inputs['events']"},
            ),
        ],
        [edge("calendar", "script", "f0_array", "events")],
    )
    cache = {}
    executor = GraphExecutor(
        base.flow,
        {"calendar": {"raw": raw}},
        {"timezone": "UTC"},
        ical_result_cache=cache,
        ical_cache_outputs_owned=True,
    )

    first = executor.execute()
    second = executor.execute()

    assert first["script"]["result"][0][3] == "mutated"
    assert first["calendar"]["f0_array"][0][3] == "Original"
    assert second["calendar"]["f0_array"][0][3] == "Original"
    assert cache["calendar"]["outputs"]["f0_array"][0][3] == "Original"


def test_invalid_configured_string_input_count_is_isolated_to_its_node():
    executor = make_executor([node("target", "string_concat", {"count": "invalid"})])

    outputs = executor.execute()

    assert "invalid literal" in outputs["target"]["__error__"]


def _replace_node(rules, data=None):
    return node("target", "string_replace", {"rules": rules, **(data or {})})


def test_string_replace_applies_plain_rules_in_order_on_the_intermediate_result():
    executor = make_executor(
        [
            _replace_node(
                json.dumps(
                    [
                        {"search": "cold", "replace": "warm", "mode": "plain"},
                        {"search": "warm water", "replace": "hot water", "mode": "plain"},
                    ]
                )
            )
        ]
    )

    outputs = executor.execute({"target": {"text": "the cold water is cold"}})

    assert outputs["target"]["result"] == "the hot water is warm"


def test_string_replace_honours_first_occurrence_and_case_sensitivity_per_rule():
    executor = make_executor(
        [
            _replace_node(
                json.dumps(
                    [
                        {"search": "a", "replace": "-", "replace_all": False},
                        {"search": "B", "replace": "+", "case_sensitive": False},
                    ]
                )
            )
        ]
    )

    outputs = executor.execute({"target": {"text": "aabb"}})

    assert outputs["target"]["result"] == "-a++"


def test_string_replace_case_insensitive_plain_rule_keeps_the_replacement_literal():
    executor = make_executor([_replace_node(json.dumps([{"search": "x", "replace": "\\1&", "case_sensitive": False}]))])

    outputs = executor.execute({"target": {"text": "aXb"}})

    assert outputs["target"]["result"] == "a\\1&b"


def test_string_replace_regex_rule_supports_group_references_and_flags():
    executor = make_executor(
        [
            _replace_node(
                json.dumps(
                    [
                        {"search": r"(\d+)-(\d+)", "replace": r"\2/\1", "mode": "regex"},
                        {"search": "ABC", "replace": "ok", "mode": "regex", "case_sensitive": False},
                    ]
                )
            )
        ]
    )

    outputs = executor.execute({"target": {"text": "abc 12-34"}})

    assert outputs["target"]["result"] == "ok 34/12"


def test_string_replace_regex_rule_can_be_limited_to_the_first_match():
    executor = make_executor([_replace_node(json.dumps([{"search": r"\d", "replace": "#", "mode": "regex", "replace_all": False}]))])

    outputs = executor.execute({"target": {"text": "1a2"}})

    assert outputs["target"]["result"] == "#a2"


@pytest.mark.parametrize(
    ("flag_value", "expected"),
    [
        (True, "ax"),
        ("true", "ax"),
        (False, "xx"),
        (None, "xx"),
        (0, "xx"),
        ("false", "xx"),
        (" Off ", "xx"),
    ],
    ids=["true", "string-true", "false", "null", "zero", "string-false", "padded-off"],
)
def test_string_replace_coerces_non_boolean_rule_flags(flag_value, expected):
    """Mirrored by replaceRuleFlag() in NodeConfigPanel.vue — keep both in sync."""
    executor = make_executor([_replace_node(json.dumps([{"search": "A", "replace": "x", "case_sensitive": flag_value}]))])

    outputs = executor.execute({"target": {"text": "aA"}})

    assert outputs["target"]["result"] == expected


def test_string_replace_defaults_both_rule_flags_to_true_when_absent():
    executor = make_executor([_replace_node(json.dumps([{"search": "A", "replace": "x"}]))])

    outputs = executor.execute({"target": {"text": "aAA"}})

    assert outputs["target"]["result"] == "axx"


@pytest.mark.parametrize("mode", ["regex", " REGEX ", "Regex"])
def test_string_replace_normalises_the_rule_mode(mode):
    """Mirrored by replaceRuleIsRegex() in NodeConfigPanel.vue — keep both in sync."""
    executor = make_executor([_replace_node(json.dumps([{"search": r"\(a\)", "replace": "X", "mode": mode}]))])

    outputs = executor.execute({"target": {"text": "(a)a"}})

    assert outputs["target"]["result"] == "Xa"


@pytest.mark.parametrize("mode", ["plain", "", None, "fuzzy"])
def test_string_replace_treats_any_other_mode_as_a_plain_search(mode):
    executor = make_executor([_replace_node(json.dumps([{"search": "(a)", "replace": "X", "mode": mode}]))])

    outputs = executor.execute({"target": {"text": "(a)a"}})

    assert outputs["target"]["result"] == "Xa"


@pytest.mark.parametrize(
    "rule",
    [
        {"search": "[", "replace": "x", "mode": "regex"},
        {"search": "(a)", "replace": r"\9", "mode": "regex"},
    ],
    ids=["invalid-pattern", "invalid-group-reference"],
)
def test_string_replace_skips_a_broken_regex_rule_instead_of_dropping_the_text(rule):
    executor = make_executor([_replace_node(json.dumps([rule, {"search": "a", "replace": "b"}]))])

    outputs = executor.execute({"target": {"text": "a["}})

    assert outputs["target"]["result"] == "b["


@pytest.mark.parametrize(
    "rule",
    [
        {"search": "", "replace": "x"},
        {"replace": "x"},
        {"search": 42, "replace": "x"},
    ],
    ids=["empty-search", "missing-search", "non-string-search"],
)
def test_string_replace_skips_a_rule_without_a_usable_search_term(rule):
    executor = make_executor([_replace_node(json.dumps([rule]))])

    outputs = executor.execute({"target": {"text": "a b"}})

    assert outputs["target"]["result"] == "a b"


def test_string_replace_treats_a_missing_replacement_as_deletion_and_coerces_other_types():
    executor = make_executor([_replace_node(json.dumps([{"search": " "}, {"search": "b", "replace": 2}]))])

    outputs = executor.execute({"target": {"text": "a b"}})

    assert outputs["target"]["result"] == "a2"


def test_string_replace_accepts_rules_stored_as_a_list_and_coerces_the_input_to_text():
    executor = make_executor([_replace_node([{"search": "2", "replace": "9", "mode": "plain"}])])

    outputs = executor.execute({"target": {"text": 1234}})

    assert outputs["target"]["result"] == "1934"


def test_string_replace_without_input_returns_no_result():
    executor = make_executor([_replace_node(json.dumps([{"search": "a", "replace": "b"}]))])

    outputs = executor.execute()

    assert outputs["target"] == {"result": None}


def test_string_replace_with_unparsable_rules_passes_the_text_through():
    executor = make_executor([_replace_node("not json")])

    outputs = executor.execute({"target": {"text": "unchanged"}})

    assert outputs["target"]["result"] == "unchanged"


def test_datetime_node_uses_application_formats():
    executor = make_executor(
        [node("clock", "datetime", {"custom_format": "yyyy-MM-dd HH:mm:ss"})],
        app_config={"timezone": "UTC", "date_format": "yyyy/MM/dd", "time_format": "HH-mm"},
    )

    output = executor.execute()["clock"]

    assert output["date"].count("/") == 2
    assert output["time"].count("-") == 1
    assert len(output["custom"]) == 19


def test_datetime_node_localizes_names_and_preserves_literal_words():
    executor = make_executor(
        [node("clock", "datetime", {"custom_format": "EEEE MMMM guguseli"})],
        app_config={"timezone": "UTC", "language": "de"},
    )

    output = executor.execute()["clock"]["custom"]

    assert "guguseli" in output
    assert any(day in output for day in ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"))


def test_datetime_node_uses_schema_default_for_custom_output():
    executor = make_executor([node("clock", "datetime", {})], app_config={"timezone": "UTC", "language": "en"})

    output = executor.execute()["clock"]["custom"]

    assert "," in output
    assert ":" in output


def test_datetime_node_falls_back_to_utc_for_invalid_timezone():
    executor = make_executor(
        [node("clock", "datetime", {"custom_format": "yyyy"})],
        app_config={"timezone": "not-a-timezone", "date_format": "yyyy", "time_format": "HH"},
    )

    output = executor.execute()["clock"]

    assert len(output["date"]) == 4
    assert len(output["time"]) == 2


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

    def test_infinite_value_falls_back_to_builtin_round(self):
        """Decimal.quantize() can't handle an infinite value (InvalidOperation)
        — _round_half_up must fall back to Python's built-in round() instead
        of propagating the error."""
        assert GraphExecutor._round_half_up(float("inf")) == float("inf")


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

    def test_math_namespace_sqrt(self):
        assert GraphExecutor._safe_eval("math.sqrt(x)", {"x": 9}) == pytest.approx(3.0)

    def test_math_dunder_attribute_blocked(self):
        with pytest.raises(ExecutionError):
            GraphExecutor._safe_eval("math.__dict__", {})

    def test_lambda_syntax_blocked(self):
        with pytest.raises(ExecutionError):
            GraphExecutor._safe_eval("(lambda x: x)(1)", {})

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

    def test_attribute_access_blocked(self):
        with pytest.raises(ExecutionError):
            GraphExecutor._safe_eval("().__class__.__bases__", {})


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
# comment node — purely visual, no executor case (falls through to the
# "unknown node type" no-op branch, same as ai_logic)
# ===========================================================================


class TestCommentNode:
    def test_is_a_no_op(self):
        out = run_single("comment", {"text": "Hysterese-Gating: siehe #1043", "width": 220, "height": 140})
        assert out == {}


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

    @pytest.mark.parametrize(
        "op, a, b, expected",
        [
            ("gt", 5, 3, True),
            ("lt", 3, 5, True),
            ("eq", 5, 5, True),
            ("gte", 5, 5, True),
            ("lte", 4, 5, True),
            ("ne", 4, 5, True),
        ],
    )
    def test_operator_aliases(self, op, a, b, expected):
        out = run_single("compare", {"operator": op}, {"in1": a, "in2": b})
        assert out["out"] is expected

    def test_static_operand_is_used_when_in2_is_unwired(self):
        out = run_single("compare", {"operator": "lt", "operand": 50}, {"in1": 10})
        assert out["out"] is True

    def test_static_operand_is_not_used_when_in2_is_wired_but_none(self):
        out = run_single("compare", {"operator": "lt", "operand": 50}, {"in1": 10, "in2": None})
        assert out["out"] is False

    def test_blank_static_operand_is_treated_as_missing(self):
        out = run_single("compare", {"operator": "gt", "operand": ""}, {"in1": 10})
        assert out["out"] is False

    @pytest.mark.parametrize(
        "op, a, b, expected",
        [
            ("eq", "open", "closed", False),
            ("eq", "open", "open", True),
            ("ne", "open", "closed", True),
            ("ne", "open", "open", False),
        ],
    )
    def test_operator_aliases_compare_nonnumeric_strings_as_strings(self, op, a, b, expected):
        out = run_single("compare", {"operator": op}, {"in1": a, "in2": b})
        assert out["out"] is expected

    @pytest.mark.parametrize("op", [">", "gt", "<", "lt", ">=", "gte", "<=", "lte"])
    def test_ordering_comparison_returns_false_for_nonnumeric_strings(self, op):
        out = run_single("compare", {"operator": op}, {"in1": "open", "in2": "closed"})
        assert out["out"] is False

    def test_compare_treats_bool_as_numeric(self):
        out = run_single("compare", {"operator": "eq"}, {"in1": True, "in2": 1})
        assert out["out"] is True

    @pytest.mark.parametrize(
        "op, expected",
        [("gt", False), ("lt", False), ("eq", False), ("ne", True)],
    )
    def test_compare_handles_mixed_numeric_and_nonnumeric_values(self, op, expected):
        out = run_single("compare", {"operator": op}, {"in1": "open", "in2": 50})
        assert out["out"] is expected

    def test_none_input_returns_false(self):
        out = run_single("compare", {"operator": ">"}, {"in1": None, "in2": 5})
        assert out["out"] is False

    def test_default_operator_is_greater_than(self):
        out = run_single("compare", {}, {"in1": 10, "in2": 5})
        assert out["out"] is True

    def test_result_source_handle_from_compare_flows_to_downstream_node(self):
        nodes = [
            node("value", "const_value", {"value": "10", "data_type": "number"}),
            node("cmp", "compare", {"operator": "lt", "operand": 50}),
            node("invert", "not", {}),
        ]
        edges = [
            edge("value", "cmp", "value", "in1"),
            edge("cmp", "invert", "result", "in1"),
        ]
        exc = make_executor(nodes, edges)

        out = exc.execute()

        assert out["cmp"]["out"] is True
        assert out["invert"]["out"] is False

    def test_out_source_handle_from_result_node_flows_to_downstream_node(self):
        nodes = [
            node("a", "const_value", {"value": "2", "data_type": "number"}),
            node("b", "const_value", {"value": "3", "data_type": "number"}),
            node("sum", "math_formula", {"formula": "a + b"}),
            node("cmp", "compare", {"operator": "eq", "operand": 5}),
        ]
        edges = [
            edge("a", "sum", "value", "in1"),
            edge("b", "sum", "value", "in2"),
            edge("sum", "cmp", "out", "in1"),
        ]
        exc = make_executor(nodes, edges)

        out = exc.execute()

        assert out["sum"]["result"] == 5
        assert out["cmp"]["out"] is True

    def test_unknown_source_handle_resolves_to_none(self):
        nodes = [
            node("value", "const_value", {"value": "10", "data_type": "number"}),
            node("cmp", "compare", {"operator": "gt", "operand": 5}),
        ]
        edges = [edge("value", "cmp", "missing", "in1")]
        exc = make_executor(nodes, edges)

        out = exc.execute()

        assert out["cmp"]["out"] is False


# ===========================================================================
# decision / value_mapping nodes
# ===========================================================================


class TestDecisionNode:
    def test_conditions_are_evaluated_independently(self):
        out = run_single(
            "decision",
            {
                "conditions": [
                    {"handle": "hot", "operator": "gte", "value": 25},
                    {"handle": "comfortable", "operator": "range", "min": 20, "max": 26},
                    {"handle": "cold", "operator": "lt", "value": 18},
                ],
            },
            {"value": 25},
        )

        assert out == {"hot": True, "comfortable": True, "cold": False}

    def test_text_and_regex_conditions(self):
        out = run_single(
            "decision",
            {
                "conditions": [
                    {"handle": "contains", "operator": "contains", "value": "open"},
                    {"handle": "starts", "operator": "starts_with", "value": "Door"},
                    {"handle": "regex", "operator": "regex", "value": r"open-\d+"},
                ],
            },
            {"value": "Door open-42"},
        )

        assert out == {"contains": True, "starts": True, "regex": True}

    def test_default_outputs_exist_without_configuration(self):
        out = run_single("decision", {}, {"value": "anything"})

        assert out == {"out_1": False, "out_2": False}

    def test_condition_without_compare_value_is_inert(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "empty_default", "operator": "eq"}]},
            {"value": ""},
        )

        assert out["empty_default"] is False

    def test_conditions_can_be_loaded_from_json_string(self):
        out = run_single(
            "decision",
            {"conditions": json.dumps([{"handle": "match", "operator": "eq", "value": "on"}])},
            {"value": "on"},
        )

        assert out["match"] is True

    def test_equality_condition_can_match_empty_string(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "empty", "operator": "eq", "value": ""}]},
            {"value": ""},
        )

        assert out["empty"] is True

    def test_equality_condition_matches_large_integers_exactly(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "match", "operator": "eq", "value": 9007199254740993}]},
            {"value": 9007199254740993},
        )

        assert out["match"] is True

    def test_equality_condition_matches_numeric_values(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "match", "operator": "eq", "value": 5.5}]},
            {"value": 5.5},
        )

        assert out["match"] is True

    def test_equality_condition_matches_structurally_equal_lists(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "match", "operator": "eq", "value": [1, 2]}]},
            {"value": [1, 2]},
        )

        assert out["match"] is True

    def test_equality_condition_matches_a_list_against_its_text_repr(self):
        """Regression: a Decision rule's compare value is entered through a
        NodeConfigPanel text input and is therefore always a string — an
        API/JSON list [1, 2] must still match a rule configured as the
        string "[1, 2]", same as before change_filter's stricter, type-
        sensitive persisted-state comparison was introduced. That stricter
        behavior must stay local to change_filter, not leak into Decision/
        Value Mapping conditions and silently break existing rules."""
        out = run_single(
            "decision",
            {"conditions": [{"handle": "match", "operator": "eq", "value": "[1, 2]"}]},
            {"value": [1, 2]},
        )

        assert out["match"] is True

    @pytest.mark.parametrize(
        "input_value, expected_value, expected",
        [
            (True, "true", True),
            (False, "false", True),
            (True, "false", False),
            ("false", False, True),
        ],
    )
    def test_equality_condition_normalizes_boolean_literals(self, input_value, expected_value, expected):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "bool_match", "operator": "eq", "value": expected_value}]},
            {"value": input_value},
        )

        assert out["bool_match"] is expected

    def test_invalid_rule_json_falls_back_to_default_outputs(self):
        out = run_single("decision", {"conditions": "not json"}, {"value": "on"})

        assert out == {"out_1": False, "out_2": False}

    def test_non_list_rule_json_falls_back_to_default_outputs(self):
        out = run_single("decision", {"conditions": json.dumps({"operator": "eq", "value": "on"})}, {"value": "on"})

        assert out == {"out_1": False, "out_2": False}

    def test_non_dict_rule_entries_are_ignored(self):
        out = run_single(
            "decision",
            {"conditions": ["ignored", {"handle": "match", "operator": "eq", "value": "on"}]},
            {"value": "on"},
        )

        assert out == {"match": True}

    def test_invalid_regex_condition_returns_false(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "bad_re", "operator": "regex", "value": "["}]},
            {"value": "abc"},
        )

        assert out["bad_re"] is False

    def test_case_sensitive_text_condition(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "case", "operator": "contains", "value": "OPEN", "case_sensitive": True}]},
            {"value": "door open"},
        )

        assert out["case"] is False

    def test_text_operators_support_case_insensitive_variants(self):
        out = run_single(
            "decision",
            {
                "conditions": [
                    {"handle": "text", "operator": "text_eq", "value": "OPEN"},
                    {"handle": "ends", "operator": "ends_with", "value": "42"},
                ],
            },
            {"value": "open-42"},
        )

        assert out == {"text": False, "ends": True}

    @pytest.mark.parametrize("operator_key", ["contains", "starts_with", "ends_with"])
    def test_blank_substring_conditions_do_not_match_everything(self, operator_key):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "blank", "operator": operator_key, "value": ""}]},
            {"value": "anything"},
        )

        assert out["blank"] is False

    def test_range_accepts_value_and_value_to_aliases(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "in_range", "operator": "range", "value": 10, "value_to": 20}]},
            {"value": 15},
        )

        assert out["in_range"] is True

    def test_range_with_non_numeric_input_returns_false(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "bad_range", "operator": "range", "min": 10, "max": 20}]},
            {"value": "hot"},
        )

        assert out["bad_range"] is False

    def test_numeric_compare_with_non_numeric_value_returns_false(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "gt", "operator": "gt", "value": 10}]},
            {"value": "hot"},
        )

        assert out["gt"] is False

    def test_none_input_returns_false_for_condition(self):
        out = run_single(
            "decision",
            {"conditions": [{"handle": "match", "operator": "eq", "value": ""}]},
            {"value": None},
        )

        assert out["match"] is False


class TestHostCheckNode:
    def test_placeholder_outputs_when_not_triggered(self):
        out = run_single("host_check", {"host": "192.168.1.1"}, inputs={"trigger": False})
        assert out["reachable"] is False
        assert out["latency_ms"] is None

    def test_placeholder_outputs_when_triggered(self):
        # Executor itself always returns the placeholder; the manager performs the actual ping
        out = run_single("host_check", {"host": "192.168.1.1"}, inputs={"trigger": True})
        assert out["reachable"] is False
        assert out["latency_ms"] is None

    def test_trigger_is_forwarded(self):
        out = run_single("host_check", {"host": "192.168.1.1"}, inputs={"trigger": True})
        assert out["_trigger"] is True

    def test_trigger_false_not_forwarded(self):
        out = run_single("host_check", {"host": "192.168.1.1"}, inputs={"trigger": False})
        assert out["_trigger"] is False

    def test_missing_trigger_input_defaults_to_false(self):
        out = run_single("host_check", {"host": "192.168.1.1"})
        assert out["_trigger"] is False

    def test_all_three_output_keys_present(self):
        out = run_single("host_check", {"host": "192.168.1.1"})
        assert "_trigger" in out
        assert "reachable" in out
        assert "latency_ms" in out


class TestValueMappingNode:
    def test_first_matching_rule_wins(self):
        out = run_single(
            "value_mapping",
            {
                "output_type": "string",
                "rules": [
                    {"operator": "gte", "value": 20, "result": "warm"},
                    {"operator": "gte", "value": 10, "result": "mild"},
                ],
                "has_default": True,
                "default_value": "cold",
            },
            {"value": 25},
        )

        assert out["result"] == "warm"

    @pytest.mark.parametrize(
        "output_type, result, expected",
        [
            ("bool", "true", True),
            ("int", "42.8", 42),
            ("float", "21.5", 21.5),
            ("string", 7, "7"),
        ],
    )
    def test_result_is_coerced_to_selected_output_type(self, output_type, result, expected):
        out = run_single(
            "value_mapping",
            {
                "output_type": output_type,
                "rules": [{"operator": "eq", "value": "on", "result": result}],
            },
            {"value": "on"},
        )

        assert out["result"] == expected

    def test_default_value_is_used_when_no_rule_matches(self):
        out = run_single(
            "value_mapping",
            {
                "output_type": "int",
                "rules": [{"operator": "eq", "value": "open", "result": 1}],
                "has_default": True,
                "default_value": "0",
            },
            {"value": "closed"},
        )

        assert out["result"] == 0

    def test_no_match_without_default_returns_none(self):
        out = run_single(
            "value_mapping",
            {"rules": [{"operator": "eq", "value": "open", "result": "yes"}]},
            {"value": "closed"},
        )

        assert out["result"] is None

    def test_rule_without_compare_value_is_inert(self):
        out = run_single(
            "value_mapping",
            {"rules": [{"operator": "eq", "result": "blank"}]},
            {"value": ""},
        )

        assert out["result"] is None

    def test_string_false_default_flag_is_not_treated_as_enabled(self):
        out = run_single(
            "value_mapping",
            {
                "rules": [{"operator": "eq", "value": "open", "result": "yes"}],
                "has_default": "false",
                "default_value": "fallback",
            },
            {"value": "closed"},
        )

        assert out["result"] is None

    def test_invalid_int_result_coerces_to_zero(self):
        out = run_single(
            "value_mapping",
            {
                "output_type": "int",
                "rules": [{"operator": "eq", "value": "on", "result": "not-int"}],
            },
            {"value": "on"},
        )

        assert out["result"] == 0

    def test_invalid_float_default_coerces_to_zero(self):
        out = run_single(
            "value_mapping",
            {
                "output_type": "float",
                "rules": [{"operator": "eq", "value": "on", "result": "1.5"}],
                "has_default": True,
                "default_value": "not-float",
            },
            {"value": "off"},
        )

        assert out["result"] == 0.0

    def test_string_default_none_coerces_to_empty_string(self):
        out = run_single(
            "value_mapping",
            {
                "output_type": "string",
                "rules": [{"operator": "eq", "value": "on", "result": "yes"}],
                "has_default": True,
                "default_value": None,
            },
            {"value": "off"},
        )

        assert out["result"] == ""


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


class TestMergeNode:
    def test_first_execution_outputs_the_only_wired_input(self):
        n1 = node("m", "merge", {})
        exc = make_executor([n1], hysteresis_state={})
        out = exc.execute({"m": {"in1": 5}})
        assert out["m"]["out"] == 5

    def test_first_execution_skips_a_wired_but_valueless_input(self):
        n1 = node("m", "merge", {})
        exc = make_executor([n1], hysteresis_state={})
        out = exc.execute({"m": {"in1": None, "in2": 7}})
        assert out["m"]["out"] == 7

    def test_switches_to_whichever_input_changed(self):
        state: dict = {}
        n1 = node("m", "merge", {})
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"m": {"in1": 5}})

        exc2 = make_executor([n1], hysteresis_state=state)
        out = exc2.execute({"m": {"in1": 5, "in2": 10}})  # in1 unchanged, in2 new
        assert out["m"]["out"] == 10

    def test_keeps_previous_active_input_when_nothing_changed(self):
        state: dict = {}
        n1 = node("m", "merge", {})
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"m": {"in1": 5}})

        exc2 = make_executor([n1], hysteresis_state=state)
        out = exc2.execute({"m": {"in1": 5}})  # unchanged
        assert out["m"]["out"] == 5

    def test_highest_port_wins_when_several_change_in_the_same_tick(self):
        n1 = node("m", "merge", {})
        exc = make_executor([n1], hysteresis_state={})
        out = exc.execute({"m": {"in1": 1, "in2": 2}})  # both new on the first tick
        assert out["m"]["out"] == 2

    def test_active_input_can_switch_back_after_a_later_tick(self):
        state: dict = {}
        n1 = node("m", "merge", {})
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"m": {"in1": 1, "in2": 2}})  # in2 becomes active

        exc2 = make_executor([n1], hysteresis_state=state)
        out = exc2.execute({"m": {"in1": 9, "in2": 2}})  # in1 changes, in2 unchanged
        assert out["m"]["out"] == 9

    def test_dynamic_input_count_beyond_two(self):
        n1 = node("m", "merge", {"input_count": 3})
        exc = make_executor([n1], hysteresis_state={})
        out = exc.execute({"m": {"in3": 42}})
        assert out["m"]["out"] == 42

    def test_unwired_inputs_beyond_input_count_are_ignored(self):
        state: dict = {}
        n1 = node("m", "merge", {"input_count": 2})
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"m": {"in1": 1}})
        assert out["m"]["out"] == 1
        assert set(state["m"]["values"]) == {"in1"}

    def test_shrinking_input_count_drops_a_stale_active_port(self):
        # in1/in2 have no recorded history under this (now-shrunk) state, so
        # both count as "changed" on this first tick — highest port wins,
        # same as any other simultaneous-change tick.
        state: dict = {"m": {"values": {"in3": 99}, "active": "in3"}}
        n1 = node("m", "merge", {"input_count": 2})
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"m": {"in1": 1, "in2": 2}})  # in3 no longer exists
        assert out["m"]["out"] == 2

    def test_shrinking_input_count_falls_back_to_first_wired_when_only_one_input_is_present(self):
        state: dict = {"m": {"values": {"in3": 99}, "active": "in3"}}
        n1 = node("m", "merge", {"input_count": 2})
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"m": {"in1": 1}})
        assert out["m"]["out"] == 1

    def test_state_persists_between_executions(self):
        """Regression: hysteresis_state={} must not be treated as None."""
        state: dict = {}
        n1 = node("m", "merge", {})
        exc = GraphExecutor(
            flow=__import__("obs.logic.models", fromlist=["FlowData"]).FlowData.model_validate({"nodes": [n1], "edges": []}),
            hysteresis_state=state,
        )
        exc.execute({"m": {"in1": 1}})
        assert "m" in state

    def test_no_wired_input_yields_none(self):
        n1 = node("m", "merge", {})
        exc = make_executor([n1], hysteresis_state={})
        out = exc.execute({})
        assert out["m"]["out"] is None


# ===========================================================================
# change_filter node
# ===========================================================================


class TestChangeFilterNode:
    def test_first_value_is_always_reported_as_changed(self):
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"cf": {"in": "foo"}})
        assert out["cf"] == {"out": "foo", "changed": True}

    def test_repeated_identical_value_is_suppressed(self):
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": "foo"}})
        out = exc.execute({"cf": {"in": "foo"}})
        assert out["cf"] == {"out": "foo", "changed": False}

    def test_repeated_nan_value_is_suppressed(self):
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": float("nan")}})

        out = exc.execute({"cf": {"in": float("nan")}})

        assert math.isnan(out["cf"]["out"])
        assert out["cf"]["changed"] is False

    @pytest.mark.parametrize("value", [Decimal("NaN"), [Decimal("NaN")], {"value": Decimal("NaN")}])
    def test_repeated_decimal_nan_value_is_suppressed(self, value):
        state = {}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)
        exc.execute({"cf": {"in": value}})

        repeated = Decimal("NaN") if isinstance(value, Decimal) else type(value)(value)
        out = exc.execute({"cf": {"in": repeated}})

        assert out["cf"]["changed"] is False

    def test_signaling_decimal_nan_can_be_replaced_by_valid_reading(self):
        state = {"cf": {"value": Decimal("sNaN")}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": Decimal("1.5")}})

        assert out["cf"] == {"out": Decimal("1.5"), "changed": True}
        assert state == {"cf": {"value": Decimal("1.5")}}

    def test_ambiguous_aware_datetimes_compare_by_instant(self):
        zone = ZoneInfo("Europe/Zurich")
        first = datetime(2025, 10, 26, 2, 30, tzinfo=zone, fold=0)
        second = datetime(2025, 10, 26, 2, 30, tzinfo=zone, fold=1)
        state = {"cf": {"value": first}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": second}})

        assert first == second  # documents Python's same-ZoneInfo fold behavior
        assert out["cf"] == {"out": second, "changed": True}

    def test_nested_ambiguous_aware_datetimes_compare_by_instant(self):
        zone = ZoneInfo("Europe/Zurich")
        first = datetime(2025, 10, 26, 2, 30, tzinfo=zone, fold=0)
        second = datetime(2025, 10, 26, 2, 30, tzinfo=zone, fold=1)
        state = {"cf": {"value": [first]}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": [second]}})

        assert out["cf"] == {"out": [second], "changed": True}

    @pytest.mark.parametrize("nested", [False, True])
    def test_aware_datetime_utc_overflow_falls_back_safely(self, nested):
        from datetime import datetime as datetime_type
        from datetime import timedelta, timezone

        value = datetime_type.min.replace(tzinfo=timezone(timedelta(hours=14)))
        baseline = [value] if nested else value
        state = {"cf": {"value": baseline}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        current = [value] if nested else value
        out = exc.execute({"cf": {"in": current}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    @pytest.mark.parametrize("nested", [False, True])
    def test_datetime_awareness_probe_failure_falls_back_safely(self, nested):
        class RaisingTimezone(tzinfo):
            def utcoffset(self, _dt):
                raise RuntimeError("offset unavailable")

            def dst(self, _dt):
                return timedelta(0)

        retained = datetime(2025, 1, 1, tzinfo=RaisingTimezone())
        live = retained
        retained_value = [retained] if nested else retained
        live_value = [live] if nested else live
        state = {"cf": {"value": retained_value}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live_value}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    @pytest.mark.parametrize("nested", [False, True])
    def test_datetime_normalization_arbitrary_failure_falls_back_safely(self, nested):
        class StatefulTimezone(tzinfo):
            def __init__(self):
                self.calls = 0

            def utcoffset(self, _dt):
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError("normalization unavailable")
                return timedelta(0)

        retained = datetime(2025, 1, 1, tzinfo=StatefulTimezone())
        live = datetime(2025, 1, 1, tzinfo=StatefulTimezone())
        state = {"cf": {"value": [retained] if nested else retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": [live] if nested else live}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_naive_datetime_fold_transitions_are_changes(self):
        first = datetime(2025, 10, 26, 2, 30, fold=0)  # noqa: DTZ001 - specifically tests naive fold metadata
        second = datetime(2025, 10, 26, 2, 30, fold=1)  # noqa: DTZ001 - specifically tests naive fold metadata
        state = {"cf": {"value": first}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": second}})

        assert first == second
        assert out["cf"] == {"out": second, "changed": True}

    def test_aware_datetime_normalized_equality_has_guarded_truth_conversion(self):
        class UnsafeTruth:
            def __bool__(self):
                raise RuntimeError("truth conversion unavailable")

        class UnsafeEquality:
            def __eq__(self, other):
                return UnsafeTruth()

        class UnsafeNormalizedDatetime(datetime):
            def astimezone(self, tz=None):
                return UnsafeEquality()

        first = UnsafeNormalizedDatetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        second = UnsafeNormalizedDatetime(2026, 1, 1, 11, 0, tzinfo=UTC)
        exc = make_executor([node("cf", "change_filter")], hysteresis_state={"cf": {"value": first}})

        out = exc.execute({"cf": {"in": second}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_nested_naive_datetime_fold_transitions_are_changes(self):
        first = datetime(2025, 10, 26, 2, 30, fold=0)  # noqa: DTZ001 - specifically tests naive fold metadata
        second = datetime(2025, 10, 26, 2, 30, fold=1)  # noqa: DTZ001 - specifically tests naive fold metadata
        state = {"cf": {"value": [first]}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": [second]}})

        assert out["cf"] == {"out": [second], "changed": True}

    def test_time_timezone_and_fold_transitions_are_changes(self):
        from datetime import time as datetime_time

        zurich = ZoneInfo("Europe/Zurich")
        first = datetime_time(2, 30, tzinfo=zurich, fold=0)
        second = datetime_time(2, 30, tzinfo=zurich, fold=1)
        state = {"cf": {"value": first}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        fold_out = exc.execute({"cf": {"in": second}})
        naive_out = exc.execute({"cf": {"in": datetime_time(2, 30)}})

        assert fold_out["cf"]["changed"] is True
        assert naive_out["cf"]["changed"] is True

    def test_equivalent_zoneinfo_time_instances_compare_by_key(self):
        from datetime import time as datetime_time

        cached = datetime_time(10, 30, tzinfo=ZoneInfo("Europe/Zurich"))
        uncached = datetime_time(10, 30, tzinfo=ZoneInfo.no_cache("Europe/Zurich"))
        state = {"cf": {"value": cached}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": uncached}})

        assert cached.tzinfo is not uncached.tzinfo
        assert out["cf"]["changed"] is False

    def test_nested_equivalent_zoneinfo_time_instances_compare_by_key(self):
        from datetime import time as datetime_time

        cached = datetime_time(10, 30, tzinfo=ZoneInfo("Europe/Zurich"))
        uncached = datetime_time(10, 30, tzinfo=ZoneInfo.no_cache("Europe/Zurich"))
        state = {"cf": {"value": [cached]}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": [uncached]}})

        assert out["cf"]["changed"] is False

    @pytest.mark.parametrize("nested", [False, True])
    def test_time_timezone_equality_failure_is_a_safe_change(self, nested):
        from datetime import time as datetime_time

        class RaisingTimezone(tzinfo):
            def utcoffset(self, _dt):
                return timedelta(0)

            def __eq__(self, _other):
                raise RuntimeError("timezone equality unavailable")

        retained = datetime_time(10, 30, tzinfo=RaisingTimezone())
        live = datetime_time(10, 30, tzinfo=RaisingTimezone())
        state = {"cf": {"value": [retained] if nested else retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": [live] if nested else live}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_nested_signaling_decimal_nan_can_be_replaced(self):
        state = {"cf": {"value": [Decimal("sNaN")]}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": [1]}})

        assert out["cf"] == {"out": [1], "changed": True}
        assert state == {"cf": {"value": [1]}}

    def test_ordinary_large_mapping_uses_linear_equality_fast_path(self):
        class Key:
            comparisons = 0

            def __init__(self, value):
                self.value = value

            def __hash__(self):
                return hash(self.value)

            def __eq__(self, other):
                type(self).comparisons += 1
                return isinstance(other, Key) and self.value == other.value

        size = 500
        left = {Key(i): i for i in range(size)}
        right = {Key(i): i for i in reversed(range(size))}

        assert GraphExecutor._nan_aware_equal(left, right) is True
        assert Key.comparisons <= size * 2

    @pytest.mark.parametrize("value", [[float("nan")], {"value": float("nan")}, (float("nan"),), {float("nan")}])
    def test_repeated_nested_nan_value_is_suppressed(self, value):
        state = {}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)
        exc.execute({"cf": {"in": value}})

        if isinstance(value, dict):
            repeated = {"value": float("nan")}
        else:
            repeated = type(value)([float("nan")])
        out = exc.execute({"cf": {"in": repeated}})

        assert out["cf"]["changed"] is False

    def test_differing_value_is_reported_as_changed(self):
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": "foo"}})
        out = exc.execute({"cf": {"in": "bar"}})
        assert out["cf"] == {"out": "bar", "changed": True}

    def test_type_tolerant_equality_does_not_count_as_changed(self):
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": 1}})
        out = exc.execute({"cf": {"in": "1"}})
        assert out["cf"] == {"out": 1, "changed": False}

    def test_state_persists_across_executions(self):
        """Regression: state must survive across separate execute() calls on the same dict."""
        state = {}
        n1 = node("cf", "change_filter")

        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": "foo"}})
        assert "cf" in state

        exc2 = make_executor([n1], hysteresis_state=state)
        out = exc2.execute({"cf": {"in": "foo"}})
        assert out["cf"] == {"out": "foo", "changed": False}

    def test_unwired_input_does_not_report_changed(self):
        """Regression: an unwired 'in' handle must not read as a first value."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({})
        assert out["cf"] == {"out": None, "changed": False}
        assert state == {}

    def test_missing_producer_output_does_not_commit_none(self):
        """A producer error has no requested output port; that is absent
        input, not a genuine ``None`` reading."""
        state = {}
        exc = make_executor(
            [node("producer", "python_script", {"script": "raise RuntimeError('boom')"}), node("cf", "change_filter")],
            [edge("producer", "cf", "result", "in")],
            hysteresis_state=state,
        )

        out = exc.execute()

        assert "__error__" in out["producer"]
        assert out["cf"] == {"out": None, "changed": False}
        assert state == {}

    def test_missing_producer_output_propagates_through_intermediate_node(self):
        state = {"cf": {"value": False}}
        exc = make_executor(
            [
                node("producer", "python_script", {"script": "raise RuntimeError('boom')"}),
                node("invert", "not"),
                node("cf", "change_filter"),
            ],
            [edge("producer", "invert", "result", "in1"), edge("invert", "cf", "out", "in")],
            hysteresis_state=state,
        )

        out = exc.execute()

        assert "__error__" in out["producer"]
        assert "__error__" in out["invert"]
        assert out["cf"] == {"out": False, "changed": False}
        assert state == {"cf": {"value": False}}

    def test_missing_producer_output_stops_at_retained_hysteresis(self):
        state = {"h": False, "cf": {"value": False}}
        nodes = [
            node("producer", "python_script", {"script": "raise RuntimeError('boom')"}),
            node("h", "hysteresis", {"threshold_on": 25.0, "threshold_off": 20.0}),
            node("cf", "change_filter"),
        ]
        edges = [edge("producer", "h", "result", "value"), edge("h", "cf", "out", "in")]

        out = make_executor(nodes, edges, hysteresis_state=state).execute()

        assert "__error__" in out["producer"]
        assert out["h"] == {"out": False}
        assert out["cf"] == {"out": False, "changed": False}
        assert state == {"h": False, "cf": {"value": False}}

        recovered_nodes = [
            node("producer", "python_script", {"script": "result = 10"}),
            node("h", "hysteresis", {"threshold_on": 25.0, "threshold_off": 20.0}),
            node("cf", "change_filter"),
        ]
        recovered = make_executor(recovered_nodes, edges, hysteresis_state=state).execute()

        assert recovered["h"] == {"out": False}
        assert recovered["cf"] == {"out": False, "changed": False}

    @pytest.mark.parametrize(
        ("logic_type", "decisive_value", "expected"),
        [("or", True, True), ("and", False, False)],
    )
    def test_missing_producer_output_is_absorbed_by_decisive_logic(self, logic_type, decisive_value, expected):
        state = {"cf": {"value": expected}}
        exc = make_executor(
            [
                node("producer", "python_script", {"script": "raise RuntimeError('boom')"}),
                node("decisive", "const_value", {"value": str(decisive_value).lower(), "data_type": "bool"}),
                node("logic", logic_type, {"input_count": 2}),
                node("cf", "change_filter"),
            ],
            [
                edge("producer", "logic", "result", "in1"),
                edge("decisive", "logic", "value", "in2"),
                edge("logic", "cf", "out", "in"),
            ],
            hysteresis_state=state,
        )

        out = exc.execute()

        assert out["logic"] == {"out": expected}
        assert out["cf"] == {"out": expected, "changed": False}

    def test_missing_producer_output_is_absorbed_by_unwired_and_input(self):
        state = {"cf": {"value": False}}
        exc = make_executor(
            [
                node("producer", "python_script", {"script": "raise RuntimeError('boom')"}),
                node("logic", "and", {"input_count": 2}),
                node("cf", "change_filter"),
            ],
            [edge("producer", "logic", "result", "in1"), edge("logic", "cf", "out", "in")],
            hysteresis_state=state,
        )

        out = exc.execute()

        assert out["logic"] == {"out": False}
        assert out["cf"] == {"out": False, "changed": False}

    def test_missing_producer_output_is_absorbed_by_closed_gate(self):
        state = {"gate": 42, "cf": {"value": 42}}
        exc = make_executor(
            [
                node("producer", "python_script", {"script": "raise RuntimeError('boom')"}),
                node("disabled", "const_value", {"value": "false", "data_type": "bool"}),
                node("gate", "gate"),
                node("cf", "change_filter"),
            ],
            [
                edge("producer", "gate", "result", "in"),
                edge("disabled", "gate", "value", "enable"),
                edge("gate", "cf", "out", "in"),
            ],
            hysteresis_state=state,
        )

        out = exc.execute()

        assert out["gate"] == {"out": 42}
        assert out["cf"] == {"out": 42, "changed": False}

    @pytest.mark.parametrize("mode", ["raises", "non_scalar"])
    def test_live_value_with_unsafe_equality_replaces_change_filter_baseline(self, mode):
        class UnsafeTruth:
            def __bool__(self):
                raise ValueError("ambiguous truth")

        class UnsafeEquality:
            def __eq__(self, other):
                if mode == "raises":
                    raise RuntimeError("comparison unavailable")
                return UnsafeTruth()

        old = UnsafeEquality()
        new = UnsafeEquality()
        state = {"cf": {"value": old}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": new}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]
        assert isinstance(state["cf"]["value"], UnsafeEquality)

    def test_float_subclass_with_failing_string_conversion_uses_safe_equality(self):
        class FailingStringFloat(float):
            def __str__(self):
                raise RuntimeError("string conversion unavailable")

        exc = make_executor([node("cf", "change_filter")], hysteresis_state={})

        first = exc.execute({"cf": {"in": FailingStringFloat(1.25)}})
        second = exc.execute({"cf": {"in": FailingStringFloat(2.5)}})

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is True
        assert "__error__" not in second["cf"]

    def test_float_subclass_with_failing_integrality_check_uses_safe_decimal_comparison(self):
        class FailingIntegralFloat(float):
            def is_integer(self):
                raise RuntimeError("integrality check unavailable")

        exc = make_executor([node("cf", "change_filter")], hysteresis_state={})

        first = exc.execute({"cf": {"in": FailingIntegralFloat(1.25)}})
        second = exc.execute({"cf": {"in": FailingIntegralFloat(2.5)}})

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is True
        assert "__error__" not in second["cf"]

    @pytest.mark.parametrize("mode", ["raises", "unsafe_truth"])
    def test_decimal_subclass_with_unsafe_nan_classification_uses_safe_comparison(self, mode):
        class UnsafeTruth:
            def __bool__(self):
                raise RuntimeError("truth conversion unavailable")

        class UnsafeNanDecimal(Decimal):
            def is_nan(self):
                if mode == "raises":
                    raise RuntimeError("NaN classification unavailable")
                return UnsafeTruth()

        exc = make_executor([node("cf", "change_filter")], hysteresis_state={})

        first = exc.execute({"cf": {"in": UnsafeNanDecimal("1.25")}})
        second = exc.execute({"cf": {"in": UnsafeNanDecimal("2.5")}})

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is True
        assert "__error__" not in second["cf"]

    @pytest.mark.parametrize("mode", ["raises", "unsafe_truth", "unsafe_equality"])
    def test_decimal_subclass_with_unsafe_finiteness_or_equality_uses_safe_comparison(self, mode):
        class UnsafeTruth:
            def __bool__(self):
                raise RuntimeError("truth conversion unavailable")

        class UnsafeDecimal(Decimal):
            def is_finite(self):
                if mode == "raises":
                    raise RuntimeError("finiteness check unavailable")
                if mode == "unsafe_truth":
                    return UnsafeTruth()
                return super().is_finite()

            def __eq__(self, other):
                if mode == "unsafe_equality":
                    raise RuntimeError("equality unavailable")
                return super().__eq__(other)

        exc = make_executor([node("cf", "change_filter")], hysteresis_state={})

        first = exc.execute({"cf": {"in": UnsafeDecimal("2.25")}})
        second = exc.execute({"cf": {"in": UnsafeDecimal("3.5")}})

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is True
        assert "__error__" not in second["cf"]

    def test_int_subclass_with_failing_conversion_uses_safe_equality(self):
        class UnsafeInt(int):
            def __int__(self):
                raise RuntimeError("integer conversion unavailable")

        exc = make_executor([node("cf", "change_filter")], hysteresis_state={})

        first = exc.execute({"cf": {"in": UnsafeInt(2)}})
        second = exc.execute({"cf": {"in": UnsafeInt(3)}})

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is True
        assert "__error__" not in second["cf"]

    @pytest.mark.parametrize("mode", ["strip", "lower"])
    def test_string_subclass_with_failing_literal_normalization_uses_safe_equality(self, mode):
        class UnsafeString(str):
            def strip(self, *args):
                if mode == "strip":
                    raise RuntimeError("strip unavailable")
                return self

            def lower(self):
                raise RuntimeError("lower unavailable")

        exc = make_executor([node("cf", "change_filter")], hysteresis_state={})

        first = exc.execute({"cf": {"in": UnsafeString("alpha")}})
        second = exc.execute({"cf": {"in": UnsafeString("beta")}})

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is True
        assert "__error__" not in second["cf"]

    def test_container_subclass_with_ambiguous_equality_is_safe(self):
        class UnsafeTruth:
            def __bool__(self):
                raise ValueError("ambiguous truth")

        class AmbiguousList(list):
            def __eq__(self, other):
                return UnsafeTruth()

        state = {"cf": {"value": AmbiguousList([1])}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": AmbiguousList([1])}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_recursive_container_leaf_with_ambiguous_equality_is_safe(self):
        class UnsafeTruth:
            def __bool__(self):
                raise ValueError("ambiguous truth")

        class AmbiguousValue:
            def __eq__(self, other):
                return UnsafeTruth()

        state = {"cf": {"value": [float("nan"), AmbiguousValue()]}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": [float("nan"), AmbiguousValue()]}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_dictionary_candidate_matching_isolates_visited_pairs(self):
        retained = {float("nan"): [2], float("nan"): [1]}
        shared = [1]
        live = {float("nan"): shared, float("nan"): shared}
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True

    def test_scalar_opaque_recovery_handles_failing_string_conversion(self):
        class FailingString:
            def __eq__(self, other):
                return False

            def __str__(self):
                raise RuntimeError("string conversion unavailable")

        state = {
            "cf": {
                "value": _OpaqueRecoveredStr("old opaque value"),
                "_opaque_recovered_str": True,
            }
        }
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": FailingString()}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_opaque_recovery_reports_transition_to_genuine_string(self):
        state = {
            "cf": {
                "value": _OpaqueRecoveredStr("(3+4j)", "builtins.complex"),
                "_opaque_recovered_str": True,
            }
        }
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": "(3+4j)"}})

        assert out["cf"]["changed"] is True
        assert state["cf"] == {"value": "(3+4j)"}

    def test_opaque_recovery_rejects_different_runtime_type_with_same_string(self):
        class SameString:
            def __str__(self):
                return "(3+4j)"

        state = {
            "cf": {
                "value": _OpaqueRecoveredStr("(3+4j)", "builtins.complex"),
                "_opaque_recovered_str": True,
            }
        }
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": SameString()}})

        assert out["cf"]["changed"] is True

    def test_opaque_recovery_treats_same_type_same_string_as_changed_once(self):
        class Reading:
            def __init__(self, value):
                self.value = value

            def __eq__(self, other):
                return isinstance(other, Reading) and self.value == other.value

            def __str__(self):
                return "reading"

        state = {
            "cf": {
                "value": _OpaqueRecoveredStr("reading", f"{Reading.__module__}.{Reading.__qualname__}"),
                "_opaque_recovered_str": True,
            }
        }
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        first = exc.execute({"cf": {"in": Reading(2)}})
        second = exc.execute({"cf": {"in": Reading(2)}})

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is False

    def test_large_recovered_dictionary_matches_without_recursion_error(self):
        live = {index: index for index in range(1100)}
        live[3 + 4j] = "opaque"
        recovered = _OpaqueRecoveredDict([(index, index) for index in range(1100)] + [(_OpaqueRecoveredStr("(3+4j)", "builtins.complex"), "opaque")])
        state = {"cf": {"value": recovered, "_opaque_recovered_str": True}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_large_recovered_set_matches_without_recursion_error(self):
        live = set(range(1100)) | {3 + 4j}
        recovered = _OpaqueRecoveredSet([*range(1100), _OpaqueRecoveredStr("(3+4j)", "builtins.complex")], frozen=False)
        state = {"cf": {"value": recovered, "_opaque_recovered_str": True}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_self_referential_containers_compare_without_recursion_error(self):
        first = []
        first.append(first)
        second = []
        second.append(second)
        state = {}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        exc.execute({"cf": {"in": first}})
        out = exc.execute({"cf": {"in": second}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_deeply_nested_plain_containers_compare_without_recursion_error(self):
        retained: list = []
        live: list = []
        retained_cursor = retained
        live_cursor = live
        for _ in range(1100):
            retained_child: list = []
            live_child: list = []
            retained_cursor.append(retained_child)
            live_cursor.append(live_child)
            retained_cursor = retained_child
            live_cursor = live_child
        retained_cursor.append("leaf")
        live_cursor.append("leaf")
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_deeply_nested_nan_containers_compare_without_recursion_error(self):
        retained: list = []
        live: list = []
        retained_cursor = retained
        live_cursor = live
        for _ in range(1100):
            retained_child: list = []
            live_child: list = []
            retained_cursor.append(retained_child)
            live_cursor.append(live_child)
            retained_cursor = retained_child
            live_cursor = live_child
        retained_cursor.append(float("nan"))
        live_cursor.append(float("nan"))
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_deeply_nested_nan_fallback_preserves_scalar_python_equality(self):
        retained: list = []
        live: list = []
        retained_cursor = retained
        live_cursor = live
        for _ in range(1100):
            retained_child: list = []
            live_child: list = []
            retained_cursor.append(retained_child)
            live_cursor.append(live_child)
            retained_cursor = retained_child
            live_cursor = live_child
        retained_cursor.extend([float("nan"), 1])
        live_cursor.extend([float("nan"), 1.0])
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_deeply_nested_nan_dictionary_keys_compare_equal(self):
        retained: list = []
        live: list = []
        retained_cursor = retained
        live_cursor = live
        for _ in range(1100):
            retained_child: list = []
            live_child: list = []
            retained_cursor.append(retained_child)
            live_cursor.append(live_child)
            retained_cursor = retained_child
            live_cursor = live_child
        retained_cursor.append({float("nan"): 1})
        live_cursor.append({float("nan"): 1})
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_deeply_nested_nan_dictionaries_compare_without_recursion_error(self):
        retained: dict = {"leaf": float("nan")}
        live: dict = {"leaf": float("nan")}
        for _ in range(1100):
            retained = {"nested": retained}
            live = {"nested": live}
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_deeply_nested_ambiguous_nan_dictionary_keys_backtrack(self):
        retained: list = []
        live: list = []
        retained_cursor = retained
        live_cursor = live
        for _ in range(1100):
            retained_child: list = []
            live_child: list = []
            retained_cursor.append(retained_child)
            live_cursor.append(live_child)
            retained_cursor = retained_child
            live_cursor = live_child
        retained_cursor.append({float("nan"): "a", float("nan"): "b"})
        live_cursor.append({float("nan"): "b", float("nan"): "a"})
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_ambiguous_nan_dictionary_cycles_reuse_visited_pairs(self):
        retained = {float("nan"): None, float("nan"): None}
        live = {float("nan"): None, float("nan"): None}
        for key in retained:
            retained[key] = retained
        for key in live:
            live[key] = live
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_deep_plain_dictionary_key_equality_failure_is_a_safe_change(self):
        class RaisingKey:
            def __hash__(self):
                return 1

            def __eq__(self, _other):
                raise RuntimeError("key equality unavailable")

        retained: dict = {RaisingKey(): "leaf"}
        live: dict = {RaisingKey(): "leaf"}
        for _ in range(1100):
            retained = {"nested": retained}
            live = {"nested": live}
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_plain_iterative_fallback_rejects_structural_and_equality_failures(self):
        class RaisingEquality:
            def __hash__(self):
                return 1

            def __eq__(self, _other):
                raise RuntimeError("equality unavailable")

        assert GraphExecutor._plain_container_equal_iterative([], ()) is False
        assert GraphExecutor._plain_container_equal_iterative([1], [1, 2]) is False
        assert GraphExecutor._plain_container_equal_iterative({"left": 1}, {}) is False
        assert GraphExecutor._plain_container_equal_iterative({"left": 1}, {"right": 1}) is False
        assert GraphExecutor._plain_container_equal_iterative({RaisingEquality(): 1}, {RaisingEquality(): 1}) is False
        assert GraphExecutor._plain_container_equal_iterative({1}, {1}) is True
        assert GraphExecutor._plain_container_equal_iterative({1}, {2}) is False
        assert GraphExecutor._plain_container_equal_iterative({RaisingEquality()}, {RaisingEquality()}) is False
        assert GraphExecutor._plain_container_equal_iterative([1], [2]) is False
        assert GraphExecutor._plain_container_equal_iterative([RaisingEquality()], [RaisingEquality()]) is False

        left_cycle: list = []
        right_cycle: list = []
        left_cycle.append(left_cycle)
        right_cycle.append(right_cycle)
        assert GraphExecutor._nan_aware_equal(left_cycle, right_cycle) is True

    def test_nonstandard_iterative_set_matching_branches(self):
        assert GraphExecutor._nonstandard_container_equal_iterative(set(), set()) is True
        assert GraphExecutor._nonstandard_container_equal_iterative({1}, {1}) is True
        assert GraphExecutor._nonstandard_container_equal_iterative({1}, {2}) is False
        assert GraphExecutor._nonstandard_container_equal_iterative({1}, {1, 2}) is False
        assert (
            GraphExecutor._nonstandard_container_equal_iterative(
                {float("nan"), float("nan")},
                {float("nan"), float("nan")},
            )
            is True
        )

    def test_ambiguous_nan_dictionary_mismatch_is_bounded(self):
        retained = {float("nan"): float("nan") for _ in range(10)}
        live = {float("nan"): float("nan") for _ in range(9)}
        live[float("nan")] = 1
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_ambiguous_nan_set_mismatch_is_bounded(self):
        retained = {float("nan") for _ in range(10)}
        live = {float("nan") for _ in range(9)} | {1}
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_ambiguous_recovered_opaque_set_mismatch_is_bounded(self):
        class SameRepresentation:
            def __str__(self):
                return "same"

        type_name = f"{SameRepresentation.__module__}.{SameRepresentation.__qualname__}"
        live = {SameRepresentation() for _ in range(10)}
        recovered = _OpaqueRecoveredSet(
            [_OpaqueRecoveredStr("same", type_name) for _ in range(9)] + [_OpaqueRecoveredStr("different", type_name)],
            frozen=False,
        )

        assert GraphExecutor._opaque_aware_container_equal(live, recovered) is False

    def test_ambiguous_recovered_opaque_dictionary_mismatch_is_bounded(self):
        class SameRepresentation:
            def __str__(self):
                return "same"

        live = {SameRepresentation(): "match" for _ in range(10)}
        recovered = _OpaqueRecoveredDict([("same", "match") for _ in range(9)] + [("same", "different")])

        assert GraphExecutor._opaque_aware_container_equal(live, recovered, allow_unmarked=True) is False

    def test_absent_missing_node_output_stays_unresolved_through_not(self):
        nodes = [node("missing", "missing_node"), node("invert", "not"), node("cf", "change_filter")]
        edges = [edge("missing", "invert", "out", "in1"), edge("invert", "cf", "out", "in")]
        state = {}
        exc = make_executor(nodes, edges, hysteresis_state=state)

        out = exc.execute()

        assert out["cf"] == {"out": None, "changed": False}
        assert "cf" not in state

    @pytest.mark.parametrize(
        "source",
        [
            node("source", "memory", {"initial_value": False, "data_type": "bool"}),
            node("source", "missing_node", {"initial_value": False, "data_type": "bool"}),
        ],
    )
    def test_absent_stale_retained_source_handle_stays_unresolved(self, source):
        nodes = [source, node("invert", "not"), node("cf", "change_filter")]
        edges = [edge("source", "invert", "removed_output", "in1"), edge("invert", "cf", "out", "in")]
        state = {}
        exc = make_executor(nodes, edges, hysteresis_state=state)

        out = exc.execute()

        assert out["cf"] == {"out": None, "changed": False}
        assert "cf" not in state

    def test_time_subclass_with_failing_fold_access_uses_safe_equality(self):
        from datetime import time as datetime_time

        class UnsafeFoldTime(datetime_time):
            @property
            def fold(self):
                raise RuntimeError("fold unavailable")

        exc = make_executor([node("cf", "change_filter")], hysteresis_state={})

        first = exc.execute({"cf": {"in": UnsafeFoldTime(10, 0)}})
        second = exc.execute({"cf": {"in": UnsafeFoldTime(11, 0)}})

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is True
        assert "__error__" not in second["cf"]

    def test_deeply_nested_opaque_recovery_compares_iteratively(self):
        retained: list = []
        live: list = []
        retained_cursor = retained
        live_cursor = live
        for _ in range(1100):
            retained_child: list = []
            live_child: list = []
            retained_cursor.append(retained_child)
            live_cursor.append(live_child)
            retained_cursor = retained_child
            live_cursor = live_child
        retained_cursor.append(_OpaqueRecoveredStr("(3+4j)", "builtins.complex"))
        live_cursor.append(3 + 4j)
        state = {"cf": {"value": retained, "_opaque_recovered_str": True}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})
        repeated = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]
        assert repeated["cf"]["changed"] is False

    def test_cyclic_dictionaries_compare_without_hanging(self):
        retained = {}
        retained["self"] = retained
        live = {}
        live["self"] = live
        state = {"cf": {"value": retained}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_large_integers_compared_without_precision_loss(self):
        """Regression: float round-trip must not equate distinct 64-bit values."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": 9007199254740992}})
        out = exc.execute({"cf": {"in": 9007199254740993}})
        assert out["cf"] == {"out": 9007199254740993, "changed": True}

    def test_dict_key_order_does_not_count_as_changed(self):
        """Regression: structurally equal dicts must not differ by key insertion order."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": {"a": 1, "b": 2}}})
        out = exc.execute({"cf": {"in": {"b": 2, "a": 1}}})
        assert out["cf"] == {"out": {"a": 1, "b": 2}, "changed": False}

    def test_differing_list_is_reported_as_changed(self):
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": [1, 2]}})
        out = exc.execute({"cf": {"in": [1, 3]}})
        assert out["cf"]["changed"] is True

    def test_downstream_mutation_of_out_does_not_corrupt_stored_baseline(self):
        """Regression: a downstream node (a "python_script" is explicitly
        allowed to mutate its inputs in place) receiving a dict/list `out`
        must not be able to corrupt the filter's own comparison baseline —
        `out` on the first-value/changed-value path was previously the
        exact same object stored as state["value"], so mutating one
        mutated the other. A later, genuinely identical input must still
        compare equal against the *original* stored value, not against
        whatever a downstream node mutated it into."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        out1 = exc.execute({"cf": {"in": {"a": 1}}})
        out1["cf"]["out"]["a"] = 999  # simulate a downstream node mutating its input

        out2 = exc.execute({"cf": {"in": {"a": 1}}})

        assert out2["cf"]["changed"] is False
        assert out2["cf"]["out"] == {"a": 1}

    def test_downstream_mutation_of_unchanged_out_does_not_corrupt_stored_baseline(self):
        """Same isolation guarantee as above, for the "unchanged" path: when
        equality holds via the dict/list normalizing path, `out` echoes the
        persisted state["value"] — that must also be an independent copy,
        not the object change_filter keeps comparing against on every
        future tick."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": {"a": 1}}})
        out2 = exc.execute({"cf": {"in": {"a": 1}}})  # equal via dict `==`, via_normalizing_path
        assert out2["cf"]["changed"] is False
        out2["cf"]["out"]["a"] = 999  # simulate a downstream node mutating its input

        out3 = exc.execute({"cf": {"in": {"a": 1}}})

        assert out3["cf"]["changed"] is False
        assert out3["cf"]["out"] == {"a": 1}

    def test_downstream_mutation_of_nested_list_inside_tuple_baseline_does_not_corrupt_state(self):
        """Regression: the isolation copy only deep-copied when the OUTER
        value was itself a dict/list/set — a tuple containing a mutable
        list member, e.g. ([1],), was handed out (and stored) as the exact
        same nested list object, since the outer tuple alone is otherwise
        immutable. Mutating that nested list via the emitted "out" would
        therefore also mutate the persisted baseline, and the next
        genuinely identical ([1],) input would be reported as changed
        against its own already-corrupted stored value."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        out1 = exc.execute({"cf": {"in": ([1],)}})
        out1["cf"]["out"][0].append(999)  # simulate a downstream node mutating the nested list

        out2 = exc.execute({"cf": {"in": ([1],)}})

        assert out2["cf"]["changed"] is False
        assert out2["cf"]["out"] == ([1],)

    def test_numeric_and_boolean_string_aliases_stay_transitive(self):
        """Regression: 1 == "1" and "1" == "true", so 1 must also equal
        "true" — otherwise a source alternating between equivalent adapter
        representations (numeric 1/0 vs boolean strings) emits a redundant
        changed pulse."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": 1}})
        out = exc.execute({"cf": {"in": "true"}})
        assert out["cf"] == {"out": 1, "changed": False}

        exc.execute({"cf": {"in": 0}})
        out = exc.execute({"cf": {"in": "false"}})
        assert out["cf"]["changed"] is False

    def test_decimal_numeric_string_and_boolean_aliases_stay_transitive(self):
        state = {"cf": {"value": "1.0"}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        assert exc.execute({"cf": {"in": 1}})["cf"]["changed"] is False
        assert exc.execute({"cf": {"in": "true"}})["cf"]["changed"] is False

    def test_decimal_boolean_aliases_stay_transitive(self):
        state = {}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)
        exc.execute({"cf": {"in": Decimal(1)}})

        assert exc.execute({"cf": {"in": "true"}})["cf"]["changed"] is False

        exc.execute({"cf": {"in": Decimal(0)}})
        assert exc.execute({"cf": {"in": "false"}})["cf"]["changed"] is False

    def test_numeric_subclass_with_raising_equality_does_not_error(self):
        class RaisingEqualityInt(int):
            def __eq__(self, _other):
                raise RuntimeError("unsafe equality")

        state = {}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)
        exc.execute({"cf": {"in": RaisingEqualityInt(1)}})

        out = exc.execute({"cf": {"in": RaisingEqualityInt(1)}})

        assert out["cf"]["changed"] is False
        assert "__error__" not in out["cf"]

    def test_numeric_subclass_with_nonscalar_equality_is_not_a_bool_literal(self):
        class NonScalarEqualityFloat(float):
            def __eq__(self, _other):
                return [True, False]

        assert GraphExecutor._try_bool_literal(NonScalarEqualityFloat(1.0)) is None

    def test_large_integer_not_equated_to_rounded_float(self):
        """Regression: 9007199254740993 (2**53 + 1) cannot be represented
        exactly as a float — round-tripping it through float() rounds it
        down to 9007199254740992.0, which must not falsely equal an actual
        float carrying that rounded value (a real change in a 64-bit
        counter/ID must not be suppressed just because one side is already
        a float)."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": 9007199254740993}})
        out = exc.execute({"cf": {"in": 9007199254740992.0}})
        assert out["cf"]["changed"] is True

    def test_non_integral_float_falls_back_to_numeric_comparison(self):
        """Regression: a float with a fractional part is not an "exact int"
        candidate on either side — it must still compare via the ordinary
        numeric path instead of being silently dropped by the exact-integer
        branch just because it's a float."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": 5.5}})
        out = exc.execute({"cf": {"in": 5.5}})
        assert out["cf"] == {"out": 5.5, "changed": False}

    def test_infinity_string_does_not_crash_exact_int_comparison(self):
        """Regression: "Infinity"/"-Infinity" are valid Decimal literals that
        pass the to_integral_value() equality check (Infinity == Infinity),
        so they reach int(Decimal(...)), which raises OverflowError. That
        must be caught and treated as "not an exact int", not propagate as
        an uncaught node execution error."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": "Infinity"}})
        out = exc.execute({"cf": {"in": "-Infinity"}})
        assert out["cf"]["changed"] is True
        assert out["cf"]["out"] == "-Infinity"

    def test_huge_scientific_exponent_does_not_materialize_giant_int(self):
        """Regression: a compact literal like "1e10000000000" parses as a
        Decimal instantly (stored as coefficient+exponent, not expanded),
        but int() on it would try to materialize an actual multi-gigabyte
        integer — a single adapter-supplied string could exhaust memory/CPU.
        Must be rejected as "not an exact int" before ever calling int()
        (the comparison then falls through to the exact-decimal path, which
        also never materializes the giant integer — Decimal compares by
        coefficient+exponent — so evaluation stays fast, but unlike the old
        float()-based fallback it now also gets the *correct*, distinct
        result instead of both operands coincidentally overflowing to the
        same inf)."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        start = time.monotonic()
        exc.execute({"cf": {"in": "1e10000000000"}})
        out = exc.execute({"cf": {"in": "2e10000000000"}})
        elapsed = time.monotonic() - start
        assert elapsed < 2.0
        assert out["cf"]["changed"] is True

    def test_huge_scientific_exponent_compares_as_different_from_finite_value(self):
        """A huge scientific-notation string must not be treated as an exact
        int, but must still compare as genuinely different from an ordinary
        finite value (via the numeric-overflow-to-inf fallback), not get
        stuck or silently equated to it."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": 42}})
        out = exc.execute({"cf": {"in": "1e10000000000"}})
        assert out["cf"]["changed"] is True

    def test_scientific_notation_string_compared_as_exact_int(self):
        """Regression: "1e2" isn't parseable by int() directly but is an
        exact whole number via Decimal — it must compare equal to the
        equivalent plain int, not fall back to a redundant str/float path."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": 100}})
        out = exc.execute({"cf": {"in": "1e2"}})
        assert out["cf"]["changed"] is False

    def test_bool_treated_as_one_or_zero_against_a_non_integral_number(self):
        """Regression: True/False must alias to 1/0 in the decimal-numeric
        comparison path too (not just the earlier boolean-literal path,
        which only applies when *both* sides parse as a boolean literal —
        1.5 does not), so a change from True to a genuinely different
        non-integral number (1.5) is still reported as changed, and from
        True to the equivalent 1.0 is not."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": True}})
        out = exc.execute({"cf": {"in": 1.5}})
        assert out["cf"]["changed"] is True

        exc.execute({"cf": {"in": True}})
        out = exc.execute({"cf": {"in": 1.0}})
        assert out["cf"]["changed"] is False

    def test_high_precision_decimal_strings_compared_exactly(self):
        """Regression: two distinct high-precision decimal strings must not
        collapse onto the same rounded binary float — an adapter supplying
        "0.123456789012345678901" and, later, the neighbouring
        "0.123456789012345678902" must be reported as a real change."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": "0.123456789012345678901"}})
        out = exc.execute({"cf": {"in": "0.123456789012345678902"}})
        assert out["cf"]["changed"] is True

    def test_decimal_value_compares_exactly_to_decimal_string(self):
        state = {"cf": {"value": "0.123456789012345678901"}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": Decimal("0.123456789012345678901")}})

        assert out["cf"]["changed"] is False

    def test_non_integral_decimal_string_does_not_short_circuit_exact_int(self):
        """Regression: "1.5" parses as a valid Decimal but isn't a whole
        number — it must not be treated as an exact-int candidate, so a
        genuinely different value ("1.6") is still reported as changed."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": "1.5"}})
        out = exc.execute({"cf": {"in": "1.6"}})
        assert out["cf"]["changed"] is True

    def test_equal_via_str_fallback_emits_current_input_not_persisted_value(self):
        """Regression: after a restart, a *legacy* persisted non-JSON-native
        value (e.g. a KNX DPT10/11 datetime.time/date saved before tagged
        persistence existed) is stored as a lossy string via the old
        `default=str`. LogicManager._load_graphs marks such a restored
        string with "_recovered_str" so the change_filter case can safely
        recognize it. When the next real value round-trips to the same
        string via that recovery path, the "unchanged" branch must emit the
        current (typed) input, not the persisted string — otherwise `out`'s
        type silently degrades after every restart even though nothing
        about the underlying value changed."""
        from datetime import time

        state = {"cf": {"value": "10:30:00", "_recovered_str": True}}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"cf": {"in": time(10, 30, 0)}})
        assert out["cf"]["changed"] is False
        assert out["cf"]["out"] == time(10, 30, 0)
        assert isinstance(out["cf"]["out"], time)

    def test_legacy_date_recovery_with_failing_string_conversion_is_a_safe_change(self):
        from datetime import date

        class UnsafeStringDate(date):
            def __str__(self):
                raise RuntimeError("string conversion unavailable")

        state = {"cf": {"value": "2026-01-01", "_recovered_str": True}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": UnsafeStringDate(2026, 1, 2)}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_mapping_with_failing_items_traversal_is_a_safe_change(self):
        class UnsafeItemsDict(dict):
            def items(self):
                raise RuntimeError("mapping traversal unavailable")

        state = {"cf": {"value": {"value": 1}}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": UnsafeItemsDict(value=2)}})

        assert out["cf"]["changed"] is True
        assert "__error__" not in out["cf"]

    def test_equal_via_str_fallback_migrates_persisted_state_to_typed_value(self):
        """Regression: matching via the legacy str()-recovery path emitted
        the correct typed "out" (test above) but left the persisted state
        itself as the unmigrated legacy string with "_recovered_str" still
        set. _execute_graph then persists that unchanged string inside the
        new version-2 envelope; on the *next* restart the loader treats a
        version-2-envelope string as already-native (no "_recovered_str"
        added — see LogicManager._load_graphs), so the same live time would
        compare unequal to it and report a spurious changed=True forever.
        The state must be migrated to the typed value (marker cleared) as
        soon as the recovery path is used, not left for a future tick."""
        from datetime import time

        state = {"cf": {"value": "10:30:00", "_recovered_str": True}}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": time(10, 30, 0)}})

        assert state["cf"] == {"value": time(10, 30, 0)}

    def test_recovered_str_marker_is_cleared_after_a_live_string_match(self):
        """Regression: when a LIVE value confirms the persisted, restart-
        recovered string via ORDINARY equality (e.g. a source that
        genuinely emits the literal string "10:30:00" itself, not a
        datetime.time), the "unchanged" branch previously left
        "_recovered_str" set on the persisted state. A later GENUINE type
        transition to datetime.time(10, 30) would then still take the
        str()-recovery fallback (since the marker survived) and incorrectly
        report changed=False, instead of the real change it is. Confirming
        the string live resolves the marker's ambiguity — this source
        evidently emits plain strings — so it must be cleared then, not
        left to swallow a later real transition forever."""
        from datetime import time

        state = {"cf": {"value": "10:30:00", "_recovered_str": True}}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)

        out1 = exc.execute({"cf": {"in": "10:30:00"}})
        assert out1["cf"]["changed"] is False
        assert state["cf"] == {"value": "10:30:00"}

        out2 = exc.execute({"cf": {"in": time(10, 30, 0)}})
        assert out2["cf"]["changed"] is True

    def test_opaque_recovery_matches_nested_dict_baseline(self):
        """The opaque-str recovery fallback must recurse into a dict-shaped
        baseline too, not just a list — a python_script result like
        {"a": 3 + 4j} persists with the complex number opaque-tagged at
        that nested key."""
        state = {"cf": {"value": {"a": "(3+4j)"}, "_opaque_recovered_str": True}}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)

        out = exc.execute({"cf": {"in": {"a": 3 + 4j}}})
        assert out["cf"]["changed"] is False

    def test_opaque_recovery_matches_a_mix_of_recovered_and_ordinary_leaves(self):
        """A container can hold both a genuinely opaque-recovered leaf
        (needing the str() fallback) and an ordinary already-identical
        leaf (matching via plain equality) side by side — both must be
        recognized as unchanged together."""
        state = {"cf": {"value": ["(3+4j)", "unchanged"], "_opaque_recovered_str": True}}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)

        out = exc.execute({"cf": {"in": [3 + 4j, "unchanged"]}})
        assert out["cf"]["changed"] is False

    @pytest.mark.parametrize(
        ("persisted", "live"),
        [
            ((_OpaqueRecoveredStr("(3+4j)"), "unchanged"), (3 + 4j, "unchanged")),
            ({_OpaqueRecoveredStr("(3+4j)"), "unchanged"}, {3 + 4j, "unchanged"}),
            (frozenset({_OpaqueRecoveredStr("(3+4j)"), "unchanged"}), frozenset({3 + 4j, "unchanged"})),
        ],
    )
    def test_opaque_recovery_recurses_through_tuple_and_set_containers(self, persisted, live):
        state = {"cf": {"value": persisted, "_opaque_recovered_str": True}}
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})
        repeated = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True
        assert repeated["cf"]["changed"] is False

    def test_opaque_recovery_handles_a_live_value_with_raising_equality(self):
        class RaisingEquality:
            def __eq__(self, other):
                raise RuntimeError("comparison unavailable")

            def __str__(self):
                return "opaque-value"

        live = RaisingEquality()
        state = {
            "cf": {
                "value": _OpaqueRecoveredStr("opaque-value"),
                "_opaque_recovered_str": True,
            }
        }
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": live}})

        assert out["cf"]["changed"] is True
        assert isinstance(out["cf"]["out"], RaisingEquality)

    def test_opaque_recovery_fallback_is_limited_to_tagged_leaf_positions(self):
        state = {
            "cf": {
                "value": [_OpaqueRecoveredStr("(3+4j)"), "2"],
                "_opaque_recovered_str": True,
            }
        }
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": [3 + 4j, 2]}})

        assert out["cf"]["changed"] is True

    def test_live_string_container_reports_transition_from_opaque_leaf(self):
        state = {
            "cf": {
                "value": [_OpaqueRecoveredStr("(3+4j)")],
                "_opaque_recovered_str": True,
            }
        }
        exc = make_executor([node("cf", "change_filter")], hysteresis_state=state)

        out = exc.execute({"cf": {"in": ["(3+4j)"]}})

        assert out["cf"] == {"out": ["(3+4j)"], "changed": True}
        assert state == {"cf": {"value": ["(3+4j)"]}}
        assert type(state["cf"]["value"][0]) is str

    def test_opaque_recovery_matches_a_recovered_dict_key(self):
        """Regression: a non-string dict KEY (e.g. 3+4j in {3+4j: "x"})
        persists the same lossy opaque-str way a leaf VALUE does — decoded
        back as the plain string key "(3+4j)". Plain `left.keys() ==
        right.keys()` set equality never matches the live complex key
        against that recovered string key, short-circuiting the whole dict
        to "changed" before the per-value opaque comparison ever runs."""
        state = {"cf": {"value": {"(3+4j)": "x"}, "_opaque_recovered_str": True}}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)

        out = exc.execute({"cf": {"in": {3 + 4j: "x"}}})
        assert out["cf"]["changed"] is False

    def test_opaque_dict_key_recovery_rejects_different_length_dicts(self):
        """Direct unit test for _opaque_aware_container_equal's dict branch:
        a different key count can never match, regardless of any opaque
        key/value recovery."""
        assert GraphExecutor._opaque_aware_container_equal({3 + 4j: "x"}, {"(3+4j)": "x", "extra": "y"}) is False

    def test_opaque_dict_key_recovery_rejects_an_unmatched_key(self):
        """Direct unit test: same key count, but no right-side key —
        neither an exact match nor an opaque str(left)-recovered match —
        corresponds to a given left key."""
        assert GraphExecutor._opaque_aware_container_equal({3 + 4j: "x"}, {"totally-unrelated": "x"}) is False

    def test_opaque_dict_key_recovery_rejects_a_matched_key_with_different_value(self):
        """Direct unit test: the key recovers/matches, but the associated
        value differs — the dict as a whole must still report unequal."""
        assert GraphExecutor._opaque_aware_container_equal({3 + 4j: "x"}, {"(3+4j)": "y"}) is False

    def test_live_string_matching_a_temporal_repr_is_not_treated_as_recovered(self):
        """Regression: a source that legitimately emits the literal string
        "10:30:00" *live*, this session — never round-tripped through DB
        persistence — must not have a later, genuinely different
        datetime.time(10, 30) value swallowed by the persisted-string
        recovery path. Only a value LogicManager._load_graphs actually
        flagged as DB-recovered (via "_recovered_str") may use that
        recovery; without the flag this is a real type transition."""
        from datetime import time

        state = {"cf": {"value": "10:30:00"}}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"cf": {"in": time(10, 30, 0)}})
        assert out["cf"]["changed"] is True
        assert out["cf"]["out"] == time(10, 30, 0)

    def test_list_is_not_equal_to_its_own_string_representation(self):
        """Regression: a transition from a list/dict to a string that happens
        to match its str() repr (e.g. [1, 2] -> "[1, 2]") is a genuine type
        change, not a persisted-value recovery — it must report changed=True,
        not be swallowed by a blanket str(left) == str(right) fallback."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        exc.execute({"cf": {"in": [1, 2]}})
        out = exc.execute({"cf": {"in": "[1, 2]"}})
        assert out["cf"]["changed"] is True

        exc.execute({"cf": {"in": {"a": 1}}})
        out = exc.execute({"cf": {"in": "{'a': 1}"}})
        assert out["cf"]["changed"] is True

    def test_non_deepcopyable_value_does_not_error_the_node(self):
        """Regression: a permitted python_script legitimately returning a
        generator (or any other value with a failing __deepcopy__/__reduce__
        hook) previously raised out of copy.deepcopy() while snapshotting
        the comparison baseline, turning the node's whole output into
        {"__error__": ...} instead of emitting the value with changed=True."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)

        out = exc.execute({"cf": {"in": (x for x in [1, 2, 3])}})

        assert "__error__" not in out["cf"]
        assert out["cf"]["changed"] is True

    def test_non_deepcopyable_value_baseline_does_not_change_type(self):
        """Regression: after the fix above stopped the crash, the fallback
        used to snapshot the baseline as a lossy str() (_snapshot_debug_value,
        meant for pure debug capture), permanently changing the comparison
        baseline's type. If the same source (e.g. Memory) repeatedly emits
        the same non-deep-copyable object — one generator instance — every
        pass, the next pass compared that live generator against the
        unmarked string stand-in, never equal, reporting changed=True again
        on every unrelated execution. The baseline must instead fall back to
        the ORIGINAL reference (_replay_known_output_value's behavior), so
        the same object compared against itself is recognized as unchanged."""
        state = {}
        n1 = node("cf", "change_filter")
        exc = make_executor([n1], hysteresis_state=state)
        same_generator = (x for x in [1, 2, 3])

        first = exc.execute({"cf": {"in": same_generator}})
        second = exc.execute({"cf": {"in": same_generator}})

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is False


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
# astro_sun node
# ===========================================================================


class TestAstroSunNode:
    def test_success_returns_sunrise_sunset_and_is_day(self):
        pytest.importorskip("astral", reason="astral not installed")
        out = run_single("astro_sun", {"latitude": 47.37, "longitude": 8.54})
        assert isinstance(out["sunrise"], str)
        assert isinstance(out["sunset"], str)
        assert isinstance(out["is_day"], bool)

    def test_computation_error_returns_none_values(self):
        """An invalid config value (e.g. a non-numeric latitude) must not
        blow up graph execution — the error is logged and defaults returned."""
        out = run_single("astro_sun", {"latitude": "not-a-number", "longitude": 8.54})
        assert out == {"sunrise": None, "sunset": None, "is_day": False}


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

    def test_write_formula_error_returns_original(self):
        # Formula error must not propagate — original value preserved
        out = run_single("datapoint_write", {"value_formula": "1 / 0"}, {"value": 5.0})
        assert out["_write_value"] == 5.0

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

    def test_read_value_map_string_lookup_is_case_insensitive(self):
        m = {"on": "true", "off": "false"}
        out = run_single("datapoint_read", {"value_map": m}, {"value": "OFF"})
        assert out["value"] == "false"

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

    # ── bool inputs with numeric value_map (issue #287) ─────────────────────

    def test_read_bool_true_with_numeric_map(self):
        # KNX DPT1.x decodes to Python bool; num_invert preset must apply
        m = {"0": "1", "1": "0"}
        out = run_single("datapoint_read", {"value_map": m}, {"value": True})
        assert out["value"] == "0"

    def test_read_bool_false_with_numeric_map(self):
        m = {"0": "1", "1": "0"}
        out = run_single("datapoint_read", {"value_map": m}, {"value": False})
        assert out["value"] == "1"

    def test_write_bool_true_with_numeric_map(self):
        m = {"0": "1", "1": "0"}
        out = run_single("datapoint_write", {"value_map": m}, {"value": True})
        assert out["_write_value"] == "0"

    def test_write_bool_false_with_numeric_map(self):
        m = {"0": "1", "1": "0"}
        out = run_single("datapoint_write", {"value_map": m}, {"value": False})
        assert out["_write_value"] == "1"


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

    def test_script_error_sets_error_key(self):
        # execute() catches all errors internally and logs them — never raises to caller;
        # the failing node gets {"__error__": "<msg>"} so callers can surface the failure.
        n1 = node("p", "python_script", {"script": "result = 1 / 0"})
        exc = make_executor([n1])
        out = exc.execute({"p": {}})
        assert "__error__" in out.get("p", {})
        assert isinstance(out["p"]["__error__"], str)
        assert out["p"]["__error__"]  # non-empty message

    def test_os_import_blocked_sets_error_key(self):
        # __import__ is not in builtins → ExecutionError caught internally → __error__ set
        n1 = node("p", "python_script", {"script": "import os; result = os.getcwd()"})
        exc = make_executor([n1])
        out = exc.execute({"p": {}})
        assert "__error__" in out.get("p", {})

    def test_round_uses_mathematical_rounding(self):
        out = run_single("python_script", {"script": "result = round(inputs['a'], 1)"}, {"a": 21.15})
        assert out["result"] == pytest.approx(21.2)

    def test_math_dunder_attribute_blocked_sets_error_key(self):
        n1 = node("p", "python_script", {"script": "result = math.__dict__"})
        exc = make_executor([n1])
        out = exc.execute({"p": {}})
        assert "__error__" in out.get("p", {})

    def test_error_node_downstream_receives_none(self):
        # A node downstream of a failing node gets None as input (not the error dict).
        nodes = [
            node("bad", "python_script", {"script": "result = 1 / 0"}),
            node("good", "math_formula", {"formula": "a + 1"}),
        ]
        edges = [edge("bad", "good", source_handle="result", target_handle="in1")]
        exc = make_executor(nodes, edges)
        out = exc.execute()
        assert "__error__" in out["bad"]
        # in1 resolves to None (handle "result" absent from error dict) → 0.0 + 1 = 1.0
        assert out["good"]["result"] == pytest.approx(1.0)

    def test_graph_continues_after_node_error(self):
        # When one node fails, the rest of the graph still executes.
        nodes = [
            node("ok", "const_value", {"value": "5", "data_type": "number"}),
            node("bad", "python_script", {"script": "result = 1 / 0"}),
            node("out", "math_formula", {"formula": "a"}),
        ]
        edges = [edge("ok", "out", source_handle="value", target_handle="in1")]
        exc = make_executor(nodes, edges)
        out = exc.execute()
        assert "__error__" in out["bad"]
        assert out["out"]["result"] == pytest.approx(5.0)


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

    def test_cycle_nodes_are_reported_instead_of_dropped(self, caplog):
        nodes = [
            node("a", "not", {}),
            node("b", "not", {}),
        ]
        edges = [
            edge("a", "b", source_handle="out", target_handle="in1"),
            edge("b", "a", source_handle="out", target_handle="in1"),
        ]
        exc = make_executor(nodes, edges)

        out = exc.execute()

        assert set(out) == {"a", "b"}
        assert out["a"]["__diagnostic__"] == "graph_cycle"
        assert out["b"]["__diagnostic__"] == "graph_cycle"
        assert set(out["a"]["__cycle_nodes__"]) == {"a", "b"}
        assert "Logic graph contains cycle" in caplog.text

    def test_cycle_keeps_acyclic_branch_and_marks_blocked_descendants(self):
        nodes = [
            node("root", "const_value", {"value": "7", "data_type": "number"}),
            node("ok", "math_formula", {"formula": "a + 1"}),
            node("a", "not", {}),
            node("b", "not", {}),
            node("blocked", "math_formula", {"formula": "a + 1"}),
        ]
        edges = [
            edge("root", "ok", source_handle="value", target_handle="in1"),
            edge("a", "b", source_handle="out", target_handle="in1"),
            edge("b", "a", source_handle="out", target_handle="in1"),
            edge("b", "blocked", source_handle="out", target_handle="in1"),
        ]
        exc = make_executor(nodes, edges)

        out = exc.execute()

        assert out["ok"]["result"] == pytest.approx(8.0)
        assert out["a"]["__diagnostic__"] == "graph_cycle"
        assert out["b"]["__diagnostic__"] == "graph_cycle"
        assert out["blocked"]["__diagnostic__"] == "graph_cycle_blocked"

    def test_memory_outputs_previous_value_and_commits_current_input_after_run(self):
        nodes = [
            node("src", "const_value", {"value": "10", "data_type": "number"}),
            node("mem", "memory", {"initial_value": "2", "data_type": "number"}),
        ]
        edges = [edge("src", "mem", source_handle="value", target_handle="in")]
        state = {}
        exc = make_executor(nodes, edges, hysteresis_state=state)

        first = exc.execute()
        second = exc.execute()

        assert first["mem"]["out"] == pytest.approx(2.0)
        assert second["mem"]["out"] == pytest.approx(10.0)
        assert state["mem"]["value"] == pytest.approx(10.0)

    def test_memory_commit_can_be_deferred_and_applied_later(self):
        nodes = [
            node("src", "const_value", {"value": "10", "data_type": "number"}),
            node("mem", "memory", {"initial_value": "2", "data_type": "number"}),
        ]
        edges = [edge("src", "mem", source_handle="value", target_handle="in")]
        state = {}
        exc = make_executor(nodes, edges, hysteresis_state=state)

        out = exc.execute(commit_memory=False)

        assert out["mem"]["out"] == pytest.approx(2.0)
        assert state == {}

        exc.commit_memory_inputs(out)
        assert state["mem"]["value"] == pytest.approx(10.0)

    def test_memory_honors_wired_reset_before_committing_input(self):
        nodes = [
            node("src", "const_value", {"value": "10", "data_type": "number"}),
            node("rst", "const_value", {"value": "true", "data_type": "bool"}),
            node("mem", "memory", {"initial_value": "2", "data_type": "number"}),
        ]
        edges = [
            edge("src", "mem", source_handle="value", target_handle="in"),
            edge("rst", "mem", source_handle="value", target_handle="reset"),
        ]
        state = {"mem": {"value": 7.0}}
        exc = make_executor(nodes, edges, hysteresis_state=state)

        out = exc.execute()

        assert out["mem"]["out"] == pytest.approx(7.0)
        assert state["mem"]["value"] == pytest.approx(2.0)

    def test_memory_does_not_commit_diagnostic_source_value(self):
        nodes = [
            node("a", "not", {}),
            node("b", "not", {}),
            node("mem", "memory", {"initial_value": "2", "data_type": "number"}),
        ]
        edges = [
            edge("a", "b", source_handle="out", target_handle="in1"),
            edge("b", "a", source_handle="out", target_handle="in1"),
            edge("a", "mem", source_handle="out", target_handle="in"),
        ]
        state = {"mem": {"value": 7.0}}
        exc = make_executor(nodes, edges, hysteresis_state=state)

        out = exc.execute()

        assert out["a"]["__diagnostic__"] == "graph_cycle"
        assert out["mem"]["out"] == pytest.approx(7.0)
        assert state["mem"]["value"] == pytest.approx(7.0)

    def test_memory_breaks_feedback_cycle_by_one_run(self):
        nodes = [
            node("mem", "memory", {"initial_value": "false", "data_type": "bool"}),
            node("not", "not", {}),
        ]
        edges = [
            edge("mem", "not", source_handle="out", target_handle="in1"),
            edge("not", "mem", source_handle="out", target_handle="in"),
        ]
        exc = make_executor(nodes, edges, hysteresis_state={})

        first = exc.execute()
        second = exc.execute()

        assert "__diagnostic__" not in first["mem"]
        assert "__diagnostic__" not in first["not"]
        assert first["mem"]["out"] is False
        assert first["not"]["out"] is True
        assert second["mem"]["out"] is True
        assert second["not"]["out"] is False


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
    """Tests for the heating_circuit node (Sommer/Winter DIN, Mannheimer Methode).

    Single 'value' input.  Slot assignment uses fixed time points:
      T1 = first measurement at hour >= 7
      T2 = first measurement at hour >= 14
      T3 = first measurement at hour >= 21
    Each slot is filled ONCE per day using first-crossing semantics:
    the value captured is the one already on the bus (last_value) when
    the threshold is crossed, not the triggering measurement.

    Test overrides injected via the inputs dict:
      _slot        – bypass time logic and force a specific slot (t1/t2/t3)
      _hour        – override wall-clock hour for time-based slot assignment
      _date        – override wall-clock date (ISO string) for multi-day scenarios
      _history_t1/t2/t3 – simulate manager history pre-fill for missing slots

    Hysteresis: ON when daily_avg < threshold_temp, OFF when daily_avg >= threshold_temp + hysteresis.
    Without historical data the first measurement initialises heating_mode
    directly: < threshold_temp → ON, >= threshold_temp → OFF.
    Slots are NOT reset after daily_avg computation — debug ports always show
    the last captured values.
    """

    # Default: heating ON below 14 °C, OFF at or above 16 °C (14 + 2 hysteresis)
    _CFG: ClassVar[dict[str, float]] = {"threshold_temp": 14.0, "hysteresis": 2.0}

    @staticmethod
    def _d(day: int) -> str:
        """ISO date string for day N (0 = 2025-01-01), used to simulate multiple days."""
        from datetime import date, timedelta

        return (date(2025, 1, 1) + timedelta(days=day)).isoformat()

    def _run_slot(self, slot, value, config=None, state=None, date="2025-01-01"):
        """Feed a single temperature reading at the given time slot."""
        if state is None:
            state = {}
        if config is None:
            config = self._CFG
        n1 = node("h", "heating_circuit", config)
        exc = make_executor([n1], hysteresis_state=state)
        return exc.execute({"h": {"value": value, "_slot": slot, "_date": date}})["h"], state

    def _run_full_day(self, t1, t2, t3, config=None, state=None, date="2025-01-01"):
        """Simulate a full day (all three slots) and return the final output."""
        if state is None:
            state = {}
        if config is None:
            config = self._CFG
        n1 = node("h", "heating_circuit", config)
        out = None
        for slot, val in [("t1", t1), ("t2", t2), ("t3", t3)]:
            exc = make_executor([n1], hysteresis_state=state)
            out = exc.execute({"h": {"value": val, "_slot": slot, "_date": date}})["h"]
        return out, state

    def _run_at_hour(self, hour, value, config=None, state=None, date="2025-01-01"):
        """Feed a temperature with a specific hour override (tests time-based slot logic)."""
        if state is None:
            state = {}
        if config is None:
            config = self._CFG
        n1 = node("h", "heating_circuit", config)
        exc = make_executor([n1], hysteresis_state=state)
        return exc.execute({"h": {"value": value, "_hour": hour, "_date": date}})["h"], state

    # ── App-Timezone Fallback ────────────────────────────────────────────────

    def test_invalid_app_timezone_falls_back_to_utc(self):
        """An invalid app_config timezone must not blow up the node — it
        falls back to UTC and execution proceeds normally."""
        n1 = node("h", "heating_circuit", self._CFG)
        exc = make_executor([n1], app_config={"timezone": "not-a-real-timezone"})
        out = exc.execute({"h": {"value": 10.0, "_slot": "t1", "_date": "2025-01-01"}})["h"]
        assert out["t1"] == pytest.approx(10.0)

    # ── DIN-Formel ────────────────────────────────────────────────────────────

    def test_din_formula_daily_avg(self):
        # T_avg = (10 + 12 + 2*8) / 4 = 38/4 = 9.5
        out, _ = self._run_full_day(10, 12, 8)
        assert out["daily_avg"] == pytest.approx(9.5)

    def test_heating_on_below_threshold(self):
        # daily_avg = (5+6+2*4)/4 = 4.75 < threshold=14 → ON
        out, _ = self._run_full_day(5, 6, 4)
        assert out["heating_mode"] == 1

    def test_heating_off_above_threshold_plus_hysteresis(self):
        # daily_avg = (22+24+2*22)/4 = 22.5 >= threshold+hysteresis=16 → OFF
        out, _ = self._run_full_day(22, 24, 22)
        assert out["heating_mode"] == 0

    # ── Hysterese ─────────────────────────────────────────────────────────────

    def test_hysteresis_stays_on_between_thresholds(self):
        """Once ON, heating stays ON when daily_avg is between threshold and threshold+hysteresis."""
        state = {}
        self._run_full_day(5, 6, 4, state=state, date=self._d(0))  # daily_avg=4.75 < 14 → ON
        # Mild day: daily_avg = (14+15+2*14.5)/4 = 14.5 → between 14 and 16 → stays ON
        out, _ = self._run_full_day(14, 15, 14.5, state=state, date=self._d(1))
        assert out["heating_mode"] == 1

    def test_hysteresis_stays_off_between_thresholds(self):
        """Once OFF, heating stays OFF when daily_avg is between thresholds."""
        state = {}
        self._run_full_day(22, 24, 22, state=state, date=self._d(0))  # daily_avg=22.5 >= 16 → OFF
        # Mild day: daily_avg = 14.5 → between 14 and 16 → stays OFF
        out, _ = self._run_full_day(14, 15, 14.5, state=state, date=self._d(1))
        assert out["heating_mode"] == 0

    def test_hysteresis_turns_on_below_threshold(self):
        """Heating turns ON once daily_avg drops below threshold_temp."""
        state = {}
        self._run_full_day(22, 24, 22, state=state, date=self._d(0))  # warm → OFF
        out, _ = self._run_full_day(5, 6, 4, state=state, date=self._d(1))  # cold → ON
        assert out["heating_mode"] == 1

    def test_hysteresis_turns_off_above_threshold_plus_hysteresis(self):
        """Heating turns OFF once daily_avg reaches or exceeds threshold+hysteresis."""
        state = {}
        self._run_full_day(5, 6, 4, state=state, date=self._d(0))  # cold → ON
        out, _ = self._run_full_day(22, 24, 22, state=state, date=self._d(1))  # warm → OFF
        assert out["heating_mode"] == 0

    # ── Anliegender Wert / exakte Zeitpunkte ─────────────────────────────────

    def test_value_at_0600_updates_last_value_only(self):
        """A measurement before 07:00 updates last_value but fills no slot."""
        state = {}
        out, _ = self._run_at_hour(6, 10.0, state=state)
        assert out["t1"] is None
        assert out["t2"] is None
        assert out["t3"] is None

    def test_t1_uses_last_value_at_0700_boundary(self):
        """T1 = the value that was PRESENT at 07:00 (last_value), not the
        triggering measurement.

        Sensor sends at 06:55 (5.0 °C) and again at 07:05 (5.5 °C).
        At 07:05 the block sees hour==7 for the first time → T1 = 5.0 (the
        06:55 value that was on the bus at 07:00), NOT 5.5.
        """
        state = {}
        self._run_at_hour(6, 5.0, state=state)  # 06:xx → stored as last_value
        out, _ = self._run_at_hour(7, 5.5, state=state)  # 07:xx crosses target
        assert out["t1"] == pytest.approx(5.0)  # last_value, NOT 5.5

    def test_t1_uses_fval_when_no_prior_value(self):
        """If no prior measurement exists, the first 07:xx reading becomes T1."""
        state = {}
        out, _ = self._run_at_hour(7, 10.0, state=state)
        assert out["t1"] == pytest.approx(10.0)

    def test_t1_captured_at_hour_9_when_no_prior_slot(self):
        """Measurement at hour 9 fills T1 when no earlier measurement exists (Issue #548).

        "Erste-Kreuzung"-Semantik: T1 = last_value beim ersten Wert ab Stunde ≥ 7.
        Kein last_value vorhanden → fval (9:00-Wert) wird als beste Näherung verwendet.
        """
        state = {}
        out, _ = self._run_at_hour(9, 15.0, state=state)
        assert out["t1"] == pytest.approx(15.0)

    def test_t1_not_overwritten_by_second_reading_at_hour_7(self):
        """Once T1 is captured, a further reading during hour 7 is ignored."""
        state = {}
        self._run_at_hour(7, 10.0, state=state)
        out, _ = self._run_at_hour(7, 15.0, state=state)  # same hour → no change
        assert out["t1"] == pytest.approx(10.0)

    def test_t2_uses_last_value_at_1400_boundary(self):
        """T2 = value present at 14:00 (last_value from hour 13)."""
        state = {}
        self._run_at_hour(7, 5.0, state=state)  # T1 captured; last_value=5.0
        self._run_at_hour(13, 12.0, state=state)  # 13:xx → no T2 slot, last_value=12.0
        out, _ = self._run_at_hour(14, 12.5, state=state)  # 14:xx crosses T2 target
        assert out["t2"] == pytest.approx(12.0)  # last_value (13:xx), NOT 12.5

    def test_t2_not_captured_at_hour_13(self):
        """Measurement at hour 13 must not fill T2 (threshold is 14:00)."""
        state = {}
        out, _ = self._run_at_hour(13, 13.0, state=state)
        assert out["t2"] is None

    def test_t1_captured_at_hour_13_but_not_t2(self):
        """Measurement at hour 13 fills T1 (≥7) but NOT T2 (threshold 14)."""
        state = {}
        out, _ = self._run_at_hour(13, 13.0, state=state)
        assert out["t1"] == pytest.approx(13.0)
        assert out["t2"] is None

    def test_t2_captured_at_hour_14_with_crossing(self):
        """Measurement at hour 14 with no prior T2 fills both T1 and T2 in one shot (Issue #548)."""
        state = {}
        out, _ = self._run_at_hour(14, 14.0, state=state)
        assert out["t1"] == pytest.approx(14.0)
        assert out["t2"] == pytest.approx(14.0)

    def test_t3_uses_last_value_at_2100_boundary(self):
        """T3 = value present at 21:00 (last_value from earlier).

        T1/T2 are pre-set via _slot override.  A reading at 20:xx stores
        last_value=7.0.  When 21:xx arrives, T3 = 7.0 (last_value), not 7.5.
        daily_avg = (5.0 + 12.0 + 2×7.0) / 4 = 7.75.
        """
        state = {}
        self._run_slot("t1", 5.0, state=state)  # T1 = 5.0
        self._run_slot("t2", 12.0, state=state)  # T2 = 12.0
        self._run_at_hour(20, 7.0, state=state)  # last_value = 7.0 (before T3 time)
        out, _ = self._run_at_hour(21, 7.5, state=state)  # T3 = 7.0 (last_value), daily_avg fires
        assert out["t3"] == pytest.approx(7.0)  # last_value, NOT 7.5
        assert out["daily_avg"] == pytest.approx(7.75)
        assert out["t3"] is not None  # slots persist after daily_avg computation

    def test_t3_not_captured_at_hour_20(self):
        """Measurement at hour 20 must not fill T3 (threshold is 21:00)."""
        state = {}
        out, _ = self._run_at_hour(20, 8.0, state=state)
        assert out["t3"] is None

    def test_t3_captured_at_hour_21(self):
        """Measurement at hour 21 fills T3 (threshold hour >= 21)."""
        state = {}
        out, _ = self._run_at_hour(21, 8.0, state=state)
        assert out["t3"] is not None

    def test_full_day_via_exact_time_points_computes_daily_avg(self):
        """Full day: last_value at 07:00, 14:00, 21:00 → daily_avg computed.

        Pre-07:00 value = 10.0, triggers at 07:xx with 10.5 → T1=10.0
        Pre-14:00 value = 14.0, triggers at 14:xx with 14.2 → T2=14.0
        Pre-21:00 value = 8.0,  triggers at 21:xx with 8.1  → T3=8.0
        daily_avg = (10.0 + 14.0 + 2×8.0) / 4 = 10.0
        """
        state = {}
        self._run_at_hour(6, 10.0, state=state)  # last_value before T1
        self._run_at_hour(7, 10.5, state=state)  # T1 = 10.0 (last_value)
        self._run_at_hour(13, 14.0, state=state)  # last_value before T2
        self._run_at_hour(14, 14.2, state=state)  # T2 = 14.0 (last_value)
        self._run_at_hour(20, 8.0, state=state)  # last_value before T3
        out, _ = self._run_at_hour(21, 8.1, state=state)  # T3 = 8.0, daily computed
        assert out["daily_avg"] == pytest.approx(10.0)

    def test_slots_persist_after_daily_avg_computation(self):
        """T1/T2/T3 debug ports retain their values after daily_avg is computed."""
        out, _ = self._run_full_day(10, 12, 8)
        assert out["t1"] == pytest.approx(10.0)
        assert out["t2"] == pytest.approx(12.0)
        assert out["t3"] == pytest.approx(8.0)

    def test_no_double_daily_avg_same_day(self):
        """After the daily avg is computed for a date, further readings on the
        same date must not append a second entry to daily_temps."""
        state = {}
        # First full day: daily_temps gains one entry
        self._run_full_day(10, 14, 8, state=state, date="2025-06-01")
        count_after_day1 = len(state["h"]["daily_temps"])
        # Simulate late-night reading on the same date via _slot override
        self._run_slot("t3", 9.0, state=state, date="2025-06-01")
        assert len(state["h"]["daily_temps"]) == count_after_day1  # no second entry

    # ── Initialzustand ohne Historik ──────────────────────────────────────────

    def test_initial_heating_mode_below_threshold(self):
        """With no daily_avg yet, first measurement below threshold_temp → ON."""
        state = {}
        out, _ = self._run_slot("t1", 5.0, state=state)  # 5 < 14
        assert out["heating_mode"] == 1

    def test_initial_heating_mode_above_threshold(self):
        """With no daily_avg yet, first measurement >= threshold_temp → OFF."""
        state = {}
        out, _ = self._run_slot("t1", 18.0, state=state)  # 18 >= 14
        assert out["heating_mode"] == 0

    def test_initial_heating_mode_exactly_at_threshold(self):
        """Exactly at threshold_temp (14 °C) → summer mode (no heating needed)."""
        state = {}
        out, _ = self._run_slot("t1", 14.0, state=state)
        assert out["heating_mode"] == 0

    # ── Mehrere Tage / Debug-Ausgaben ─────────────────────────────────────────

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
        # Day 1: T_avg = (4+6+2*2)/4 = 3.5
        self._run_full_day(4, 6, 2, state=state, date=self._d(0))
        # Day 2: T_avg = (8+10+2*6)/4 = 7.5
        out, _ = self._run_full_day(8, 10, 6, state=state, date=self._d(1))
        assert out["monthly_avg"] == pytest.approx(5.5)

    def test_heating_mode_uses_daily_avg(self):
        """Heating mode is driven by daily_avg, not monthly_avg."""
        state = {}
        for i in range(3):
            self._run_full_day(22, 24, 22, state=state, date=self._d(i))  # daily_avg = 22.5 → OFF
        # Cold day: daily_avg = 4.75 < threshold (14) → ON, regardless of monthly_avg
        out, _ = self._run_full_day(5, 6, 4, state=state, date=self._d(3))
        assert out["heating_mode"] == 1

    def test_monthly_avg_is_debug_output_only(self):
        """monthly_avg accumulates correctly as a diagnostic output."""
        state = {}
        # Day 1: daily_avg = (4+6+2*2)/4 = 3.5
        self._run_full_day(4, 6, 2, state=state, date=self._d(0))
        # Day 2: daily_avg = (8+10+2*6)/4 = 7.5; monthly_avg = (3.5+7.5)/2 = 5.5
        out, _ = self._run_full_day(8, 10, 6, state=state, date=self._d(1))
        assert out["monthly_avg"] == pytest.approx(5.5)

    def test_no_input_returns_default_heating_mode(self):
        state = {}
        n1 = node("h", "heating_circuit", self._CFG)
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"h": {}})["h"]
        assert out["heating_mode"] == 0
        assert out["daily_avg"] is None

    def test_monthly_buffer_capped_at_31_days(self):
        state = {}
        for i in range(40):
            self._run_full_day(10, 12, 8, state=state, date=self._d(i))
        assert len(state["h"]["daily_temps"]) <= 31

    # ── History-Fallback ──────────────────────────────────────────────────────

    def test_history_fallback_fills_missing_t1(self):
        """_history_t1 from manager pre-fill is used when T1 is missing for today."""
        state = {}
        n1 = node("h", "heating_circuit", self._CFG)
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"h": {"_history_t1": 8.0, "_date": "2025-01-01"}})["h"]
        assert out["t1"] == pytest.approx(8.0)

    def test_history_fallback_does_not_overwrite_existing_slot(self):
        """_history_t1 is ignored when T1 is already captured for today."""
        state = {}
        self._run_slot("t1", 10.0, state=state, date="2025-01-01")
        n1 = node("h", "heating_circuit", self._CFG)
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute({"h": {"_history_t1": 5.0, "_date": "2025-01-01"}})["h"]
        assert out["t1"] == pytest.approx(10.0)  # live-captured value preserved

    def test_history_fallback_completes_daily_avg(self):
        """All three history slots trigger daily_avg computation (simulates post-restart fill)."""
        state = {}
        n1 = node("h", "heating_circuit", self._CFG)
        exc = make_executor([n1], hysteresis_state=state)
        out = exc.execute(
            {
                "h": {
                    "_history_t1": 10.0,
                    "_history_t2": 12.0,
                    "_history_t3": 8.0,
                    "_date": "2025-01-01",
                }
            }
        )["h"]
        # daily_avg = (10 + 12 + 2*8) / 4 = 9.5
        assert out["daily_avg"] == pytest.approx(9.5)
        assert out["heating_mode"] == 1  # 9.5 < threshold=14

    # ── Issue #548: Erste-Kreuzung — Sensor trifft Stunden nicht exakt ────────

    def test_single_measurement_at_hour_22_fills_all_slots(self):
        """A single daily measurement at hour 22 fills T1, T2, and T3 in one shot
        and immediately triggers the daily_avg computation.

        Reproduces Issue #548: sensors with 4h intervals may never hit hours 7 or 14 exactly.
        With crossing semantics, the 22:00 reading (22 >= 21) sets T1=T2=T3=last_value.
        Slots are NOT reset after computation; debug ports retain values.
        """
        state = {}
        self._run_at_hour(6, 25.0, state=state)  # last_value = 25.0
        out, _ = self._run_at_hour(22, 26.0, state=state)  # T1=T2=T3=25 → daily_avg computed
        assert out["daily_avg"] == pytest.approx(25.0)  # (25+25+2×25)/4 = 25
        assert out["monthly_avg"] == pytest.approx(25.0)
        # Slots retain their values after daily_avg computation (no reset)
        assert out["t1"] == pytest.approx(25.0)
        assert out["t2"] == pytest.approx(25.0)
        assert out["t3"] == pytest.approx(25.0)

    def test_sparse_sensor_4h_interval_computes_daily_avg(self):
        """Sensor sending every 4 h starting at 2:00 (2/6/10/14/18/22) fills all slots.

        With crossing semantics:
          T1 captured at 10:00 (10 >= 7) using last_value from 6:00 = 21.0
          T2 captured at 14:00 (14 >= 14) using last_value from 10:00 = 25.0
          T3 captured at 22:00 (22 >= 21) using last_value from 18:00 = 26.0
        daily_avg = (21 + 25 + 2×26) / 4 = 98/4 = 24.5
        """
        state = {}
        temps = {2: 20.0, 6: 21.0, 10: 25.0, 14: 28.0, 18: 26.0, 22: 22.0}
        out = None
        for hour in sorted(temps):
            out, _ = self._run_at_hour(hour, temps[hour], state=state)
        assert out["daily_avg"] == pytest.approx(24.5)
        assert out["monthly_avg"] == pytest.approx(24.5)
        # 24.5 >= threshold+hysteresis (16) → heating OFF
        assert out["heating_mode"] == 0

    def test_issue_548_heating_turns_off_after_one_warm_day_with_sparse_sensor(self):
        """Issue #548 scenario: heating was ON from winter, sensor uses 4h intervals.

        With crossing semantics daily_avg is computed even without hitting exact slot hours.
        A warm day (daily_avg >= threshold+hysteresis) immediately turns heating OFF.
        """
        state = {}
        self._run_full_day(5, 6, 4, state=state, date=self._d(0))
        assert state["h"]["heating_mode"] == 1  # cold → ON

        temps_day1 = {2: 25.0, 6: 26.0, 10: 28.0, 14: 30.0, 18: 27.0, 22: 24.0}
        out = None
        for hour in sorted(temps_day1):
            out, _ = self._run_at_hour(hour, temps_day1[hour], state=state, date=self._d(1))

        # daily_avg was computed via crossing semantics
        assert out["daily_avg"] is not None, "daily_avg must be computed with crossing semantics"
        # Warm day: daily_avg well above threshold+hysteresis → OFF
        assert out["heating_mode"] == 0

    # ── Legacy config migration ───────────────────────────────────────────────

    def test_legacy_temp_winter_temp_summer_honoured(self):
        """Existing graphs with temp_winter/temp_summer still work after config rename."""
        legacy_cfg = {"temp_winter": 12.0, "temp_summer": 18.0}
        state = {}
        # Cold day: daily_avg = 4.75 < temp_winter (12) → ON
        out, _ = self._run_full_day(5, 6, 4, config=legacy_cfg, state=state, date=self._d(0))
        assert out["heating_mode"] == 1
        # Warm day: daily_avg = 22.5 >= temp_winter + (temp_summer - temp_winter) = 18 → OFF
        out2, _ = self._run_full_day(22, 24, 22, config=legacy_cfg, state=state, date=self._d(1))
        assert out2["heating_mode"] == 0

    def test_legacy_fields_ignored_when_new_fields_present(self):
        """When new config keys are present, legacy fields are ignored."""
        mixed_cfg = {"threshold_temp": 14.0, "hysteresis": 2.0, "temp_winter": 5.0, "temp_summer": 6.0}
        out, _ = self._run_full_day(10, 12, 8, config=mixed_cfg)
        # daily_avg = 9.5 < threshold=14 → ON (uses new key, not legacy temp_winter=5)
        assert out["heating_mode"] == 1


# ===========================================================================
# min_max_tracker
# ===========================================================================


class TestMinMaxTracker:
    def _run(self, value, state=None, app_config=None):
        if state is None:
            state = {}
        n1 = node("m", "min_max_tracker", {})
        exc = make_executor([n1], hysteresis_state=state, app_config=app_config)
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
        _out, state = self._run(5.0, state)
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

    def test_periods_use_configured_application_timezone(self):
        utc_today = datetime.now(UTC).date()
        timezone = next(name for name in ("Pacific/Kiritimati", "Etc/GMT+12") if datetime.now(ZoneInfo(name)).date() != utc_today)
        expected_day = datetime.now(ZoneInfo(timezone)).date().isoformat()

        _, state = self._run(42.0, app_config={"timezone": timezone})

        assert state["m"]["last_day"] == expected_day

    def test_invalid_application_timezone_falls_back_to_default(self):
        _, state = self._run(42.0, app_config={"timezone": "not/a-timezone"})

        assert state["m"]["last_day"] == datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat()

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

    def test_periods_use_configured_application_timezone(self):
        # Choose a timezone whose calendar date differs from UTC right now,
        # so this cannot accidentally pass by using the server clock.
        utc_today = datetime.now(UTC).date()
        timezone = next(name for name in ("Pacific/Kiritimati", "Etc/GMT+12") if datetime.now(ZoneInfo(name)).date() != utc_today)
        expected_day = datetime.now(ZoneInfo(timezone)).date().isoformat()
        state = {}
        n1 = node("c", "consumption_counter", {})
        exc = make_executor([n1], hysteresis_state=state, app_config={"timezone": timezone})

        exc.execute({"c": {"value": 100.0}})

        assert state["c"]["last_day"] == expected_day

    def test_invalid_app_timezone_falls_back_to_default(self):
        """An invalid app_config timezone must not blow up the node — it
        falls back to Europe/Zurich and execution proceeds normally."""
        state = {}
        n1 = node("c", "consumption_counter", {})
        exc = make_executor([n1], hysteresis_state=state, app_config={"timezone": "not-a-real-timezone"})

        out = exc.execute({"c": {"value": 100.0}})["c"]

        assert out["daily"] == pytest.approx(0.0)
        assert state["c"]["last_day"] == datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat()

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
