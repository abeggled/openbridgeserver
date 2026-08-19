"""Node definition for the ``math_formula`` function block (Formel)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="math_formula",
    label="Formel",
    category="math",
    description="Berechnet einen Ausdruck. Variablen: a (= IN 1), b (= IN 2)",
    inputs=[port("in1", "IN 1"), port("in2", "IN 2")],
    outputs=[port("result", "Ergebnis")],
    config_schema={
        "formula": {"type": "string", "default": "a + b"},
        "output_formula": {"type": "string", "default": ""},
    },
    color="#7c3aed",
)
