"""Pydantic models for Logic Engine graphs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class NodePosition(BaseModel):
    x: float
    y: float


class LogicNodeData(BaseModel):
    """Node-type-specific configuration stored in the node."""

    label: str = ""
    # type-specific fields — stored as arbitrary dict
    model_config = {"extra": "allow"}


class LogicNode(BaseModel):
    id: str
    type: str  # e.g. "and", "or", "datapoint_read"
    position: NodePosition
    data: dict[str, Any] = {}


class LogicEdge(BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class FlowData(BaseModel):
    nodes: list[LogicNode] = []
    edges: list[LogicEdge] = []


class LogicGraphCreate(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    flow_data: FlowData = FlowData()


class LogicGraphUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    flow_data: FlowData | None = None


class LogicGraphOut(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    flow_data: FlowData
    created_at: str
    updated_at: str


class LogicGraphImport(BaseModel):
    """Import-Payload für einen exportierten Logic Graph."""

    obs_export: str  # muss "logic_graph" sein
    version: int
    name: str
    description: str = ""
    enabled: bool = True
    flow_data: FlowData


class NodeTypePort(BaseModel):
    id: str
    label: str
    type: str = "value"  # "value" | "trigger"


class NodeTypeDef(BaseModel):
    type: str  # unique identifier
    label: str  # display name
    category: str  # "logic" | "datapoint" | "timer" | "math" | "script" | "ai"
    description: str = ""
    inputs: list[NodeTypePort] = []
    outputs: list[NodeTypePort] = []
    config_schema: dict[str, Any] = {}  # JSON schema for node data
    color: str = "#475569"  # default node color (tailwind slate-600)


class LogicUsageOut(BaseModel):
    """Describes a single usage of a DataPoint inside a logic graph."""

    graph_id: str
    graph_name: str
    graph_enabled: bool
    node_id: str
    node_type: str  # "datapoint_read" | "datapoint_write"
    direction: str  # "SOURCE" | "DEST" — from the DataPoint's perspective
