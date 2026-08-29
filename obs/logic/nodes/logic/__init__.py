"""Boolean, comparison and state function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.logic.and_node import NODE_TYPE as AND
from obs.logic.nodes.logic.change_filter import NODE_TYPE as CHANGE_FILTER
from obs.logic.nodes.logic.compare import NODE_TYPE as COMPARE
from obs.logic.nodes.logic.const_value import NODE_TYPE as CONST_VALUE
from obs.logic.nodes.logic.decision import NODE_TYPE as DECISION
from obs.logic.nodes.logic.edge_detect import NODE_TYPE as EDGE_DETECT
from obs.logic.nodes.logic.gate import NODE_TYPE as GATE
from obs.logic.nodes.logic.hysteresis import NODE_TYPE as HYSTERESIS
from obs.logic.nodes.logic.memory import NODE_TYPE as MEMORY
from obs.logic.nodes.logic.merge import NODE_TYPE as MERGE
from obs.logic.nodes.logic.not_node import NODE_TYPE as NOT
from obs.logic.nodes.logic.or_node import NODE_TYPE as OR
from obs.logic.nodes.logic.value_mapping import NODE_TYPE as VALUE_MAPPING
from obs.logic.nodes.logic.xor_node import NODE_TYPE as XOR

NODE_TYPES: tuple[NodeTypeDef, ...] = (
    CONST_VALUE,
    AND,
    OR,
    NOT,
    XOR,
    GATE,
    MEMORY,
    MERGE,
    CHANGE_FILTER,
    EDGE_DETECT,
    COMPARE,
    HYSTERESIS,
    DECISION,
    VALUE_MAPPING,
)

__all__ = ["NODE_TYPES"]
