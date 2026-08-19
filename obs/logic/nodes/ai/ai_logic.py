"""Node definition for the ``ai_logic`` function block (AI Logic)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef

NODE_TYPE = NodeTypeDef(
    type="ai_logic",
    label="AI Logic",
    category="ai",
    description="",
    inputs=[],
    outputs=[],
    config_schema={},
    color="#7c3aed",
)
