"""Time, schedule and sequence function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.timer.cron import NODE_TYPE as TIMER_CRON
from obs.logic.nodes.timer.datetime_node import NODE_TYPE as DATETIME
from obs.logic.nodes.timer.delay import NODE_TYPE as TIMER_DELAY
from obs.logic.nodes.timer.operating_hours import NODE_TYPE as OPERATING_HOURS
from obs.logic.nodes.timer.pulse import NODE_TYPE as TIMER_PULSE
from obs.logic.nodes.timer.value_sequence import NODE_TYPE as VALUE_SEQUENCE

NODE_TYPES: tuple[NodeTypeDef, ...] = (
    TIMER_DELAY,
    TIMER_PULSE,
    VALUE_SEQUENCE,
    TIMER_CRON,
    DATETIME,
    OPERATING_HOURS,
)

__all__ = ["NODE_TYPES"]
