"""Node definition for the ``astro_sun`` function block (Astro Sonne)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="astro_sun",
    label="Astro Sonne",
    category="astro",
    description="Berechnet Sonnenauf- und -untergang basierend auf Breitengrad/Längengrad. Benötigt: pip install astral",
    inputs=[],
    outputs=[
        port("sunrise", "Aufgang"),
        port("sunset", "Untergang"),
        port("is_day", "Tagsüber", "trigger"),
    ],
    config_schema={
        "latitude": {"type": "number", "default": 47.37, "label": "Breitengrad"},
        "longitude": {"type": "number", "default": 8.54, "label": "Längengrad"},
    },
    color="#d97706",
)
