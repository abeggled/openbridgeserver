"""Node definition for the ``substring_extractor`` function block (Substring / RegEx)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="substring_extractor",
    label="Substring / RegEx",
    category="integration",
    description="Extrahiert Text aus einem String per Substring-Operation oder regulärem Ausdruck. Modi: links_von / rechts_von (erstes oder letztes Vorkommen), zwischen (zwei Markierungen), ausschneiden (Position + Länge), regex (Python re-Syntax, Gruppen wählbar).",
    inputs=[port("data", "Daten")],
    outputs=[port("value", "Wert")],
    config_schema={
        "mode": {
            "type": "string",
            "enum": ["links_von", "rechts_von", "zwischen", "ausschneiden", "regex"],
            "default": "rechts_von",
            "label": "Modus",
        },
        "search": {"type": "string", "default": "", "label": "Suchbegriff (links_von / rechts_von)"},
        "occurrence": {"type": "string", "enum": ["first", "last"], "default": "first", "label": "Vorkommen (erstes / letztes)"},
        "start_marker": {"type": "string", "default": "", "label": "Start-Markierung (zwischen)"},
        "end_marker": {"type": "string", "default": "", "label": "End-Markierung (zwischen)"},
        "start": {"type": "number", "default": 0, "label": "Startposition (ausschneiden, 0-basiert)"},
        "length": {"type": "number", "default": -1, "label": "Länge (ausschneiden, -1 = bis Ende)"},
        "pattern": {"type": "string", "default": "", "label": "RegEx-Muster"},
        "flags": {"type": "string", "default": "", "label": "Flags (z.B. i für case-insensitive)"},
        "group": {"type": "number", "default": 0, "label": "Capture-Gruppe (0 = gesamter Treffer)"},
    },
    color="#0369a1",
)
