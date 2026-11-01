"""Pydantic models for Logic Engine graphs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, model_validator

ControlClassName = Literal["room_local", "central_plant"]


class NodePosition(BaseModel):
    x: float
    y: float


class LogicNodeData(BaseModel):
    """Node-type-specific configuration stored in the node."""

    label: str = ""
    # type-specific fields — stored as arbitrary dict
    model_config = {"extra": "allow"}


# The timer blocks name their duration differently: (key read, sibling's key).
_TIMER_DURATION_KEYS = {
    "timer_delay": ("delay_s", "duration_s"),
    "timer_pulse": ("duration_s", "delay_s"),
}


class LogicNode(BaseModel):
    id: str
    type: str  # e.g. "and", "or", "datapoint_read"
    position: NodePosition
    data: dict[str, Any] = {}

    @model_validator(mode="after")
    def _adopt_sibling_timer_duration(self) -> LogicNode:
        """Move a timer duration saved under the sibling block's key to the one this block reads.

        A Delay saved with only ``duration_s`` (the Pulse's key) used to run with
        the 1 s default (#1377). Every parse — save, import, load, read — moves
        it, so sheets stored that way take their configured duration on load.
        """
        keys = _TIMER_DURATION_KEYS.get(self.type)
        if keys is None:
            return self
        own, sibling = keys
        if sibling in self.data and self.data.get(own) in (None, ""):
            data = dict(self.data)
            value = data.pop(sibling)
            if value not in (None, ""):
                data[own] = value
            self.data = data
        return self


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
    control_class: ControlClassName = "room_local"


class LogicGraphUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    flow_data: FlowData | None = None
    control_class: ControlClassName | None = None


class LogicGraphRun(BaseModel):
    """Ephemeral values used for one interactive debug execution."""

    input_overrides: dict[str, dict[str, Any]] = {}
    debug: bool = False


class LogicGraphDuplicate(BaseModel):
    """Optional override for the duplicate's name — falls back to the ``"Kopie von {name}"``
    default when omitted or blank."""

    name: str | None = None


class LogicGraphOut(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    flow_data: FlowData
    created_at: str
    updated_at: str
    control_class: ControlClassName = "room_local"


class LogicGraphImport(BaseModel):
    """Import-Payload für einen exportierten Logic Graph."""

    obs_export: str  # muss "logic_graph" sein
    version: int
    name: str
    description: str = ""
    enabled: bool = True
    flow_data: FlowData
    control_class: ControlClassName = "room_local"


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
    has_external_side_effect: bool | None = None
    required_capability: str | None = None
    hidden_from_palette: bool = False
    legacy: bool = False
    # Heading anchor id in help/{en,de}/logic/blocks-<category>.md, resolvable at
    # runtime via GET /help/help-index.json -> .helpIds[help_id].{en,de}.
    help_id: str | None = None


class LogicRunPreflightCheck(BaseModel):
    target_type: str
    target_id: str
    node_ids: list[str] = []
    allowed: bool
    reason: str


class LogicRunPreflight(BaseModel):
    graph_id: str
    allowed: bool
    checks: list[LogicRunPreflightCheck]


class LogicUsageOut(BaseModel):
    """Describes a single usage of a DataPoint inside a logic graph."""

    graph_id: str
    graph_name: str
    graph_enabled: bool
    node_id: str
    node_type: str  # "datapoint_read" | "datapoint_write"
    direction: str  # "SOURCE" | "DEST" — from the DataPoint's perspective
