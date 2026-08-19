"""Node definition for the ``xml_extractor`` function block (XML Extractor)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="xml_extractor",
    label="XML Extractor",
    category="integration",
    description="Parst einen XML-String und extrahiert einen oder mehrere Werte anhand von XPath-Ausdrücken (ElementTree-Syntax, z.B. .//temperature). Mehrere Ausgänge konfigurierbar über + im Konfigurations-Panel.",
    inputs=[port("data", "Daten")],
    outputs=[port("value", "Wert")],  # overridden dynamically when xml_paths is set
    config_schema={
        "xml_path": {"type": "string", "default": "", "label": "XPath-Ausdruck (Legacy)"},
        "xml_paths": {"type": "string", "default": "", "label": "Ausgänge (JSON-Array)"},
    },
    color="#0369a1",
)
