"""
Shared fixtures for open bridge server unit tests.
"""
from __future__ import annotations

import pytest

from obs.logic.executor import GraphExecutor
from obs.logic.models import FlowData


# ---------------------------------------------------------------------------
# GraphExecutor helpers
# ---------------------------------------------------------------------------

def make_executor(nodes: list[dict], edges: list[dict] | None = None,
                  hysteresis_state: dict | None = None,
                  app_config: dict | None = None) -> GraphExecutor:
    """Build a GraphExecutor from raw node/edge dicts."""
    flow = FlowData.model_validate({"nodes": nodes, "edges": edges or []})
    return GraphExecutor(flow=flow, hysteresis_state=hysteresis_state,
                         app_config=app_config or {})


def node(node_id: str, node_type: str, data: dict | None = None) -> dict:
    """Shorthand for building a node dict."""
    return {"id": node_id, "type": node_type, "data": data or {}}


def edge(source: str, target: str,
         source_handle: str = "out", target_handle: str = "in") -> dict:
    return {
        "id": f"{source}-{target}",
        "source": source,
        "target": target,
        "sourceHandle": source_handle,
        "targetHandle": target_handle,
    }
