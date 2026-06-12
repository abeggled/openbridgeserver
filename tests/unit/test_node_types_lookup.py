"""Unit tests for obs.logic.node_types lookup helpers.

Covers the plugin-aware branches of get_node_type() / list_node_types() added by
the function-block import feature (#446), which are not exercised by the
built-in node definitions alone.
"""

from __future__ import annotations

import pytest

from obs.logic import plugin_registry
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


def test_register_overwrite_warns(dummy_plugin, caplog):
    import logging

    class DummyBlock2(LogicNodePlugin):
        type_name = PLUGIN_TYPE

        @classmethod
        def node_type_def(cls) -> NodeTypeDef:
            return NodeTypeDef(type=PLUGIN_TYPE, label="Dummy2", category="logic")

        @classmethod
        def evaluate(cls, node_id, inputs, config, state):
            return {}, state

    with caplog.at_level(logging.WARNING):
        register_node_type(DummyBlock2)
    assert "already registered" in caplog.text
    assert get_node_type(PLUGIN_TYPE).label == "Dummy2"


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
