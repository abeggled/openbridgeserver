"""DataPoint read/write function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.datapoint.read import NODE_TYPE as DATAPOINT_READ
from obs.logic.nodes.datapoint.write import NODE_TYPE as DATAPOINT_WRITE

NODE_TYPES: tuple[NodeTypeDef, ...] = (
    DATAPOINT_READ,
    DATAPOINT_WRITE,
)

__all__ = ["NODE_TYPES"]
