"""Sun position function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.astro.sun import NODE_TYPE as ASTRO_SUN

NODE_TYPES: tuple[NodeTypeDef, ...] = (ASTRO_SUN,)

__all__ = ["NODE_TYPES"]
