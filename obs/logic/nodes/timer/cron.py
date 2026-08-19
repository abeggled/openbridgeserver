"""Node definition for the ``timer_cron`` function block (Trigger)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="timer_cron",
    label="Trigger",
    category="timer",
    description="Löst automatisch nach einem Cron-Zeitplan aus (Minute Stunde Tag Monat Wochentag).",
    inputs=[],
    outputs=[port("trigger", "Trigger", "trigger")],
    config_schema={"cron": {"type": "string", "default": "0 7 * * *"}},
    color="#b45309",
)
