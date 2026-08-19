"""Script execution function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.script.python_script import NODE_TYPE as PYTHON_SCRIPT

NODE_TYPES: tuple[NodeTypeDef, ...] = (PYTHON_SCRIPT,)

__all__ = ["NODE_TYPES"]
