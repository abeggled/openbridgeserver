"""Several plugin files side by side in one plugins directory (issue #446)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from obs.logic import plugin_loader, plugin_registry

_TEMPLATE = """
from obs.logic.plugin_api import LogicNodePlugin, NodeTypeDef, register_node_type


@register_node_type
class Block(LogicNodePlugin):
    type_name = "{type_name}"

    @classmethod
    def node_type_def(cls):
        return NodeTypeDef(type="{type_name}", label="{label}", category="integration")

    @classmethod
    def evaluate(cls, node_id, inputs, config, state):
        return {{}}, state
"""


def _write(directory: Path, file_stem: str, type_name: str, label: str = "") -> Path:
    path = directory / f"{file_stem}.py"
    path.write_text(_TEMPLATE.format(type_name=type_name, label=label or file_stem))
    return path


@pytest.fixture
def plugins_dir(tmp_path):
    yield tmp_path
    for path in tmp_path.glob("*.py"):
        plugin_loader._unload_file(path)


def _label(type_name: str) -> str:
    return plugin_registry.get_plugin_node_type(type_name).node_type_def().label


def test_plugins_side_by_side_load_reload_and_unload_independently(plugins_dir):
    alpha = _write(plugins_dir, "obs_plugin_loader_alpha", "loader_alpha")
    beta = _write(plugins_dir, "obs_plugin_loader_beta", "loader_beta")
    _write(plugins_dir, "_helper", "loader_hidden")

    assert plugin_loader.load_plugins(str(plugins_dir)) >= 2
    assert {"loader_alpha", "loader_beta"} <= set(plugin_registry._registry)
    assert plugin_registry.get_plugin_node_type("loader_hidden") is None

    _write(plugins_dir, "obs_plugin_loader_alpha", "loader_alpha", label="Alpha v2")
    assert plugin_loader._load_file(alpha) == ["loader_alpha"]
    assert _label("loader_alpha") == "Alpha v2"
    assert plugin_registry.get_plugin_node_type("loader_beta") is not None

    assert plugin_loader._unload_file(beta) == ["loader_beta"]
    assert plugin_registry.get_plugin_node_type("loader_beta") is None
    assert plugin_registry.get_plugin_node_type("loader_alpha") is not None


def test_second_file_with_a_taken_type_name_neither_replaces_nor_removes_it(plugins_dir, caplog):
    first = _write(plugins_dir, "obs_plugin_loader_first", "loader_shared", label="First")
    second = _write(plugins_dir, "obs_plugin_loader_second", "loader_shared", label="Second")

    assert plugin_loader._load_file(first) == ["loader_shared"]
    with caplog.at_level(logging.ERROR):
        assert plugin_loader._load_file(second) == []
    assert "already provided by" in caplog.text
    assert _label("loader_shared") == "First"

    assert plugin_loader._unload_file(second) == []
    assert _label("loader_shared") == "First"
