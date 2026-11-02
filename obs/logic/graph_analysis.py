"""Graph topology analysis for the logic engine."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from typing import Any

from obs.logic.models import FlowData, LogicNode
from obs.logic.registry import get_node_type

# Nodes whose output does not depend on their inputs within a run: memory
# outputs last tick's value; a delay or pulse outputs what its timer (owned by
# the LogicManager) decided. Edges into them do not order the run, so a
# feedback loop through one is no cycle.
# The blocks whose timing the LogicManager owns (issue #1166).
TIMER_NODE_TYPES = frozenset({"timer_delay", "timer_pulse"})
TICK_BOUNDARY_NODE_TYPES = {"memory", *TIMER_NODE_TYPES}

# Import-time placeholder for an unresolved node type (obs/api/v1/logic.py) — an
# already-known, intentional marker, not itself an "unknown" type to re-flag.
_MISSING_NODE_TYPE = "missing_node"

_NUMERIC_SCHEMA_TYPES = {"number", "integer"}

# `data` keys that are legitimately absent from a node type's `config_schema` and therefore must
# not be reported as `unknown_config_field`. Every entry is written by the Admin GUI or read by the
# runtime; a key outside these lists and the schema is most likely a typo that silently falls back
# to the schema default (e.g. `{"operater": ">"}` on `compare`).
# - `label`: the user-defined block name every block type carries (issue #1157).
_GENERIC_EXTRA_CONFIG_KEYS: frozenset[str] = frozenset({"label"})
_TYPE_EXTRA_CONFIG_KEYS: dict[str, frozenset[str]] = {
    # Value-map preset picked in the properties panel, applied by GraphExecutor.
    "datapoint_read": frozenset({"value_map"}),
    "datapoint_write": frozenset({"value_map"}),
    # The names `_migrate_legacy_api_client_field_names` (obs/logic/manager.py) moves the schema's
    # older `headers_secret_file`/`auth_token_file` to; graphs saved since then carry these.
    "api_client": frozenset({"headers_value_file", "auth_value_file"}),
    # Legacy thresholds GraphExecutor still falls back to; checked by `_heating_legacy_warnings`.
    "heating_circuit": frozenset({"temp_winter", "temp_summer"}),
}
_HEATING_LEGACY_KEYS = ("temp_winter", "temp_summer")
# Numbered keys, bounded by the runtime's own slot range rather than the node's current count: the
# editor keeps a slot's key when the count is lowered, and the runtime ignores it, so such a key is
# harmless — but slot 0 or one beyond the maximum can never have come from the editor.
_GATE_NEGATE_INPUT = re.compile(r"negate_in(?:[1-9]|[12]\d|30)")  # in1..in30, input_count ≤ 30
_TYPE_EXTRA_CONFIG_KEY_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    # Per-port negation toggled on the block card (GenericNode.vue): inputs of the gates and
    # binary_stats, plus the output of the gates only.
    "and": (_GATE_NEGATE_INPUT, re.compile(r"negate_out")),
    "or": (_GATE_NEGATE_INPUT, re.compile(r"negate_out")),
    "xor": (_GATE_NEGATE_INPUT, re.compile(r"negate_out")),
    "binary_stats": (_GATE_NEGATE_INPUT,),
    # Static text for an unwired input `in_<n>`, n in 1..20 (string_concat's count range).
    "string_concat": (re.compile(r"text_(?:[1-9]|1\d|20)"),),
}

# Some enum-declared config fields accept additional runtime spellings beyond the schema's
# canonical `enum` values — genuine synonyms GraphExecutor treats identically to a canonical
# value, not typos that silently fall back to a different (wrong) default. Duplicated here
# rather than imported from executor.py — executor.py already imports from this module, so the
# reverse import would be circular. Kept honest by
# tests/unit/logic/test_config_schema_warnings.py, which recomputes these unions from executor.py's
# actual op-sets, mirroring the `_DURATION_FIELDS` precedent in obs/logic/validation.py.
_COMPARE_OPERATOR_ALIASES: frozenset[str] = frozenset({">", "gt", "<", "lt", "=", "==", "eq", ">=", "gte", "<=", "lte", "!=", "ne"})
_CONDITION_OPERATOR_ALIASES: frozenset[str] = frozenset(
    {
        "range",
        "between",
        "regex",
        "regexp",
        "contains",
        "starts_with",
        "startswith",
        "begins_with",
        "ends_with",
        "endswith",
        "text",
        "text_eq",
        "equals_text",
        "=",
        "==",
        "eq",
        "!=",
        "ne",
        ">",
        "gt",
        "<",
        "lt",
        ">=",
        "gte",
        "<=",
        "lte",
    }
)

# Keyed by the schema's own canonical enum values, so both `compare` and the shared
# decision/value_mapping operator schema resolve to their respective alias supersets.
_ENUM_ALIAS_SUPERSETS: dict[frozenset[str], frozenset[str]] = {
    frozenset({">", "<", "=", ">=", "<=", "!="}): _COMPARE_OPERATOR_ALIASES,
    frozenset(
        {"eq", "ne", "gt", "lt", "gte", "lte", "range", "text_eq", "contains", "starts_with", "ends_with", "regex"}
    ): _CONDITION_OPERATOR_ALIASES,
}


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
                    "message": (
                        "Graph cycle detected; node cannot be executed without a memory, delay or pulse node. "
                        f"Cycle nodes: {', '.join(ordered_cyclic[:5])}"
                    ),
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

    A key present in ``data`` but declared neither by the schema nor as a known extra key
    (``_GENERIC_EXTRA_CONFIG_KEYS`` and friends) is reported as ``unknown_config_field`` — the
    block would ignore it and silently use the default. heating_circuit's legacy thresholds are
    reported as ``ignored_config_field`` where the runtime does not read them. An import placeholder (``missing_node``)
    is reported once as ``missing_node_type`` with the type it stands in for, since it does
    nothing at run time; its ``data`` is not checked further.
    """
    warnings: list[dict[str, str]] = []
    for node in flow.nodes:
        if node.type == _MISSING_NODE_TYPE:
            warnings.append(_missing_node_warning(node))
            continue
        node_type = get_node_type(node.type)
        if node_type is None:
            warnings.append(_schema_warning(node.id, "unknown_node_type", f"Unknown node type '{node.type}'."))
            continue
        for key in node.data:
            if key not in node_type.config_schema and not _is_known_extra_config_key(node.type, key):
                warnings.append(_unknown_config_field_warning(node.id, node.type, key, node_type.config_schema))
        if node.type == "heating_circuit":
            warnings.extend(_heating_legacy_warnings(node))
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


def _missing_node_warning(node: LogicNode) -> dict[str, str]:
    # Same resolution as the read path (`_normalize_missing_node_placeholders` in
    # obs/api/v1/logic.py): a placeholder without `original_type` carries its type in `label`.
    original_type = node.data.get("original_type") or node.data.get("label")
    if isinstance(original_type, str) and original_type.strip():
        subject = f"node type '{original_type.strip()}'"
    else:
        subject = "an unknown node type"
    return _schema_warning(
        node.id,
        "missing_node_type",
        f"Placeholder for {subject} that is not available on this server; the block does nothing at run time.",
    )


def _is_known_extra_config_key(node_type: str, key: str) -> bool:
    if key in _GENERIC_EXTRA_CONFIG_KEYS or key in _TYPE_EXTRA_CONFIG_KEYS.get(node_type, frozenset()):
        return True
    return any(pattern.fullmatch(key) for pattern in _TYPE_EXTRA_CONFIG_KEY_PATTERNS.get(node_type, ()))


def _heating_legacy_warnings(node: LogicNode) -> list[dict[str, str]]:
    """Mirror GraphExecutor's heating_circuit fallback to the legacy thresholds.

    They apply only while ``threshold_temp`` is absent and ``temp_winter`` is set (``temp_summer``
    alone is never read), and are then converted with ``float()`` — no empty-string leniency.
    """
    data = node.data
    present = [key for key in _HEATING_LEGACY_KEYS if data.get(key) is not None]
    if "threshold_temp" in data:
        return [_ignored_field_warning(node.id, key, "threshold_temp is set") for key in present]
    if data.get("temp_winter") is None:
        return [_ignored_field_warning(node.id, key, "temp_winter is not set") for key in present]
    return [
        _schema_warning(node.id, "config_schema_type_mismatch", f"{key}: expected a number, got {data[key]!r}.")
        for key in present
        if data[key] == "" or not _is_numeric(data[key])
    ]


def _ignored_field_warning(node_id: str, key: str, reason: str) -> dict[str, str]:
    return _schema_warning(node_id, "ignored_config_field", f"{key}: legacy field, ignored because {reason}.")


def _unknown_config_field_warning(node_id: str, node_type: str, key: str, config_schema: dict[str, Any]) -> dict[str, str]:
    message = f"{key}: not a config field of this node type; it is ignored and the default is used."
    candidates = [*config_schema, *_GENERIC_EXTRA_CONFIG_KEYS, *sorted(_TYPE_EXTRA_CONFIG_KEYS.get(node_type, frozenset()))]
    suggestions = difflib.get_close_matches(key, candidates, n=1)
    if suggestions:
        message += f" Did you mean '{suggestions[0]}'?"
    return _schema_warning(node_id, "unknown_config_field", message)


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


def _matches_enum(value: Any, enum_values: list[Any]) -> bool:
    """True if ``value`` is accepted for a field declaring ``enum_values``.

    Beyond an exact match: many enum-style config fields are read case/whitespace-normalized at
    execution time (e.g. `GraphExecutor` lowers `compare.operator`/`string_replace.rules[].mode`,
    `LogicManager` uppercases `api_client.method`) — checked here as a generic case-insensitive
    comparison against the canonical values. A handful of fields additionally accept genuine
    synonyms beyond their canonical spelling (`_ENUM_ALIAS_SUPERSETS`), which is checked instead of
    the canonical set when the field has an entry there.
    """
    if value in enum_values:
        return True
    if not isinstance(value, str):
        return False
    candidates = _ENUM_ALIAS_SUPERSETS.get(frozenset(enum_values), enum_values)
    normalized = value.strip().lower()
    return any(isinstance(candidate, str) and candidate.strip().lower() == normalized for candidate in candidates)


def _field_warnings(node_id: str, key: str, value: Any, schema: dict[str, Any]) -> list[dict[str, str]]:
    if value is None:
        return []
    if "enum" in schema:
        if _matches_enum(value, schema["enum"]):
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
