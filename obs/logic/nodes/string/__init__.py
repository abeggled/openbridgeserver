"""Text and annotation function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.string.comment import NODE_TYPE as COMMENT
from obs.logic.nodes.string.concat import NODE_TYPE as STRING_CONCAT
from obs.logic.nodes.string.replace import NODE_TYPE as STRING_REPLACE

NODE_TYPES: tuple[NodeTypeDef, ...] = (
    COMMENT,
    STRING_CONCAT,
    STRING_REPLACE,
)

__all__ = ["NODE_TYPES"]
