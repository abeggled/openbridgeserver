"""Metadata and default contract of the ``string_replace`` function block."""

from __future__ import annotations

import json

from obs.logic.nodes.string.replace import NODE_TYPE


def test_string_replace_declares_one_text_input_and_one_result_output():
    assert NODE_TYPE.type == "string_replace"
    assert NODE_TYPE.category == "string"
    assert [(p.id, p.type) for p in NODE_TYPE.inputs] == [("text", "string")]
    assert [(p.id, p.type) for p in NODE_TYPE.outputs] == [("result", "string")]


def test_string_replace_defaults_to_one_empty_plain_rule():
    rules = json.loads(NODE_TYPE.config_schema["rules"]["default"])

    assert rules == [
        {"search": "", "replace": "", "mode": "plain", "case_sensitive": True, "replace_all": True},
    ]


def test_string_replace_does_not_classify_itself():
    assert NODE_TYPE.has_external_side_effect is None
    assert NODE_TYPE.required_capability is None
