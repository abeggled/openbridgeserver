"""Unit tests for ``config_schema_warnings`` — see obs/logic/graph_analysis.py."""

from __future__ import annotations

from obs.logic.graph_analysis import config_schema_warnings
from obs.logic.models import FlowData, LogicNode
from obs.logic.registry import BUILTIN_NODE_TYPES


def _flow(node_type: str, data: dict | None = None, node_id: str = "n1") -> FlowData:
    return FlowData(nodes=[LogicNode(id=node_id, type=node_type, position={"x": 0, "y": 0}, data=data or {})])


def _codes(flow: FlowData) -> list[str]:
    return [w["code"] for w in config_schema_warnings(flow)]


def test_unknown_node_type_is_flagged():
    warnings = config_schema_warnings(_flow("totally_made_up"))

    assert [w["code"] for w in warnings] == ["unknown_node_type"]
    assert warnings[0]["node_id"] == "n1"


def test_missing_node_placeholder_is_not_flagged():
    assert config_schema_warnings(_flow("missing_node")) == []


def test_absent_key_is_never_flagged():
    # decision's "conditions" has a rich schema but isn't set here at all.
    assert config_schema_warnings(_flow("decision", {})) == []


def test_enum_valid_and_invalid():
    assert _codes(_flow("compare", {"operator": ">"})) == []
    assert _codes(_flow("compare", {"operator": "greater_than"})) == ["config_schema_enum_invalid"]


def test_numeric_field_accepts_real_numbers_bool_numeric_strings_and_the_empty_string_sentinel():
    for value in (5, 5.5, True, "5.5", ""):
        assert _codes(_flow("compare", {"operand": value})) == [], value


def test_numeric_field_rejects_non_numeric_content():
    assert _codes(_flow("compare", {"operand": "abc"})) == ["config_schema_type_mismatch"]
    # Neither a number, a bool, nor a string — the final `_is_numeric` fallback.
    assert _codes(_flow("compare", {"operand": [1, 2]})) == ["config_schema_type_mismatch"]


def test_none_is_always_skipped_regardless_of_declared_type():
    assert _codes(_flow("compare", {"operand": None})) == []
    assert _codes(_flow("compare", {"operator": None})) == []


def test_string_field_type_mismatch():
    assert _codes(_flow("datapoint_read", {"datapoint_name": 123})) == ["config_schema_type_mismatch"]
    assert _codes(_flow("datapoint_read", {"datapoint_name": "living_room_temp"})) == []


def test_boolean_field_is_never_flagged():
    # GraphExecutor._to_bool coerces any value, so nothing here is "invalid".
    assert _codes(_flow("datapoint_read", {"trigger_on_change": "not-a-bool"})) == []
    assert _codes(_flow("datapoint_read", {"trigger_on_change": 42})) == []


def test_array_field_malformed_json_string():
    assert _codes(_flow("decision", {"conditions": "{not valid json"})) == ["config_schema_malformed_json"]


def test_array_field_wrong_top_level_type():
    assert _codes(_flow("decision", {"conditions": 42})) == ["config_schema_type_mismatch"]
    # A JSON string that parses fine but not to a list goes through the same check.
    assert _codes(_flow("decision", {"conditions": "42"})) == ["config_schema_type_mismatch"]
    # An empty string is treated like an empty list — no items, nothing to flag.
    assert _codes(_flow("decision", {"conditions": ""})) == []


def test_array_item_not_an_object():
    assert _codes(_flow("decision", {"conditions": [123]})) == ["config_schema_type_mismatch"]


def test_array_item_missing_required_field():
    assert _codes(_flow("decision", {"conditions": [{"operator": "eq"}]})) == ["config_schema_missing_required_field"]
    # Empty string counts as "missing" too, matching the executor's own `or` fallback.
    assert _codes(_flow("decision", {"conditions": [{"handle": "", "operator": "eq"}]})) == ["config_schema_missing_required_field"]


def test_array_item_nested_enum_is_validated():
    warnings = config_schema_warnings(_flow("decision", {"conditions": [{"handle": "out_1", "operator": "greater_than"}]}))

    assert [w["code"] for w in warnings] == ["config_schema_enum_invalid"]


def test_array_item_nested_untyped_fields_are_never_flagged():
    data = {"conditions": [{"handle": "out_1", "operator": "eq", "value": {"nested": "anything"}, "min": object()}]}
    # "value"/"min" are deliberately untyped (their legal shape depends on the operator) —
    # asserting no crash and no warning for an arbitrary dict/object value.
    assert config_schema_warnings(_flow("decision", data)) == []


def test_array_field_without_items_schema_only_checks_list_shape():
    # value_sequence.steps has no "items" schema — shallow check only.
    assert _codes(_flow("value_sequence", {"steps": [1, 2, 3]})) == []
    assert _codes(_flow("value_sequence", {"steps": "not json"})) == ["config_schema_malformed_json"]


def test_every_node_types_own_default_config_is_self_consistent():
    # string_replace's default is a deliberate exception: its one starter rule ships with an
    # empty "search" on purpose ("a freshly dropped block already shows an editable row" —
    # obs/logic/nodes/string/replace.py), and an empty "search" is exactly what the executor
    # itself treats as "this rule does nothing yet" (GraphExecutor._apply_replace_rules skips a
    # blank search). The validator correctly reports that starter rule as incomplete.
    expected_warning_codes = {"string_replace": ["config_schema_missing_required_field"]}

    for node_type in BUILTIN_NODE_TYPES:
        data = {key: field_schema["default"] for key, field_schema in node_type.config_schema.items() if "default" in field_schema}

        codes = _codes(_flow(node_type.type, data))
        assert codes == expected_warning_codes.get(node_type.type, []), node_type.type
