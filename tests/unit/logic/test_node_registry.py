"""Contract tests for the global built-in node catalogue.

These tests guard the *registry* — identifiers, catalogue assembly and the
public API surface. Assertions about a single function block belong next to
that block under ``tests/unit/logic/nodes/<category>/``.
"""

from __future__ import annotations

import pytest

from obs.logic import node_types as facade
from obs.logic.capabilities import LOGIC_NODE_CAPABILITIES, PURE_LOGIC_NODE_TYPES
from obs.logic.models import FlowData, LogicNode, NodeTypeDef
from obs.logic.registry import (
    BUILTIN_NODE_CATEGORIES,
    BUILTIN_NODE_TYPES,
    NODE_TYPE_REGISTRY,
    _build_catalogue,
    get_node_type,
    list_node_types,
)

# Frozen public contract: persisted graphs, exports and the logic editor address
# blocks by these identifiers. The catalogue must keep publishing all of them —
# removing or renaming one is a breaking change. Adding a new block needs no edit
# here; it is acknowledged in obs/logic/capabilities.py instead.
PUBLISHED_NODE_TYPES = frozenset(
    {
        "ai_logic",
        "and",
        "api_client",
        "astro_sun",
        "avg_multi",
        "clamp",
        "comment",
        "compare",
        "const_value",
        "consumption_counter",
        "datapoint_read",
        "datapoint_write",
        "datetime",
        "decision",
        "gate",
        "heating_circuit",
        "host_check",
        "hysteresis",
        "ical",
        "json_extractor",
        "math_formula",
        "math_map",
        "memory",
        "message_archive",
        "min_max_tracker",
        "not",
        "notify_message",
        "notify_pushover",
        "notify_sms",
        "operating_hours",
        "or",
        "python_script",
        "random_value",
        "statistics",
        "string_concat",
        "substring_extractor",
        "timer_cron",
        "timer_delay",
        "timer_pulse",
        "value_mapping",
        "value_sequence",
        "wake_on_lan",
        "xml_extractor",
        "xor",
    }
)

PORT_TYPES = frozenset({"value", "trigger", "string", "boolean", "number"})
CONFIG_TYPES = frozenset({"string", "integer", "number", "boolean", "array"})


def test_every_node_type_identifier_is_unique():
    identifiers = [node_type.type for node_type in BUILTIN_NODE_TYPES]

    assert len(identifiers) == len(set(identifiers))
    assert set(NODE_TYPE_REGISTRY) == set(identifiers)


def test_catalogue_still_publishes_the_documented_node_type_identifiers():
    published = {node_type.type for node_type in BUILTIN_NODE_TYPES}

    assert PUBLISHED_NODE_TYPES <= published, f"node types no longer published: {sorted(PUBLISHED_NODE_TYPES - published)}"


def test_every_category_registry_is_included_in_the_catalogue():
    catalogue = list(BUILTIN_NODE_TYPES)

    for category, node_types in BUILTIN_NODE_CATEGORIES.items():
        assert node_types, f"category {category} registers no node"
        for node_type in node_types:
            registered = NODE_TYPE_REGISTRY[node_type.type]
            assert registered.type == node_type.type
            assert registered.category == category

    assert len(catalogue) == sum(len(node_types) for node_types in BUILTIN_NODE_CATEGORIES.values())


def test_catalogue_is_grouped_by_category_in_palette_order():
    categories = [node_type.category for node_type in BUILTIN_NODE_TYPES]
    grouped = [category for index, category in enumerate(categories) if index == 0 or categories[index - 1] != category]

    assert grouped == list(BUILTIN_NODE_CATEGORIES)


@pytest.mark.parametrize("node_type", BUILTIN_NODE_TYPES, ids=lambda node_type: node_type.type)
def test_registered_node_declares_valid_ports(node_type: NodeTypeDef):
    for ports in (node_type.inputs, node_type.outputs):
        identifiers = [p.id for p in ports]
        assert len(identifiers) == len(set(identifiers)), f"duplicate port id in {node_type.type}"
        for p in ports:
            assert p.id, f"empty port id in {node_type.type}"
            assert p.label, f"empty port label in {node_type.type}.{p.id}"
            assert p.type in PORT_TYPES, f"unknown port type {p.type!r} in {node_type.type}.{p.id}"


@pytest.mark.parametrize("node_type", BUILTIN_NODE_TYPES, ids=lambda node_type: node_type.type)
def test_registered_node_declares_a_valid_config_schema(node_type: NodeTypeDef):
    assert node_type.label, f"{node_type.type} has no label"
    assert node_type.color.startswith("#"), f"{node_type.type} has no colour"

    for key, schema in node_type.config_schema.items():
        assert schema.get("type") in CONFIG_TYPES, f"{node_type.type}.{key} declares no known type"
        if "enum" in schema and "default" in schema and schema["default"] is not None:
            assert schema["default"] in schema["enum"], f"{node_type.type}.{key} default is not a member of its enum"


@pytest.mark.parametrize("node_type", BUILTIN_NODE_TYPES, ids=lambda node_type: node_type.type)
def test_registered_node_is_classified_for_authorization(node_type: NodeTypeDef):
    assert node_type.has_external_side_effect is not None, f"{node_type.type} is not classified"

    if node_type.type in LOGIC_NODE_CAPABILITIES:
        assert node_type.has_external_side_effect is True
        assert node_type.required_capability == LOGIC_NODE_CAPABILITIES[node_type.type]
    else:
        assert node_type.type in PURE_LOGIC_NODE_TYPES
        assert node_type.has_external_side_effect is False
        assert node_type.required_capability is None


def test_duplicate_node_type_registration_fails_early():
    duplicate = NodeTypeDef(type="and", label="Copy", category="math")

    with pytest.raises(ValueError, match="duplicate node type 'and'"):
        _build_catalogue({"logic": BUILTIN_NODE_CATEGORIES["logic"], "math": (duplicate,)})


def test_node_registered_in_a_foreign_category_fails_early():
    misplaced = NodeTypeDef(type="stray", label="Stray", category="math")

    with pytest.raises(ValueError, match="declares category 'math' but is registered in 'timer'"):
        _build_catalogue({"timer": (misplaced,)})


def test_node_declaring_its_own_side_effect_flag_fails_early():
    """A node must not classify itself — that would bypass capabilities.py."""
    self_classified = NodeTypeDef(type="stray", label="Stray", category="math", has_external_side_effect=False)

    with pytest.raises(ValueError, match="must not declare its own authorization classification"):
        _build_catalogue({"math": (self_classified,)})


def test_node_declaring_its_own_capability_fails_early():
    self_classified = NodeTypeDef(type="stray", label="Stray", category="math", required_capability="http_request")

    with pytest.raises(ValueError, match="must not declare its own authorization classification"):
        _build_catalogue({"math": (self_classified,)})


def test_lookup_helpers_expose_the_registry():
    assert get_node_type("and") is NODE_TYPE_REGISTRY["and"]
    assert get_node_type("does_not_exist") is None
    assert list_node_types() is BUILTIN_NODE_TYPES


def test_compatibility_facade_re_exports_the_registry():
    assert facade.BUILTIN_NODE_TYPES is BUILTIN_NODE_TYPES
    assert facade.NODE_TYPE_REGISTRY is NODE_TYPE_REGISTRY
    assert facade.get_node_type is get_node_type
    assert facade.list_node_types is list_node_types


@pytest.mark.asyncio
async def test_api_serves_the_registry_catalogue():
    from obs.api.v1.logic import get_node_types

    served = await get_node_types(_user="admin")

    assert [node_type.type for node_type in served] == [node_type.type for node_type in BUILTIN_NODE_TYPES]


def test_persisted_graph_using_every_node_type_remains_loadable():
    flow = FlowData(
        nodes=[LogicNode(id=f"n{index}", type=node_type.type, position={"x": 0, "y": 0}) for index, node_type in enumerate(BUILTIN_NODE_TYPES)]
    )

    reloaded = FlowData.model_validate(flow.model_dump())

    assert [node.type for node in reloaded.nodes] == [node_type.type for node_type in BUILTIN_NODE_TYPES]
    for node in reloaded.nodes:
        assert get_node_type(node.type) is not None
