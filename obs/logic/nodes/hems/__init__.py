"""Home Energy Management (HEMS Lite) function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.hems.surplus import NODE_TYPE as HEMS_SURPLUS

NODE_TYPES: tuple[NodeTypeDef, ...] = (HEMS_SURPLUS,)

__all__ = ["NODE_TYPES"]
