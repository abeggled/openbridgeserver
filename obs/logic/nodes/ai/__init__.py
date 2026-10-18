"""AI function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.ai.ai_logic import NODE_TYPE as AI_LOGIC

NODE_TYPES: tuple[NodeTypeDef, ...] = (AI_LOGIC,)

__all__ = ["NODE_TYPES"]
