"""Registration and lookup of the built-in Logic node catalogue.

This module owns the *assembly* of the catalogue only: it combines the
per-category ``NODE_TYPES`` tuples from ``obs.logic.nodes`` and exposes the
lookup helpers the rest of the application uses. It deliberately contains no
node-specific business logic — a node's metadata, defaults and validation live
in its own module under ``obs/logic/nodes/<category>/``.

Registration is explicit (no runtime package scanning) so that missing or
duplicate node types fail on import instead of silently changing the public
catalogue. See ``docs/architecture/logic-nodes.md``.
"""

from __future__ import annotations

from obs.logic.capabilities import LOGIC_NODE_CAPABILITIES, PURE_LOGIC_NODE_TYPES
from obs.logic.models import NodeTypeDef
from obs.logic.nodes.ai import NODE_TYPES as AI_NODE_TYPES
from obs.logic.nodes.astro import NODE_TYPES as ASTRO_NODE_TYPES
from obs.logic.nodes.datapoint import NODE_TYPES as DATAPOINT_NODE_TYPES
from obs.logic.nodes.integration import NODE_TYPES as INTEGRATION_NODE_TYPES
from obs.logic.nodes.logic import NODE_TYPES as LOGIC_NODE_TYPES
from obs.logic.nodes.math import NODE_TYPES as MATH_NODE_TYPES
from obs.logic.nodes.notification import NODE_TYPES as NOTIFICATION_NODE_TYPES
from obs.logic.nodes.script import NODE_TYPES as SCRIPT_NODE_TYPES
from obs.logic.nodes.string import NODE_TYPES as STRING_NODE_TYPES
from obs.logic.nodes.timer import NODE_TYPES as TIMER_NODE_TYPES

# Category registries, in catalogue order. The key is the node ``category`` and
# at the same time the package name below ``obs/logic/nodes/``; the order
# mirrors the palette order in gui/src/components/logic/NodePalette.vue.
BUILTIN_NODE_CATEGORIES: dict[str, tuple[NodeTypeDef, ...]] = {
    "logic": LOGIC_NODE_TYPES,
    "datapoint": DATAPOINT_NODE_TYPES,
    "math": MATH_NODE_TYPES,
    "string": STRING_NODE_TYPES,
    "timer": TIMER_NODE_TYPES,
    "astro": ASTRO_NODE_TYPES,
    "notification": NOTIFICATION_NODE_TYPES,
    "integration": INTEGRATION_NODE_TYPES,
    "script": SCRIPT_NODE_TYPES,
    "ai": AI_NODE_TYPES,
}


def _classify_node_type(node_type: NodeTypeDef) -> NodeTypeDef:
    """Attach the authorization classification a node type is registered for."""
    capability = LOGIC_NODE_CAPABILITIES.get(node_type.type)
    if capability is not None:
        return node_type.model_copy(
            update={"has_external_side_effect": True, "required_capability": capability},
        )
    if node_type.type in PURE_LOGIC_NODE_TYPES:
        return node_type.model_copy(
            update={"has_external_side_effect": False, "required_capability": None},
        )
    return node_type


def _build_catalogue(categories: dict[str, tuple[NodeTypeDef, ...]]) -> list[NodeTypeDef]:
    """Flatten the category registries into the global catalogue.

    Raises ``ValueError`` on a duplicate node type identifier, on a node
    registered in a category package that does not match its ``category``, and
    on a node that classifies itself instead of being classified centrally.
    """
    catalogue: list[NodeTypeDef] = []
    seen: dict[str, str] = {}
    for category, node_types in categories.items():
        for node_type in node_types:
            if node_type.type in seen:
                raise ValueError(f"duplicate node type {node_type.type!r} registered in {seen[node_type.type]!r} and {category!r}")
            if node_type.category != category:
                raise ValueError(f"node type {node_type.type!r} declares category {node_type.category!r} but is registered in {category!r}")
            if node_type.has_external_side_effect is not None or node_type.required_capability is not None:
                # A node module must not classify itself: an unlisted node declaring
                # has_external_side_effect=False would pass the Logic run preflight as a
                # pure block without ever being reviewed into PURE_LOGIC_NODE_TYPES.
                raise ValueError(
                    f"node type {node_type.type!r} must not declare its own authorization classification — classify it in obs/logic/capabilities.py"
                )
            seen[node_type.type] = category
            catalogue.append(_classify_node_type(node_type))
    return catalogue


BUILTIN_NODE_TYPES: list[NodeTypeDef] = _build_catalogue(BUILTIN_NODE_CATEGORIES)

# Dict lookup by type
NODE_TYPE_REGISTRY: dict[str, NodeTypeDef] = {nt.type: nt for nt in BUILTIN_NODE_TYPES}


def get_node_type(type_: str) -> NodeTypeDef | None:
    """Return the definition of a single node type, or ``None`` if unknown."""
    return NODE_TYPE_REGISTRY.get(type_)


def list_node_types() -> list[NodeTypeDef]:
    """Return the complete public node catalogue (order is API-visible)."""
    return BUILTIN_NODE_TYPES


__all__ = [
    "BUILTIN_NODE_CATEGORIES",
    "BUILTIN_NODE_TYPES",
    "NODE_TYPE_REGISTRY",
    "get_node_type",
    "list_node_types",
]
