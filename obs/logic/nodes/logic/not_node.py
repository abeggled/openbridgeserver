"""Node definition for the ``not`` function block (NOT)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="not",
    label="NOT",
    category="logic",
    description="Invertiert den Eingang",
    inputs=[port("in1", "IN 1")],
    outputs=[port("out", "Out")],
    color="#1d4ed8",
)
