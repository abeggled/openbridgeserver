"""Graph topology analysis for the logic engine."""

from __future__ import annotations

from dataclasses import dataclass

from obs.logic.models import FlowData, LogicNode

TICK_BOUNDARY_NODE_TYPES = {"memory"}


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
