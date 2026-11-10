"""Unit tests for obs.logic.node_types lookup helpers.

Covers the plugin-aware branches of get_node_type() / list_node_types() added by
the function-block import feature (#446), which are not exercised by the
built-in node definitions alone.
"""

from __future__ import annotations

import pytest

from obs.logic import plugin_registry
from obs.logic.capabilities import PLUGIN_CAPABILITY
from obs.logic.models import NodeTypeDef
from obs.logic.node_types import BUILTIN_NODE_TYPES, get_node_type, list_node_types
from obs.logic.plugin_api import LogicNodePlugin, register_node_type

PLUGIN_TYPE = "test_dummy_block"


@pytest.fixture
def dummy_plugin():
    """Register a throwaway plugin node type and clean it up afterwards."""

    @register_node_type
    class DummyBlock(LogicNodePlugin):
        type_name = PLUGIN_TYPE

        @classmethod
        def node_type_def(cls) -> NodeTypeDef:
            return NodeTypeDef(type=PLUGIN_TYPE, label="Dummy", category="logic")

        @classmethod
        def evaluate(cls, node_id, inputs, config, state):
            return {}, state

    yield DummyBlock
    plugin_registry._unregister(PLUGIN_TYPE)


def test_get_node_type_returns_builtin():
    nt = get_node_type("and")
    assert nt is not None
    assert nt.type == "and"


def test_get_node_type_returns_plugin(dummy_plugin):
    nt = get_node_type(PLUGIN_TYPE)
    assert nt is not None
    assert nt.type == PLUGIN_TYPE
    assert nt.label == "Dummy"
    # Every plugin node type is always classified into the shared authorization
    # bucket by obs/logic/registry.py — see docs/logic-plugin-api.md.
    assert nt.has_external_side_effect is True
    assert nt.required_capability == PLUGIN_CAPABILITY


def test_list_node_types_classifies_plugins_with_the_shared_capability(dummy_plugin):
    (nt,) = [t for t in list_node_types() if t.type == PLUGIN_TYPE]
    assert nt.has_external_side_effect is True
    assert nt.required_capability == PLUGIN_CAPABILITY


def test_get_node_type_unknown_returns_none():
    assert get_node_type("does_not_exist_xyz") is None


def test_list_node_types_includes_builtins_and_plugins(dummy_plugin):
    types = list_node_types()
    type_names = {nt.type for nt in types}
    assert {nt.type for nt in BUILTIN_NODE_TYPES} <= type_names
    assert PLUGIN_TYPE in type_names


def test_list_node_types_builtins_only_without_plugins():
    types = list_node_types()
    assert PLUGIN_TYPE not in {nt.type for nt in types}


def test_register_empty_type_name_raises():
    class NoName(LogicNodePlugin):
        type_name = ""

        @classmethod
        def node_type_def(cls) -> NodeTypeDef:
            return NodeTypeDef(type="x", label="x", category="logic")

        @classmethod
        def evaluate(cls, node_id, inputs, config, state):
            return {}, state

    with pytest.raises(ValueError, match="non-empty type_name"):
        register_node_type(NoName)


def _block(type_name: str, label: str) -> type[LogicNodePlugin]:
    class Block(LogicNodePlugin):
        @classmethod
        def node_type_def(cls) -> NodeTypeDef:
            return NodeTypeDef(type=type_name, label=label, category="logic")

        @classmethod
        def evaluate(cls, node_id, inputs, config, state):
            return {}, state

    Block.type_name = type_name
    return Block


def test_register_duplicate_keeps_the_first_plugin(dummy_plugin, caplog):
    import logging

    with caplog.at_level(logging.ERROR):
        register_node_type(_block(PLUGIN_TYPE, "Dummy2"))
    assert "already provided by" in caplog.text
    assert get_node_type(PLUGIN_TYPE).label != "Dummy2"


def test_register_builtin_type_name_is_ignored(caplog):
    import logging

    with caplog.at_level(logging.ERROR):
        register_node_type(_block("and", "Fake AND"))
    assert "clashes with a built-in block" in caplog.text
    assert plugin_registry.get_plugin_node_type("and") is None
    assert get_node_type("and").label != "Fake AND"


def test_unregister_missing_returns_false():
    assert plugin_registry._unregister("never_registered_zzz") is False


def test_list_node_types_skips_plugin_raising_node_type_def(caplog):
    import logging

    @register_node_type
    class BrokenBlock(LogicNodePlugin):
        type_name = "test_broken_block"

        @classmethod
        def node_type_def(cls) -> NodeTypeDef:
            raise RuntimeError("boom")

        @classmethod
        def evaluate(cls, node_id, inputs, config, state):
            return {}, state

    try:
        with caplog.at_level(logging.ERROR):
            types = list_node_types()
        assert "test_broken_block" not in {nt.type for nt in types}
        assert "node_type_def() raised" in caplog.text
    finally:
        plugin_registry._unregister("test_broken_block")
