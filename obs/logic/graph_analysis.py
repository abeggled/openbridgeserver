"""Graph topology analysis for the logic engine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from obs.logic.models import FlowData, LogicNode
from obs.logic.registry import get_node_type

TICK_BOUNDARY_NODE_TYPES = {"memory"}

# Import-time placeholder for an unresolved node type (obs/api/v1/logic.py) — an
# already-known, intentional marker, not itself an "unknown" type to re-flag.
_MISSING_NODE_TYPE = "missing_node"

_NUMERIC_SCHEMA_TYPES = {"number", "integer"}


@dataclass(frozen=True)
class TopologicalSortResult:
    order: list[LogicNode]
    cyclic_node_ids: set[str]
    blocked_node_ids: set[str]

    @property
    def skipped_node_ids(self) -> set[str]:
        return self.cyclic_node_ids | self.blocked_node_ids


def edge_is_tick_boundary(flow: FlowData, target_node_id: str) -> bool:
    node_types = {node.id: node.type for node in flow.nodes}
    return node_types.get(target_node_id) in TICK_BOUNDARY_NODE_TYPES


def analyze_topology(flow: FlowData) -> TopologicalSortResult:
    node_map = {n.id: n for n in flow.nodes}
    node_types = {n.id: n.type for n in flow.nodes}
    in_degree: dict[str, int] = {n.id: 0 for n in flow.nodes}
    adj: dict[str, list[str]] = {n.id: [] for n in flow.nodes}

    for edge in flow.edges:
        if edge.source not in adj or edge.target not in in_degree:
            continue
        if node_types.get(edge.target) in TICK_BOUNDARY_NODE_TYPES:
            continue
        adj[edge.source].append(edge.target)
        in_degree[edge.target] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    order: list[LogicNode] = []

    while queue:
        nid = queue.pop(0)
        if nid in node_map:
            order.append(node_map[nid])
        for neighbor in adj.get(nid, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    ordered_ids = {n.id for n in order}
    unresolved = set(node_map) - ordered_ids
    cyclic_node_ids = _find_cyclic_node_ids(adj, unresolved)
    blocked_node_ids = unresolved - cyclic_node_ids

    return TopologicalSortResult(
        order=order,
        cyclic_node_ids=cyclic_node_ids,
        blocked_node_ids=blocked_node_ids,
    )


def topology_warnings(flow: FlowData) -> list[dict[str, str]]:
    analysis = analyze_topology(flow)
    ordered_cyclic = [node.id for node in flow.nodes if node.id in analysis.cyclic_node_ids]
    warnings: list[dict[str, str]] = []
    for node in flow.nodes:
        if node.id in analysis.cyclic_node_ids:
            warnings.append(
                {
                    "node_id": node.id,
                    "code": "graph_cycle",
                    "message": f"Graph cycle detected; node cannot be executed without a memory node. Cycle nodes: {', '.join(ordered_cyclic[:5])}",
                },
            )
        elif node.id in analysis.blocked_node_ids:
            warnings.append(
                {
                    "node_id": node.id,
                    "code": "graph_cycle_blocked",
                    "message": f"Graph cycle detected upstream; node cannot be executed. Cycle nodes: {', '.join(ordered_cyclic[:5])}",
                },
            )
    return warnings


def config_schema_warnings(flow: FlowData) -> list[dict[str, str]]:
    """Check each node's ``data`` against its type's ``config_schema``.

    Deliberately narrow: types, enums and array/object shape only — no cross-entity checks (e.g.
    whether a ``datapoint_id`` actually exists). A key absent from ``data`` is never flagged (it
    falls back to the schema default); a present ``None`` is always treated as "no value yet" and
    skipped too. Mirrors the executor's own coercion leniency (``GraphExecutor._to_num``,
    ``_load_rule_list``) so this reports exactly the inputs that would silently produce wrong
    behaviour at run time, not inputs the executor already handles safely.
    """
    warnings: list[dict[str, str]] = []
    for node in flow.nodes:
        if node.type == _MISSING_NODE_TYPE:
            continue
        node_type = get_node_type(node.type)
        if node_type is None:
            warnings.append(_schema_warning(node.id, "unknown_node_type", f"Unknown node type '{node.type}'."))
            continue
        for key, field_schema in node_type.config_schema.items():
            if key not in node.data:
                continue
            value = node.data[key]
            if field_schema.get("type") == "array":
                warnings.extend(_array_field_warnings(node.id, key, value, field_schema))
            else:
                warnings.extend(_field_warnings(node.id, key, value, field_schema))
    return warnings


def _schema_warning(node_id: str, code: str, message: str) -> dict[str, str]:
    return {"node_id": node_id, "code": code, "message": message}


def _is_numeric(value: Any) -> bool:
    """Mirror ``GraphExecutor._to_num``'s coercion: bool, real number, or a numeric string."""
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            return False
        return True
    return False


def _field_warnings(node_id: str, key: str, value: Any, schema: dict[str, Any]) -> list[dict[str, str]]:
    if value is None:
        return []
    if "enum" in schema:
        if value in schema["enum"]:
            return []
        return [_schema_warning(node_id, "config_schema_enum_invalid", f"{key}: {value!r} is not one of {schema['enum']}.")]
    field_type = schema.get("type")
    if field_type in _NUMERIC_SCHEMA_TYPES:
        if value == "" or _is_numeric(value):
            return []
        return [_schema_warning(node_id, "config_schema_type_mismatch", f"{key}: expected a {field_type}, got {value!r}.")]
    if field_type == "string" and not isinstance(value, str):
        return [_schema_warning(node_id, "config_schema_type_mismatch", f"{key}: expected a string, got {value!r}.")]
    # "boolean" and untyped fields (e.g. decision/value_mapping's context-dependent "value"/"min"/
    # "max") are intentionally not checked further — GraphExecutor._to_bool coerces any value, and
    # an untyped field's legal shape is not declared, so nothing here can be flagged as invalid.
    return []


def _array_field_warnings(node_id: str, key: str, value: Any, schema: dict[str, Any]) -> list[dict[str, str]]:
    items = value
    if isinstance(items, str):
        try:
            items = json.loads(items) if items else []
        except (TypeError, ValueError):
            return [_schema_warning(node_id, "config_schema_malformed_json", f"{key}: could not parse as JSON.")]
    if not isinstance(items, list):
        return [_schema_warning(node_id, "config_schema_type_mismatch", f"{key}: expected an array, got {items!r}.")]

    items_schema = schema.get("items")
    if not isinstance(items_schema, dict):
        return []

    required = items_schema.get("required", [])
    properties = items_schema.get("properties", {})
    warnings: list[dict[str, str]] = []
    for index, item in enumerate(items):
        item_key = f"{key}[{index}]"
        if not isinstance(item, dict):
            warnings.append(_schema_warning(node_id, "config_schema_type_mismatch", f"{item_key}: expected an object, got {item!r}."))
            continue
        for field_name in required:
            field_value = item.get(field_name)
            if field_value is None or field_value == "":
                warnings.append(
                    _schema_warning(node_id, "config_schema_missing_required_field", f"{item_key}.{field_name}: required field is missing."),
                )
        for field_name, field_schema in properties.items():
            if field_name in item:
                warnings.extend(_field_warnings(node_id, f"{item_key}.{field_name}", item[field_name], field_schema))
    return warnings


def _find_cyclic_node_ids(adj: dict[str, list[str]], candidates: set[str]) -> set[str]:
    if not candidates:
        return set()

    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    cyclic: set[str] = set()

    def strongconnect(node_id: str) -> None:
        nonlocal index
        indices[node_id] = index
        lowlinks[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)

        for neighbor in adj.get(node_id, []):
            if neighbor not in candidates:
                continue
            if neighbor not in indices:
                strongconnect(neighbor)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indices[neighbor])

        if lowlinks[node_id] != indices[node_id]:
            return

        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node_id:
                break

        has_self_loop = len(component) == 1 and component[0] in adj.get(component[0], [])
        if len(component) > 1 or has_self_loop:
            cyclic.update(component)

    for node_id in candidates:
        if node_id not in indices:
            strongconnect(node_id)

    return cyclic
