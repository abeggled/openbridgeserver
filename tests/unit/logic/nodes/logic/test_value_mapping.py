from __future__ import annotations

import json

from obs.logic.nodes.logic.value_mapping import NODE_TYPE


def test_value_mapping_default_rules_do_not_persist_localized_names():
    rules = json.loads(NODE_TYPE.config_schema["rules"]["default"])

    assert rules == [
        {"operator": "eq", "result": ""},
        {"operator": "eq", "result": ""},
    ]


def test_value_mapping_default_output_type_is_string_without_fallback():
    assert NODE_TYPE.config_schema["output_type"]["default"] == "string"
    assert NODE_TYPE.config_schema["has_default"]["default"] is False
    assert NODE_TYPE.config_schema["default_value"]["default"] == ""
